
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from services.vector_store import (
    AgricultureVectorStore,
    PASSAGE_PREFIX,
    EMBED_BATCH_SIZE,
    DB_BATCH_SIZE,
    CHROMA_PATH,
)

logger = logging.getLogger(__name__)

PDF_COLLECTION_NAME = "icar_kharif_2025"

PDF_CHUNKS_PATH = (
    Path(__file__).resolve().parent.parent
    / "knowledge_base"
    / "extracted"
    / "icar_kharif_2025_chunks_day1_validated.json"
)


class PDFVectorStore:
    """Persistent vector store for ICAR PDF chunks.

    Uses the existing multilingual E5 model and ChromaDB client.
    Does not modify or delete the existing agriculture_knowledge collection.
    """

    def __init__(self) -> None:
        self.base_store = AgricultureVectorStore()

        # Initialize the existing ChromaDB client.
        _ = self.base_store.collection

        self.client = self.base_store._client
        self.model = self.base_store.model

        self.collection = self.client.get_or_create_collection(
            name=PDF_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _metadata(chunk: dict[str, Any]) -> dict[str, Any]:
        """Convert chunk metadata into ChromaDB-supported scalar values."""

        def as_string_list(value: Any) -> str:
            if isinstance(value, (list, tuple, set)):
                return ", ".join(str(item) for item in value)
            return str(value or "")

        flags = chunk.get("flags") or {}

        return {
            "chunk_id": str(chunk.get("id", "")),
            "source_name": str(chunk.get("source", "")),
            "source_file": str(chunk.get("source_file", "")),
            "language": str(chunk.get("language", "en")),
            "state": str(chunk.get("state") or ""),
            "states": as_string_list(chunk.get("states")),
            "zone": str(chunk.get("zone") or ""),
            "category": str(chunk.get("category") or ""),
            "crop": str(chunk.get("crop") or ""),
            "crops": as_string_list(chunk.get("crops")),
            "topic": str(chunk.get("topic") or ""),
            "topics": as_string_list(chunk.get("topics")),
            "context": str(chunk.get("context") or ""),
            "doc_type": str(chunk.get("doc_type") or ""),
            "season": str(chunk.get("season") or ""),
            "year": int(chunk.get("year") or 0),
            "page_start": int(chunk.get("page_start") or 0),
            "page_end": int(chunk.get("page_end") or 0),
            "section_id": str(chunk.get("section_id") or ""),
            "verified": str(chunk.get("verified", False)),
            "review_status": str(
                chunk.get("review_status", "pending")
            ),
            "flags": json.dumps(flags, ensure_ascii=False),
        }

    def load_chunks(
        self,
        path: Path = PDF_CHUNKS_PATH,
    ) -> list[dict[str, Any]]:
        """Load and validate the prepared PDF chunks."""

        if not path.exists():
            raise FileNotFoundError(
                f"PDF chunks file not found: {path}"
            )

        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        chunks = data.get("chunks") if isinstance(data, dict) else None

        if not isinstance(chunks, list) or not chunks:
            raise ValueError("PDF chunks JSON must contain a non-empty chunks list.")

        valid_chunks = []
        seen_ids = set()

        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue

            chunk_id = str(chunk.get("id") or "").strip()
            text = str(chunk.get("text") or "").strip()

            if not chunk_id or not text:
                continue

            if chunk_id in seen_ids:
                raise ValueError(f"Duplicate PDF chunk ID: {chunk_id}")

            seen_ids.add(chunk_id)
            valid_chunks.append(chunk)

        if not valid_chunks:
            raise ValueError("No valid PDF chunks found.")

        return valid_chunks

    def index_pdf(self) -> dict[str, Any]:
        """Index PDF chunks using safe, repeatable upserts.

        Existing collection records are never deleted.
        """

        chunks = self.load_chunks()

        ids = [str(chunk["id"]) for chunk in chunks]
        documents = [str(chunk["text"]).strip() for chunk in chunks]
        metadatas = [self._metadata(chunk) for chunk in chunks]

        existing_count = self.collection.count()

        indexed_now = 0

        for start in range(0, len(ids), DB_BATCH_SIZE):
            end = min(start + DB_BATCH_SIZE, len(ids))

            batch_documents = documents[start:end]

            embeddings = self.model.encode(
                [
                    PASSAGE_PREFIX + document
                    for document in batch_documents
                ],
                normalize_embeddings=True,
                batch_size=EMBED_BATCH_SIZE,
                show_progress_bar=False,
            )

            self.collection.upsert(
                ids=ids[start:end],
                documents=batch_documents,
                metadatas=metadatas[start:end],
                embeddings=embeddings.tolist(),
            )

            indexed_now += len(batch_documents)

            logger.info(
                "Indexed PDF chunks: %d/%d",
                indexed_now,
                len(ids),
            )

        final_count = self.collection.count()

        return {
            "collection": PDF_COLLECTION_NAME,
            "chunks_processed": len(chunks),
            "chunks_upserted": indexed_now,
            "count_before": existing_count,
            "count_after": final_count,
            "verified_chunks": sum(
                str(chunk.get("verified", False)).lower() == "true"
                for chunk in chunks
            ),
            "pending_review": sum(
                chunk.get("review_status") == "pending"
                for chunk in chunks
            ),
            "needs_review": sum(
                chunk.get("review_status") == "needs_review"
                for chunk in chunks
            ),
        }

    def search(
        self,
        query: str,
        top_k: int = 5,
        language: str | None = None,
        crop: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search PDF chunks. Results retain verification metadata."""

        if not isinstance(query, str) or not query.strip():
            return []

        top_k = max(1, min(int(top_k), 20))

        if self.collection.count() == 0:
            return []

        query_embedding = self.model.encode(
            ["query: " + query.strip()[:1000]],
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        conditions = []

        if language:
            conditions.append({"language": language})

        if crop:
            conditions.append({"crop": crop})

        if state:
            conditions.append({"state": state})

        where = None
        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        kwargs = {
            "query_embeddings": query_embedding.tolist(),
            "n_results": min(top_k, self.collection.count()),
            "include": ["documents", "metadatas", "distances"],
        }

        if where:
            kwargs["where"] = where

        results = self.collection.query(**kwargs)

        matches = []

        for index, chunk_id in enumerate(results["ids"][0]):
            matches.append({
                "id": chunk_id,
                "document": results["documents"][0][index],
                "metadata": results["metadatas"][0][index] or {},
                "distance": results["distances"][0][index],
            })

        return matches
