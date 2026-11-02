from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.documents import Document

from services.vector_store import AgricultureVectorStore

logger = logging.getLogger(__name__)

# Prefix the vector store adds to stored passages (E5-style models).
PASSAGE_PREFIX = "passage: "

# Candidate pool sizes pulled from the vector store before filtering/re-ranking.
BASE_FETCH_K = 20
FILTERED_FETCH_K = 40  # filters discard candidates, so start with more
FETCH_K_PER_RESULT = 4  # pool is never smaller than limit x this

# Re-ranking weights (added to -distance, so higher is better).
LEXICAL_WEIGHT = 0.02
MAX_LEXICAL_HITS = 5     # cap so long queries cannot swamp vector similarity
EXACT_CROP_BOOST = 0.05  # records for the exact crop beat generic "all" records

# Used only for broad crop_care questions.
CROP_CARE_TOPIC_BOOSTS = {
    "irrigation": 0.30,
    "yield_management": 0.25,
    "soil_health": 0.20,
    "pest_disease": 0.15,
    "waterlogging": 0.10,
    "crop_rotation": 0.05,
}

_STOPWORDS = {
    "the", "and", "for", "how", "what", "when", "why", "which", "with",
    "can", "should", "does", "are", "was", "this", "that", "from", "you",
    "your", "have", "has", "about", "into", "any", "get",
}

# Avoids \w, which splits Hindi/Kannada words at combining vowel signs.
_TOKEN_SPLIT = re.compile(r"[\s.,;:!?\"'()\[\]{}\-/।]+")


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_SPLIT.split(text.casefold())
        if len(token) > 2 and token not in _STOPWORDS
    }


def _keywords_text(raw: Any) -> str:
    """Metadata keywords may be a list or (as ChromaDB requires) a plain string."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple, set)):
        return " ".join(str(item) for item in raw)
    return ""


class AgricultureRAGService:
    """LangChain document retrieval backed by local ChromaDB.

    This class only retrieves and ranks knowledge passages. Turning them into
    an answer for the farmer happens in whichever component builds the prompt
    and calls the language model.
    """

    def __init__(self):
        self.vector_store = AgricultureVectorStore()

    def retrieve(
        self,
        query: str,
        crop: str | None = None,
        topic: str | None = None,
        limit: int = 3,
        max_distance: float | None = None,
    ) -> list[Document]:
        """Retrieve, filter, and rank relevant agriculture knowledge.

        `max_distance` (optional) drops weak matches so an unrelated question
        returns nothing instead of the "least bad" passages. The right value
        depends on your embedding model and Chroma distance metric.
        """
        if not isinstance(query, str) or not query.strip():
            return []

        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return []
        if limit < 1:
            return []

        query = query.strip()
        crop_n = _norm(crop)
        topic_n = _norm(topic)
        broad_crop_care = topic_n == "crop_care"

        # Filters are applied after retrieval, so pull a larger pool when used.
        base = FILTERED_FETCH_K if (crop_n or topic_n) else BASE_FETCH_K
        fetch_k = max(base, limit * FETCH_K_PER_RESULT)

        matches = self.vector_store.search(query, top_k=fetch_k)
        if not matches:
            return []

        query_tokens = _tokenize(query)  # computed once, not once per match
        documents: list[Document] = []

        for index, match in enumerate(matches):
            metadata = match.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}

            # Exclude unverified knowledge from farmer-facing responses.
            if str(metadata.get("verified", "")).strip().lower() != "true":
                continue

            content = (match.get("document") or "").removeprefix(PASSAGE_PREFIX)
            if not content.strip():
                continue

            record_crop = _norm(metadata.get("crop"))
            record_topic = _norm(metadata.get("topic"))

            # Crop filter ("all" records apply to every crop).
            if crop_n and record_crop not in (crop_n, "all"):
                continue

            # Topic filter; broad crop_care questions are boosted, not filtered.
            if topic_n and not broad_crop_care and record_topic != topic_n:
                continue

            try:
                distance = float(match.get("distance", 1.0))
            except (TypeError, ValueError):
                distance = 1.0

            if max_distance is not None and distance > max_distance:
                continue

            # Base relevance from vector distance (inverted: higher is better).
            score = -distance

            if crop_n and record_crop == crop_n:
                score += EXACT_CROP_BOOST

            if broad_crop_care:
                score += CROP_CARE_TOPIC_BOOSTS.get(record_topic, 0.0)

            # Lexical overlap bonus using whole words (no "rice" in "price").
            if query_tokens:
                record_tokens = _tokenize(
                    content + " " + _keywords_text(metadata.get("keywords"))
                )
                hits = len(query_tokens & record_tokens)
                score += LEXICAL_WEIGHT * min(hits, MAX_LEXICAL_HITS)

            if metadata.get("record_id") is None:
                logger.debug(
                    "Retrieved passage has no record_id; callers that look "
                    "records up by id will ignore it."
                )

            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        **metadata,
                        "distance": distance,
                        "relevance_score": score,
                        # Original vector-store order, used as a stable tie-breaker.
                        "retrieval_rank": index,
                    },
                )
            )

        # Best score first; earlier vector-store rank wins ties.
        documents.sort(
            key=lambda doc: (
                -doc.metadata["relevance_score"],
                doc.metadata["retrieval_rank"],
            )
        )

        # Keep only the best passage per record.
        unique_documents: list[Document] = []
        seen_records: set[str] = set()

        for document in documents:
            record_id = document.metadata.get("record_id")
            dedupe_key = (
                str(record_id) if record_id is not None else document.page_content
            )

            if dedupe_key in seen_records:
                continue

            seen_records.add(dedupe_key)
            unique_documents.append(document)

            if len(unique_documents) >= limit:
                break

        return unique_documents