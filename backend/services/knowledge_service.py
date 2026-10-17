
import json
from pathlib import Path
from typing import Any

KNOWLEDGE_FILE = (
    Path(__file__).resolve().parent.parent
    / "knowledge_base"
    / "agriculture.json"
)


def load_knowledge() -> list[dict[str, Any]]:
    """Load agricultural records from the JSON knowledge base."""
    if not KNOWLEDGE_FILE.exists():
        raise FileNotFoundError(
            f"Agricultural knowledge file not found: {KNOWLEDGE_FILE}"
        )

    with KNOWLEDGE_FILE.open("r", encoding="utf-8") as file:
        records = json.load(file)

    if not isinstance(records, list):
        raise ValueError("The knowledge base must contain a JSON list.")

    return records


def search_knowledge(
    query: str,
    crop: str | None = None,
    topic: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return only records that match the requested crop/topic and query."""
    records = load_knowledge()
    query_lower = query.casefold()
    results = []

    for record in records:
        record_crop = str(record.get("crop", "")).casefold()
        record_topic = str(record.get("topic", "")).casefold()
        keywords = [
            str(keyword).casefold()
            for keyword in record.get("keywords", [])
        ]

        searchable_text = " ".join([
            record_crop,
            record_topic,
            str(record.get("content", "")).casefold(),
            *keywords,
        ])

        if crop and record_crop not in (crop.casefold(), "all"):
            continue

        if topic and record_topic != topic.casefold():
            continue

        score = 0

        for keyword in keywords:
            if keyword and keyword in query_lower:
                score += 2

        if topic and record_topic == topic.casefold():
            score += 3

        if crop and record_crop == crop.casefold():
            score += 3

        for word in query_lower.split():
            if len(word) > 2 and word in searchable_text:
                score += 1

        # Require a meaningful match instead of returning unrelated records.
        if score >= 3:
            results.append((score, record))

    results.sort(key=lambda item: item[0], reverse=True)
    return [record for _, record in results[:limit]]
