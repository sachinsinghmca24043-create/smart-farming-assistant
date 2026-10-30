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


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def search_knowledge(
    query: str,
    crop: str | None = None,
    topic: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Vector retrieval first, topped up by keyword search if needed."""
    if not isinstance(query, str) or not query.strip() or limit < 1:
        return []

    query = query.strip()
    lookup = _records_by_id()
    results: list[dict[str, Any]] = []
    seen: set[int] = set()  # object identity, so records without ids dedupe too
    distances: dict[int, float | None] = {}  # cosine distance per record (vector hits only)

    try:
        documents = _get_rag_service().retrieve(
            query=query,
            crop=crop,
            topic=topic,
            limit=limit,
        )

        for document in documents:
            record = lookup.get(str(document.metadata.get("record_id", "")))
            if record is not None and id(record) not in seen:
                results.append(record)
                seen.add(id(record))
                distances[id(record)] = document.metadata.get("distance")

    except Exception:
        # Keep the chat available if vector retrieval fails, but leave a trace.
        logger.warning("Vector retrieval failed; using keyword search.",
                       exc_info=True)

    # Fill any remaining slots (or all of them, if retrieval failed).
    if len(results) < limit:
        for record in _keyword_search(query, crop, topic, limit):
            if id(record) not in seen:
                results.append(record)
                seen.add(id(record))

    # Return copies so callers cannot modify the cached knowledge base. Each copy
    # carries "_distance" (None for keyword-only hits) for quality scoring.
    output = []
    for record in results[:limit]:
        item = dict(record)
        item["_distance"] = distances.get(id(record))
        output.append(item)
    return output


def ensure_index() -> dict[str, Any] | None:
    """Index the knowledge base if it is new or changed (call once at startup).

    Returns index statistics if it indexed, or None if it was already current.
    """
    return _get_rag_service().vector_store.ensure_indexed()