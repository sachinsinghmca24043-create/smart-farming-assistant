
from services.intent_service import analyze_intent

questions = [
    "What is the capital of France?",
    "Tell me a joke.",
    "How do I reset my iPhone password?",
    "Who won the football match?",
    "How should I irrigate my rice crop?",
    "How can I control pests in wheat?",
    "धान की फसल में सिंचाई कब करनी चाहिए?",
    "ಭತ್ತದ ಬೆಳೆಗೆ ಯಾವಾಗ ನೀರು ಹಾಕಬೇಕು?",
]

for question in questions:
    result = analyze_intent(question)

    print("\nQuestion:", question)
    print("Intent:", result["intent"])
    print("Confidence:", result["confidence"])
    print("Source:", result["intent_source"])
    print("Topics:", result["entities"]["topics"])
