from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from threading import Lock, RLock
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
KB_PATH = BASE_DIR / "knowledge_base" / "agriculture.json"
CHROMA_PATH = BASE_DIR / "chroma_db"
KB_HASH_PATH = CHROMA_PATH / "kb.sha256"  # remembers which KB version is indexed

MODEL_NAME = "intfloat/multilingual-e5-small"
COLLECTION_NAME = "agriculture_knowledge"

# E5 models need these prefixes at embedding time (not in the stored text).
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "

EMBED_BATCH_SIZE = 32
DB_BATCH_SIZE = 500
MAX_TOP_K = 20
MAX_QUERY_CHARS = 1000
OVERFETCH_FACTOR = 3  # fetch extra hits so de-duplication still fills top_k
KB_LANGUAGES = ("en", "hi", "kn")


class AgricultureVectorStore:
    """Vector storage and semantic search using ChromaDB and multilingual E5 embeddings."""

    def __init__(self) -> None:
        self._model: SentenceTransformer | None = None
        self._client: Any = None
        self._collection: Any = None
        self._model_lock = Lock()
        self._collection_lock = Lock()
        self._index_lock = RLock()  # only one (re)index at a time

    # ------------------------------------------------------------------ lazy loaders

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    logger.info("Loading SentenceTransformer model '%s'...", MODEL_NAME)
                    self._model = SentenceTransformer(MODEL_NAME)
        return self._model

    @property
    def collection(self):
        if self._collection is None:
            with self._collection_lock:
                if self._collection is None:
                    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
                    logger.info("Initializing ChromaDB at %s", CHROMA_PATH)
                    self._client = chromadb.PersistentClient(path=str(CHROMA_PATH))
                    self._collection = self._client.get_or_create_collection(
                        name=COLLECTION_NAME,
                        metadata={"hnsw:space": "cosine"},
                    )
        return self._collection

    def warmup(self) -> None:
        """Load model and DB at startup so the first query isn't slow."""
        _ = self.model
        _ = self.collection

    # ------------------------------------------------------------------ indexing

    @staticmethod
    def _kb_hash() -> str:
        return hashlib.sha256(KB_PATH.read_bytes()).hexdigest()

    @staticmethod
    def _load_records() -> list[Any]:
        if not KB_PATH.exists():
            raise FileNotFoundError(f"Knowledge base file not found at: {KB_PATH}")
        try:
            with open(KB_PATH, "r", encoding="utf-8") as file:
                records = json.load(file)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in knowledge base file: {exc}") from exc
        if not isinstance(records, list):
            raise ValueError("Knowledge base must be a JSON list of records.")
        return records

    @staticmethod
    def _build_entries(
        records: list[Any],
    ) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        seen: set[str] = set()

        for record in records:
            if not isinstance(record, dict) or "id" not in record:
                continue

            record_id = str(record["id"])
            translations = record.get("translations") or {}
            texts = {
                "en": record.get("content", ""),
                "hi": translations.get("hi", ""),
                "kn": translations.get("kn", ""),
            }

            for language in KB_LANGUAGES:
                content = str(texts[language] or "").strip()
                if not content:
                    continue

                doc_id = f"{record_id}_{language}"
                if doc_id in seen:
                    logger.warning("Duplicate id '%s' in knowledge base; keeping first.", doc_id)
                    continue
                seen.add(doc_id)

                ids.append(doc_id)
                documents.append(content)  # raw text; prefix is added only when embedding
                metadatas.append({
                    "record_id": record_id,
                    "language": language,
                    "crop": str(record.get("crop", "")),
                    "topic": str(record.get("topic", "")),
                    "source_name": str(record.get("source_name", "")),
                    "source_url": str(record.get("source_url", "")),
                    "verified": str(record.get("verified", False)),
                    "scope_note": str(record.get("scope_note", "")),
                })
        return ids, documents, metadatas

    def index_knowledge_base(self) -> dict[str, Any]:
        """(Re)index the knowledge base without ever leaving the collection empty.

        Embeddings are computed first, then upserted, then stale entries are
        deleted. Searches during a reindex keep seeing valid data, and a failure
        midway can't wipe the existing index.
        """
        with self._index_lock:
            records = self._load_records()
            ids, documents, metadatas = self._build_entries(records)
            kb_hash = self._kb_hash()
            collection = self.collection
            existing_ids = set(collection.get(include=[])["ids"])

            if not documents:
                logger.warning("No valid documents found in knowledge base.")
                for start in range(0, len(existing_ids), DB_BATCH_SIZE):
                    collection.delete(ids=list(existing_ids)[start:start + DB_BATCH_SIZE])
                return {
                    "knowledge_records": len(records),
                    "indexed_documents": 0,
                    "collection_count": collection.count(),
                }

            logger.info("Encoding %d passages with %s...", len(documents), MODEL_NAME)
            embeddings = self.model.encode(
                [PASSAGE_PREFIX + d for d in documents],
                normalize_embeddings=True,
                batch_size=EMBED_BATCH_SIZE,
                show_progress_bar=False,
            )

            for start in range(0, len(ids), DB_BATCH_SIZE):
                end = start + DB_BATCH_SIZE
                collection.upsert(
                    ids=ids[start:end],
                    documents=documents[start:end],
                    metadatas=metadatas[start:end],
                    embeddings=embeddings[start:end].tolist(),
                )

            stale = list(existing_ids - set(ids))
            for start in range(0, len(stale), DB_BATCH_SIZE):
                collection.delete(ids=stale[start:start + DB_BATCH_SIZE])
            if stale:
                logger.info("Removed %d stale documents.", len(stale))

            KB_HASH_PATH.write_text(kb_hash, encoding="utf-8")

            count = collection.count()
            logger.info("Indexed %d documents into '%s'.", count, COLLECTION_NAME)
            return {
                "knowledge_records": len(records),
                "indexed_documents": len(ids),
                "collection_count": count,
            }

    def ensure_indexed(self) -> dict[str, Any] | None:
        """Index only if the collection is empty or the KB file changed since last index.

        Call this at startup. Returns index stats if it indexed, else None.
        """
        with self._index_lock:
            if self.collection.count() > 0 and KB_PATH.exists():
                try:
                    indexed_hash = KB_HASH_PATH.read_text(encoding="utf-8").strip()
                except FileNotFoundError:
                    indexed_hash = ""
                if indexed_hash == self._kb_hash():
                    logger.info("Knowledge base index is up to date.")
                    return None
            logger.info("Knowledge base is new or changed; indexing...")
            return self.index_knowledge_base()

    # ------------------------------------------------------------------ search

    @staticmethod
    def _build_where(language: str | None, crop: str | None) -> dict[str, Any] | None:
        conditions: list[dict[str, Any]] = []
        if language:
            conditions.append({"language": language})
        if crop:
            conditions.append({"crop": crop})
        if not conditions:
            return None
        return conditions[0] if len(conditions) == 1 else {"$and": conditions}

    def search(
        self,
        query: str,
        top_k: int = 3,
        *,
        language: str | None = None,
        crop: str | None = None,
        max_distance: float | None = None,
        dedupe: bool = True,
    ) -> list[dict[str, Any]]:
        """Semantic search.

        language / crop: optional metadata filters.
        max_distance:    drop weak matches (cosine distance; lower = more similar).
        dedupe:          return at most one hit per knowledge record, since each
                         record is indexed in en/hi/kn and would otherwise
                         fill top_k with translations of the same fact.
        """
        if not isinstance(query, str) or not query.strip():
            return []

        query = query.strip()[:MAX_QUERY_CHARS]
        top_k = max(1, min(int(top_k), MAX_TOP_K))

        try:
            count = self.collection.count()
            if count == 0:
                logger.warning("Vector collection is empty. Run indexing first.")
                return []

            fetch = top_k * OVERFETCH_FACTOR if dedupe else top_k
            query_embedding = self.model.encode(
                [QUERY_PREFIX + query],
                normalize_embeddings=True,
                show_progress_bar=False,
            )

            kwargs: dict[str, Any] = {
                "query_embeddings": query_embedding.tolist(),
                "n_results": max(1, min(fetch, count)),
                "include": ["documents", "metadatas", "distances"],
            }
            where = self._build_where(language, crop)
            if where is not None:
                kwargs["where"] = where

            results = self.collection.query(**kwargs)
            if not results or not results.get("ids") or not results["ids"][0]:
                return []

            matches: list[dict[str, Any]] = []
            seen_records: set[str] = set()

            for i, result_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i]
                if max_distance is not None and distance > max_distance:
                    continue

                metadata = results["metadatas"][0][i] or {}
                record_id = metadata.get("record_id", result_id)
                if dedupe and record_id in seen_records:
                    continue
                seen_records.add(record_id)

                document = results["documents"][0][i]
                if document.startswith(PASSAGE_PREFIX):  # indexes built by the old version
                    document = document[len(PASSAGE_PREFIX):]

                matches.append({
                    "id": result_id,
                    "document": document,
                    "metadata": metadata,
                    "distance": distance,
                    "score": round(1.0 - distance, 4),  # cosine similarity
                })
                if len(matches) >= top_k:
                    break

            return matches
        except Exception as exc:
            logger.error("Vector similarity search failed: %s", exc, exc_info=True)
            return []