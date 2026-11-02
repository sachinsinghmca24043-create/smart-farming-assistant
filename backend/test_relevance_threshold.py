
from services.vector_store import AgricultureVectorStore

store = AgricultureVectorStore()

questions = [
    # Relevant agricultural questions
    "How should I irrigate my rice crop?",
    "धान की फसल में सिंचाई कब करनी चाहिए?",
    "ಭತ್ತದ ಬೆಳೆಗೆ ಯಾವಾಗ ನೀರು ಹಾಕಬೇಕು?",
    "How can I control pests in rice?",
    "Which fertilizer is suitable for wheat?",

    # Unrelated questions
    "What is the capital of France?",
    "Tell me a joke.",
    "How do I reset my iPhone password?",
    "Who won the football match?",
]

for question in questions:
    print("\n" + "=" * 65)
    print("Question:", question)

    results = store.search(query=question, top_k=3)

    if not results:
        print("No results")
        continue

    for result in results:
        metadata = result["metadata"]
        print(
            "Record:", metadata.get("record_id"),
            "| Topic:", metadata.get("topic"),
            "| Distance:", round(result["distance"], 4),
            "| Verified:", metadata.get("verified"),
        )
