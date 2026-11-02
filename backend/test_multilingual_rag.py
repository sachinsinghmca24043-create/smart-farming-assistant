from services.vector_store import AgricultureVectorStore

store = AgricultureVectorStore()

questions = {
    "English": "How should I take care of my rice crop?",
    "Hindi": "मुझे धान की फसल की देखभाल कैसे करनी चाहिए?",
    "Kannada": "ಭತ್ತದ ಬೆಳೆಯನ್ನು ಹೇಗೆ ಆರೈಕೆ ಮಾಡಬೇಕು?",
}

for language, question in questions.items():
    print("\n" + "=" * 60)
    print(f"LANGUAGE: {language}")
    print(f"QUESTION: {question}")

    results = store.search(
        query=question,
        top_k=3,
        dedupe=True,
    )

    if not results:
        print("No relevant documents found.")
        continue

    for index, result in enumerate(results, start=1):
        metadata = result["metadata"]

        print(f"\nResult {index}")
        print("Record ID:", metadata.get("record_id"))
        print("Document language:", metadata.get("language"))
        print("Crop:", metadata.get("crop"))
        print("Topic:", metadata.get("topic"))
        print("Similarity:", result["score"])
        print("Distance:", result["distance"])
        print("Content:", result["document"][:300])