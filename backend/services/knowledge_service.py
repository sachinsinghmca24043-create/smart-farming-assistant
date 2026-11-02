from __future__ import annotations

import json
import logging
import re
import time
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

KNOWLEDGE_FILE = (
    Path(__file__).resolve().parent.parent
    / "knowledge_base"
    / "agriculture.json"
)

# Minimum query relevance (keyword hits x2 + shared words) for a keyword match.
MIN_RELEVANCE = 1
# After the RAG service fails to start, wait this long before trying again.
RAG_RETRY_SECONDS = 300

_STOPWORDS = {
    "the", "and", "for", "how", "what", "when", "why", "which", "with",
    "can", "should", "does", "are", "was", "this", "that", "from", "you",
    "your", "have", "has", "about", "into", "any", "get",
}

# Whitespace/punctuation split. We avoid \w because it breaks Hindi/Kannada
# words at combining vowel signs.
_TOKEN_SPLIT = re.compile(r"[\s.,;:!?\"'()\[\]{}\-/।]+")

_rag_service = None
_rag_failed_at: float | None = None
_rag_lock = Lock()
_pdf_store = None
_pdf_failed_at: float | None = None
_pdf_lock = Lock()


# --------------------------------------------------------------------------
# Knowledge base loading
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_knowledge() -> list[dict[str, Any]]:
    """Load and cache agricultural records from the JSON knowledge base."""
    if not KNOWLEDGE_FILE.exists():
        raise FileNotFoundError(
            f"Agricultural knowledge file not found: {KNOWLEDGE_FILE}"
        )

    try:
        with KNOWLEDGE_FILE.open("r", encoding="utf-8") as file:
            records = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Knowledge base is not valid JSON ({KNOWLEDGE_FILE}): {exc}"
        ) from exc

    if not isinstance(records, list):
        raise ValueError("The knowledge base must contain a JSON list.")

    valid = [record for record in records if isinstance(record, dict)]
    skipped = len(records) - len(valid)
    if skipped:
        logger.warning("Skipped %d non-object records in knowledge base.", skipped)

    return valid


@lru_cache(maxsize=1)
def _records_by_id() -> dict[str, dict[str, Any]]:
    """Build the id lookup once instead of on every search."""
    lookup: dict[str, dict[str, Any]] = {}

    for record in load_knowledge():
        if "id" not in record:
            logger.warning("Knowledge record without an 'id' (topic=%s).",
                           record.get("topic"))
            continue

        record_id = str(record["id"])
        if record_id in lookup:
            logger.warning("Duplicate knowledge record id: %s", record_id)
        lookup[record_id] = record

    return lookup


def reload_knowledge() -> None:
    """Drop cached data so the next search re-reads agriculture.json."""
    load_knowledge.cache_clear()
    _records_by_id.cache_clear()


# --------------------------------------------------------------------------
# RAG service (lazy, thread-safe, retries after failures)
# --------------------------------------------------------------------------
def _get_rag_service():
    """Create the RAG service on first use.

    The import is lazy so a missing LangChain/vector-store dependency does not
    stop this module from importing; keyword search still works without it.
    """
    global _rag_service, _rag_failed_at

    if _rag_service is not None:
        return _rag_service

    with _rag_lock:
        if _rag_service is not None:
            return _rag_service

        if (
            _rag_failed_at is not None
            and time.monotonic() - _rag_failed_at < RAG_RETRY_SECONDS
        ):
            raise RuntimeError("RAG service unavailable (recent startup failure).")

        try:
            from services.rag_service import AgricultureRAGService

            _rag_service = AgricultureRAGService()
        except Exception:
            _rag_failed_at = time.monotonic()
            raise

        _rag_failed_at = None
        return _rag_service



def _get_pdf_store():
    """Initialize the ICAR PDF vector store lazily and safely."""
    global _pdf_store, _pdf_failed_at

    if _pdf_store is not None:
        return _pdf_store

    with _pdf_lock:
        if _pdf_store is not None:
            return _pdf_store

        if (
            _pdf_failed_at is not None
            and time.monotonic() - _pdf_failed_at < RAG_RETRY_SECONDS
        ):
            raise RuntimeError("PDF vector store unavailable after recent failure.")

        try:
            from services.pdf_vector_store import PDFVectorStore

            store = PDFVectorStore()

            if store.collection.count() == 0:
                raise RuntimeError("ICAR PDF collection is empty.")

            _pdf_store = store
            _pdf_failed_at = None
            return _pdf_store

        except Exception:
            _pdf_failed_at = time.monotonic()
            logger.exception("Could not initialize ICAR PDF vector store.")
            raise

# --------------------------------------------------------------------------
# Keyword search
# --------------------------------------------------------------------------
def _norm(value: Any) -> str:
    return str(value or "").casefold().strip()


def _tokenize(text: str) -> set[str]:
    """Split into lowercase words, dropping very short words and stopwords."""
    return {
        token
        for token in _TOKEN_SPLIT.split(text.casefold())
        if len(token) > 2 and token not in _STOPWORDS
    }


def _contains(text: str, term: str) -> bool:
    """Whole-word match for English terms, substring match for Indic terms.

    Prevents false hits such as "rice" matching inside "price".
    """
    if not term:
        return False

    if re.fullmatch(r"[a-z0-9 \-]+", term):
        return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text) is not None

    return term in text


def _keyword_search(
    query: str,
    crop: str | None = None,
    topic: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Keyword fallback. Crop/topic filter and boost, but never replace relevance."""
    crop_n = _norm(crop)
    topic_n = _norm(topic)
    query_lower = query.casefold()
    query_tokens = _tokenize(query)
    scored: list[tuple[int, int, dict[str, Any]]] = []

    for index, record in enumerate(load_knowledge()):
        if str(record.get("verified", "")).strip().lower() != "true":
            continue

        record_crop = _norm(record.get("crop"))
        record_topic = _norm(record.get("topic"))

        if crop_n and record_crop not in (crop_n, "all"):
            continue
        if topic_n and record_topic != topic_n:
            continue

        raw_keywords = record.get("keywords", [])
        if not isinstance(raw_keywords, list):
            raw_keywords = []
        keywords = [_norm(keyword) for keyword in raw_keywords]

        record_tokens = _tokenize(
            " ".join([record_crop, record_topic, str(record.get("content", "")),
                      *keywords])
        )

        # Relevance comes only from the query text itself.
        relevance = 2 * sum(
            1 for keyword in keywords if _contains(query_lower, keyword)
        )
        relevance += len(query_tokens & record_tokens)

        crop_match = bool(crop_n) and record_crop == crop_n
        topic_match = bool(topic_n) and record_topic == topic_n

        # An exact crop + topic match is a strong signal even when the wording
        # of the question differs from the record.
        if relevance < MIN_RELEVANCE and not (crop_match and topic_match):
            continue

        score = relevance + (3 if crop_match else 0) + (3 if topic_match else 0)
        scored.append((score, index, record))

    # Highest score first; original file order breaks ties deterministically.
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [record for _, _, record in scored[:limit]]


def _pdf_crop_matches(metadata: dict[str, Any], crop: str | None) -> bool:
    """Match crop against the singular and plural PDF metadata fields."""
    if not crop:
        return True

    requested = _norm(crop)

    available = {
        _norm(metadata.get("crop")),
        *[
            _norm(value)
            for value in str(metadata.get("crops") or "").split(",")
        ],
    }

    aliases = {
        "rice": {"rice", "paddy", "धान", "ಭತ್ತ"},
        "cotton": {"cotton", "कपास", "ಹತ್ತಿ"},
        "wheat": {"wheat", "गेहूं", "ಗೋಧಿ"},
        "maize": {"maize", "corn", "मक्का", "ಮೆಕ್ಕೆಜೋಳ"},
    }

    accepted = aliases.get(requested, {requested})
    return bool(accepted.intersection(available))


def _pdf_topic_score(
    metadata: dict[str, Any],
    topic: str | None,
) -> int:
    """Score topic compatibility without discarding potentially useful evidence."""
    if not topic:
        return 0

    actual = _norm(metadata.get("topic"))
    requested = _norm(topic)

    if actual == requested:
        return 4

    topic_aliases = {
        "pest_management": {"pest_management", "disease_management"},
        "disease_management": {"disease_management", "pest_management"},
        "irrigation_water": {"irrigation_water", "climate_weather"},
        "nutrient_fertilizer": {"nutrient_fertilizer", "soil_management"},
        "crop_care": {
            "sowing_planting",
            "pest_management",
            "disease_management",
            "nutrient_fertilizer",
            "irrigation_water",
            "soil_management",
            "weed_management",
        },
    }

    if actual in topic_aliases.get(requested, set()):
        return 2

    return 0


def _pdf_result_to_record(result: dict[str, Any]) -> dict[str, Any]:
    """Convert an ICAR vector result into the record format used by chat."""
    metadata = result.get("metadata") or {}

    verified_value = str(metadata.get("verified", "false")).strip().lower()
    verified = verified_value == "true"

    return {
        "id": str(metadata.get("chunk_id") or result.get("id") or ""),
        "content": str(result.get("document") or ""),
        "crop": metadata.get("crop") or "",
        "crops": metadata.get("crops") or "",
        "topic": metadata.get("topic") or "",
        "topics": metadata.get("topics") or "",
        "state": metadata.get("state") or "",
        "source_name": metadata.get("source_name") or "ICAR Kharif Agro-Advisories 2025",
        "source_url": metadata.get("source_url") or "",
        "source_file": metadata.get("source_file") or "",
        "scope_note": metadata.get("context") or "",
        "language": metadata.get("language") or "en",
        "page_start": metadata.get("page_start"),
        "page_end": metadata.get("page_end"),
        "verified": verified,
        "review_status": metadata.get("review_status") or "pending",
        "_source_type": "icar_pdf",
        "_distance": result.get("distance"),
    }

# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def search_knowledge(
    query: str,
    crop: str | None = None,
    topic: str | None = None,
    limit: int = 3,
    state: str | None = None,
) -> list[dict[str, Any]]:
    """Search original agricultural RAG and ICAR PDF RAG together."""

    if not isinstance(query, str) or not query.strip() or limit < 1:
        return []

    query = query.strip()
    lookup = _records_by_id()

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add_candidate(
        record: dict[str, Any],
        distance: float | None,
        source_type: str,
        topic_score: int = 0,
    ) -> None:
        record_id = str(record.get("id") or "")
        identity = (source_type, record_id)

        if identity in seen:
            return

        seen.add(identity)

        item = dict(record)
        item["_distance"] = distance
        item["_source_type"] = source_type
        item["_topic_score"] = topic_score

        # Lower cosine distance is better.
        numeric_distance = (
            float(distance) if distance is not None else 1.0
        )

        # Topic match is a ranking signal, not proof of relevance.
        item["_ranking_score"] = (
            -numeric_distance + topic_score * 0.025 + _content_topic_score(record.get("content", ""), topic)
        )

        candidates.append(item)

    # 1. Search the original agricultural collection.
    try:
        documents = _get_rag_service().retrieve(
            query=query,
            crop=crop,
            topic=topic,
            limit=max(limit, 5),
        )

        for document in documents:
            record = lookup.get(
                str(document.metadata.get("record_id", ""))
            )

            if record is not None:
                add_candidate(
                    record=record,
                    distance=document.metadata.get("distance"),
                    source_type="agriculture",
                )

    except Exception:
        logger.warning(
            "Original agricultural RAG retrieval failed.",
            exc_info=True,
        )

    # 2. Search the ICAR PDF collection.
    try:
        pdf_store = _get_pdf_store()

        # Retrieve a wider candidate pool because crop/topic ranking
        # happens after semantic retrieval.
        # ICAR advisories are regional; search only when state is known.
        if state:
            pdf_results = pdf_store.search(
                query=query,
                top_k=20,
                crop=crop,
                state=state,
            )
        else:
            pdf_results = []

        for result in pdf_results:
            metadata = result.get("metadata") or {}

            # Do not use ICAR chunks until they have been reviewed.
            if str(metadata.get("verified", "false")).strip().lower() != "true":
                continue

            if not _pdf_crop_matches(metadata, crop):
                continue

            record = _pdf_result_to_record(result)

            topic_score = _pdf_topic_score(metadata, topic)

            record["_topic_score"] = topic_score
            add_candidate(
                record=record,
                distance=result.get("distance"),
                source_type="icar_pdf",
                topic_score=topic_score,
            )

    except Exception:
        logger.warning(
            "ICAR PDF retrieval failed; continuing with original knowledge.",
            exc_info=True,
        )

    # 3. Keep the existing verified JSON keyword fallback.
    if len(candidates) < limit:
        try:
            for record in _keyword_search(
                query=query,
                crop=crop,
                topic=topic,
                limit=limit,
            ):
                add_candidate(
                    record=record,
                    distance=None,
                    source_type="keyword",
                )
        except Exception:
            logger.warning(
                "Agricultural keyword fallback failed.",
                exc_info=True,
            )

    # 4. Merge and rank both collections.
    candidates.sort(
        key=lambda item: (
            -item["_ranking_score"],
            item["_distance"] is None,
        )
    )
    output = []
    seen_results = set()

    for item in candidates:
        content = str(item.get("content", "")).strip()

        key = (
            item.get("_source_type"),
            item.get("source_file") or item.get("source_name"),
            item.get("page_start"),
            item.get("page_end"),
            content[:200],
        )

        if key in seen_results:
            continue

        seen_results.add(key)

        item.pop("_ranking_score", None)
        item.pop("_topic_score", None)
        output.append(item)

        if len(output) >= limit:
            break

    return output


def ensure_index() -> dict[str, Any] | None:
    """Index the knowledge base if it is new or changed (call once at startup).

    Returns index statistics if it indexed, or None if it was already current.
    """
    return _get_rag_service().vector_store.ensure_indexed()


def _content_topic_score(content: str, topic: str | None) -> float:
    if not content or not topic:
        return 0.0

    text = content.lower()

    keywords = {
        "irrigation": [
            "irrigation",
            "irrigate",
            "water level",
            "water depth",
            "ponded water",
            "stagnant water",
            "water shortage",
            "alternate wetting",
            "flooding",
            "dry before irrigating",
        ],
        "fertilizer": [
            "fertilizer",
            "fertiliser",
            "nutrient management",
            "nitrogen",
            "phosphorus",
            "potassium",
            "npk",
            "fym",
            "compost",
            "urea",
        ],
        "pest_disease": [
            "pest",
            "insect",
            "disease",
            "fungus",
            "fungal",
            "bollworm",
            "stem borer",
            "aphid",
            "whitefly",
        ],
    }

    terms = keywords.get(topic, [])
    matches = sum(1 for term in terms if term in text)

    return min(matches, 5) * 0.05