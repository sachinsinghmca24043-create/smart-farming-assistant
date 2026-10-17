import pytest

from services.intent_service import analyze_intent


@pytest.mark.parametrize(
    ("message", "language", "expected_intent", "expected_crop"),
    [
        (
            "मुझे धान में पानी कब देना चाहिए",
            "hi",
            "irrigation",
            "rice",
        ),
        (
            "ನನ್ನ ಭತ್ತದ ಬೆಳೆಗೆ ನೀರು ಯಾವಾಗ ಹಾಕಬೇಕು?",
            "kn",
            "irrigation",
            "rice",
        ),
        (
            "धान की मिट्टी की जांच कैसे करें",
            "hi",
            "soil_health",
            "rice",
        ),
        (
            "What should I do after rice harvest?",
            "en",
            "post_harvest",
            "rice",
        ),
    ],
)
def test_multilingual_intent_and_crop(
    message, language, expected_intent, expected_crop
):
    result = analyze_intent(message, language)

    assert result["intent"] == expected_intent
    assert expected_crop in result["entities"]["crops"]
    assert expected_intent in result["entities"]["topics"]
