
from services.knowledge_service import search_knowledge

tests = [
    ("How should I irrigate my rice crop?", "rice", "irrigation"),
    ("धान की फसल में सिंचाई कब करनी चाहिए?", "rice", "irrigation"),
    ("ಭತ್ತದ ಬೆಳೆಗೆ ಯಾವಾಗ ನೀರು ಹಾಕಬೇಕು?", "rice", "irrigation"),
    ("How can I control pests in wheat?", "wheat", "pest_disease"),
    ("What is the capital of France?", None, None),
]

for question, crop, topic in tests:
    print("\n" + "=" * 60)
    print("Question:", question)

    results = search_knowledge(
        query=question,
        crop=crop,
        topic=topic,
        limit=3,
    )

    if not results:
        print("No relevant verified records found.")
        continue

    for record in results:
        print("ID:", record.get("id"))
        print("Crop:", record.get("crop"))
        print("Topic:", record.get("topic"))
        print("Verified:", record.get("verified"))
        print("Distance:", record.get("_distance"))
