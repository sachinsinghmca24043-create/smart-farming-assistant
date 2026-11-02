
from services.knowledge_service import _keyword_search


def test_keyword_search_returns_only_verified_records():
    results = _keyword_search(
        query="rice irrigation",
        limit=10,
    )

    assert all(
        str(record.get("verified", "")).strip().lower() == "true"
        for record in results
    )
