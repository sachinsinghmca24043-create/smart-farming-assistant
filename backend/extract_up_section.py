import json
from pathlib import Path

INPUT_FILE = Path(
    "knowledge_base/extracted/"
    "ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.json"
)

OUTPUT_FILE = Path(
    "knowledge_base/extracted/uttar_pradesh.json"
)

pages = json.loads(
    INPUT_FILE.read_text(encoding="utf-8")
)

up_pages = []

for page in pages:
    page_number = page["page"]

    if 269 <= page_number <= 279:
        up_pages.append({
            "page": page_number,
            "state": "Uttar Pradesh",
            "text": page["text"]
        })

OUTPUT_FILE.write_text(
    json.dumps(up_pages, indent=2, ensure_ascii=False),
    encoding="utf-8"
)

print("State:", "Uttar Pradesh")
print("Pages extracted:", len(up_pages))
print("Saved:", OUTPUT_FILE)