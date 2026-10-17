
from services.translation_service import (
    translate_to_english,
    translate_from_english,
)

tests = [
    ("Hindi -> English",
     lambda: translate_to_english(
         "मुझे धान की फसल की देखभाल के बारे में जानकारी चाहिए", "hi")),
    ("Kannada -> English",
     lambda: translate_to_english(
         "ನನಗೆ ಭತ್ತದ ಬೆಳೆಯ ಆರೈಕೆಯ ಬಗ್ಗೆ ಮಾಹಿತಿ ಬೇಕು", "kn")),
    ("English -> Hindi",
     lambda: translate_from_english(
         "How should I take care of my rice crop?", "hi")),
    ("English -> Kannada",
     lambda: translate_from_english(
         "How should I take care of my rice crop?", "kn")),
]

for name, translate in tests:
    print(f"\n--- {name} ---")
    try:
        print(translate())
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
