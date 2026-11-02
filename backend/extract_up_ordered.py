import json
import pymupdf
from pathlib import Path

PDF_FILE = Path(
    "knowledge_base/source_documents/"
    "ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf"
)

OUTPUT_FILE = Path(
    "knowledge_base/extracted/uttar_pradesh_ordered.json"
)

pdf = pymupdf.open(PDF_FILE)

ordered_pages = []

# PDF pages 269–279 correspond to indexes 268–278.
for page_index in range(268, 279):

    page = pdf[page_index]

    blocks = page.get_text("blocks")

    # Sort blocks from top to bottom, then left to right.
    blocks = sorted(
        blocks,
        key=lambda block: (block[1], block[0])
    )

    page_blocks = []

    for block in blocks:

        text = block[4].strip()

        if not text:
            continue

        page_blocks.append({
            "text": text,
            "x0": round(block[0], 2),
            "y0": round(block[1], 2),
            "x1": round(block[2], 2),
            "y1": round(block[3], 2)
        })

    ordered_pages.append({
        "page": page_index + 1,
        "blocks": page_blocks
    })

OUTPUT_FILE.write_text(
    json.dumps(
        ordered_pages,
        indent=2,
        ensure_ascii=False
    ),
    encoding="utf-8"
)

print("Pages processed:", len(ordered_pages))
print("Saved:", OUTPUT_FILE)