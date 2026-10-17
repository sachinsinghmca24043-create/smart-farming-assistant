
from services.intent_service import extract_entities


examples = [
    "I need fertilizer for rice in Kanpur.",
    "मेरी गेहूं की फसल में कीड़े लग गए हैं।",
    "ನನ್ನ ಭತ್ತದ ಬೆಳೆಗೆ ನೀರು ಹಾಕಬೇಕು.",
    "What will the weather be like in Bengaluru?",
]


if __name__ == "__main__":
    for message in examples:
        print(f"\nMessage: {message}")
        print(extract_entities(message))
