
from services.intent_service import analyze_intent


examples = [
    (
        "नमस्ते मुझे धान की फ़सल की देखभाल के बारे में जानकारी चाहिए",
        "hi",
    ),
    (
        "I need information about rice crop care.",
        "en",
    ),
    (
        "ನನ್ನ ಭತ್ತದ ಬೆಳೆಗೆ ಯಾವ ಗೊಬ್ಬರ ಬಳಸಬೇಕು?",
        "kn",
    ),
]


if __name__ == "__main__":
    for message, language in examples:
        print(f"\nMessage: {message}")
        result = analyze_intent(message, language)
        print(result)
