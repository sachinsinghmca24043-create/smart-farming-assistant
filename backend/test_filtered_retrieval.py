from services.vector_store import AgricultureVectorStore

store = AgricultureVectorStore()

tests = [
    {
        "language": "en",
        "crop": "rice",
        "question": "How should I irrigate my rice crop?"
    },
    {
        "language": "hi",
        "crop": "rice",
        "question": "धान की फसल में सिंचाई कब करनी चाहिए?"
    },
    {
        "language": "kn",
        "crop": "rice",
        "question": "ಭತ್ತದ ಬೆಳೆಗೆ ಯಾವಾಗ ನೀರು ಹಾಕಬೇಕು?"
    }
]

for test in tests:
    print("\n", "=" * 55)
    print("Language:", test["language"])
    print("Question:", test["question"])

    results = store.search(
        query=test["question"],
        top_k=3,
        language=test["language"],
        crop=test["crop"]
    )

    for result in results:
        metadata = result["metadata"]
        print("\nRecord:", metadata.get("record_id"))
        print("Crop:", metadata.get("crop"))
        print("Topic:", metadata.get("topic"))
        print("Language:", metadata.get("language"))
        print("Similarity:", result["score"])
        print("Verified:", metadata.get("verified"))
