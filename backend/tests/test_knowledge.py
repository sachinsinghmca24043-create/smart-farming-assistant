from services.knowledge_service import search_knowledge


def test_rice_post_harvest_record():
    results = search_knowledge(
        "rice harvest post harvest",
        crop="rice",
        topic="post_harvest",
    )

    assert results
    assert results[0]["id"] == "rice_005"


def test_rice_soil_health_record():
    results = search_knowledge(
        "rice soil health soil test",
        crop="rice",
        topic="soil_health",
    )

    assert results
    assert results[0]["id"] == "rice_002"


def test_rice_irrigation_record():
    results = search_knowledge(
        "rice irrigation water",
        crop="rice",
        topic="irrigation",
    )

    assert results
    assert results[0]["id"] == "rice_001"


def test_unrelated_crop_is_excluded():
    results = search_knowledge(
        "wheat irrigation",
        crop="wheat",
        topic="irrigation",
    )

    assert all(
        record["crop"] in ("wheat", "all")
        for record in results
    )



def test_hindi_irrigation_query_retrieves_rice_record():
    results = search_knowledge(
        "मुझे धान में पानी कब देना चाहिए",
        crop="rice",
        topic="irrigation",
    )

    assert results
    assert results[0]["id"] == "rice_001"


def test_kannada_soil_health_query_retrieves_rice_record():
    results = search_knowledge(
        "ನನ್ನ ಭತ್ತದ ಬೆಳೆಗೆ ಮಣ್ಣಿನ ಪರೀಕ್ಷೆ",
        crop="rice",
        topic="soil_health",
    )

    assert results
    assert results[0]["id"] == "rice_002"
