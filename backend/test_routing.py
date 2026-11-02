import asyncio
from unittest.mock import patch

from main import handle_knowledge
from main import resolve_route


def make_analysis(intent, topics=None):
    return {
        "intent": intent,
        "entities": {
            "topics": topics or []
        }
    }


def test_rice_crop_care():
    assert resolve_route(
        "How should I care for rice?",
        make_analysis("crop_care")
    ) == ("knowledge", "crop_care")


def test_weather():
    assert resolve_route(
        "What is the weather in Kanpur?",
        make_analysis("weather")
    ) == ("weather", "weather")


def test_market_prices():
    assert resolve_route(
        "What is today's rice price?",
        make_analysis("market_prices")
    ) == ("market_prices", "market_prices")


def test_government_schemes():
    assert resolve_route(
        "Tell me about government schemes for farmers",
        make_analysis("government_schemes", ["government_schemes"])
    ) == ("knowledge", "government_schemes")


def test_pest_disease():
    assert resolve_route(
        "How can I control rice pests?",
        make_analysis("pest_disease", ["pest_disease"])
    ) == ("knowledge", "pest_disease")


def test_unrelated_question_current_behavior():
    assert resolve_route(
        "What is the capital of France?",
        make_analysis("other")
    ) == ("knowledge", "other")



def test_unrelated_question_skips_knowledge_search():
    with patch("main.search_knowledge") as mock_search:
        result = asyncio.run(
            handle_knowledge(
                message="What is the capital of France?",
                language="en",
                intent="other",
                crop=None,
            )
        )
