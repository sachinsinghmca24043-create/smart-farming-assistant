import pymupdf
import json
from pathlib import Path

PDF_DIR = Path("knowledge_base/source_documents")
OUTPUT_DIR = Path("knowledge_base/extracted")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def extract_pdf(pdf_path):

    document = pymupdf.open(pdf_path)
    extracted_pages = []

    for page_number, page in enumerate(document, start=1):

        text = page.get_text("text")

        if not text.strip():
            continue

        extracted_pages.append({
            "page": page_number,
            "text": text.strip()
        })

    return extracted_pages


for pdf_path in PDF_DIR.glob("*.pdf"):

    print(f"\nExtracting: {pdf_path.name}")

    pages = extract_pdf(pdf_path)

    output_file = OUTPUT_DIR / f"{pdf_path.stem}.json"

    output_file.write_text(
        json.dumps(pages, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"Pages extracted: {len(pages)}")
    print(f"Saved to: {output_file}")