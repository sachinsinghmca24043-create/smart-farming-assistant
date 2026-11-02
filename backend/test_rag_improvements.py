from services.rag_service import AgricultureRAGService

rag = AgricultureRAGService()

tests = [
    ("How should I irrigate my rice crop?", "rice", "irrigation"),
    ("धान की फसल में सिंचाई कब करनी चाहिए?", "rice", "irrigation"),
    ("ಭತ್ತದ ಬೆಳೆಗೆ ಯಾವಾಗ ನೀರು ಹಾಕಬೇಕು?", "rice", "irrigation"),
    ("What is the capital of France?", None, None),
]

for question, crop, topic in tests:
    print("\n" + "=" * 60)
    print("Question:", question)

    # Apply the exact topic filter for specific irrigation questions.
    results = rag.retrieve(
        query=question,
        crop=crop,
        topic=topic,
        limit=3,
    )

    if not results:
        print("No matching knowledge found.")
        continue

    for doc in results:
        metadata = doc.metadata

        print("Record:", metadata.get("record_id"))
        print("Crop:", metadata.get("crop"))
        print("Topic:", metadata.get("topic"))
        print("Verified:", metadata.get("verified"))
        print("Distance:", round(metadata.get("distance", 0), 4))
        print("Relevance:", round(metadata.get("relevance_score", 0), 4))
        print("-" * 40)