from services.vector_store import AgricultureVectorStore

store = AgricultureVectorStore()

questions = [
    "How should I irrigate my rice crop?",
    "धान की फसल में सिंचाई कब करनी चाहिए?",
    "ಭತ್ತದ ಬೆಳೆಗೆ ಯಾವಾಗ ನೀರು ಹಾಕಬೇಕು?",
    "What is the capital of France?",
]

for question in questions:
    print("\n" + "=" * 60)
    print("QUESTION:", question)

    results = store.search(query=question, top_k=3)

    for result in results:
        metadata = result["metadata"]

        print("\nRecord:", metadata.get("record_id"))
        print("Topic:", metadata.get("topic"))
        print("Similarity:", result["score"])
        print("Verified:", metadata.get("verified"))
        print("Content:", result["document"][:200])
