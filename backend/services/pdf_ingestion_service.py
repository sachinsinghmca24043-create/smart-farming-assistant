from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pymupdf


class PDFIngestionService:
    """Extract agricultural PDF text while retaining page and layout metadata.

    This service extracts text only. It does not verify agricultural advice
    and does not write anything to ChromaDB.
    """

    STATES = {
        "andhra pradesh", "arunachal pradesh", "assam", "bihar",
        "chhattisgarh", "goa", "gujarat", "haryana", "himachal pradesh",
        "jharkhand", "karnataka", "kerala", "madhya pradesh",
        "maharashtra", "manipur", "meghalaya", "mizoram", "nagaland",
        "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu",
        "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal",
"andaman & nicobar islands",
"andaman and nicobar islands",
    }

    CATEGORIES = {
        "cereal crops": "cereals",
        "fruit & vegetable crops": "fruits_vegetables",
        "fruit and vegetable crops": "fruits_vegetables",
        "commercial crops": "commercial_crops",
        "pulse crops": "pulses",
        "pulse crop": "pulses",
        "oilseed crops": "oilseeds",
        "oil seed crops": "oilseeds",
        "livestock": "livestock",
        "animal husbandry": "livestock",
        "fisheries": "fisheries",
        "poultry": "poultry",
        "spices": "spices",
        "root and tuber crops": "root_tuber_crops",
        "root & tuber crops": "root_tuber_crops",
        "fodder crops": "fodder_crops",
    }

    CROPS = {
        "rice", "paddy", "maize", "wheat", "millets", "sorghum",
        "jowar", "bajra", "ragi", "finger millet", "pulses",
        "pigeon pea", "arhar", "green gram", "black gram",
        "groundnut", "soybean", "sesamum", "cotton", "sugarcane",
        "vegetables", "fruits", "banana", "mango", "litchi",
        "mustard", "chickpea", "lentil", "grape", "cashew",
        "coconut", "betel nut", "pepper", "okra", "tomato",
        "potato", "onion", "chilli", "turmeric",
        "arecanut", "sapota", "papaya", "pineapple", "citrus",
        "brinjal", "cowpea", "cucurbits", "tapioca",
        "sweet potato", "dioscorea", "elephant foot yam",
        "black pepper", "cinnamon", "ginger", "clove",
        "nutmeg", "taro", "cassava",
    }

    CROP_ALIASES = {
        "paddy": "rice",
        "finger_millet": "ragi",
        "finger millet": "ragi",
        "sorghum": "jowar",
        "green_gram": "mung_bean",
        "green gram": "mung_bean",
        "black_gram": "urd_bean",
        "black gram": "urd_bean",
        "pigeon_pea": "arhar",
        "pigeon pea": "arhar",
    }

    CAPTION_HINTS = (
        "photo:", "fig.", "figure ", "image:",
        "varietal demonstration", "field view",
        "icar kharif agro-advisory 2025",
    )

    TOPIC_KEYWORDS = {
        "irrigation": (
            "irrigation", "irrigate", "water management",
            "drainage", "water requirement",
        ),
        "fertilizer": (
            "fertilizer", "fertiliser", "nitrogen", "phosphorus",
            "potassium", "urea", "dap", "nutrient management",
        ),
        "pest_disease": (
            "pest", "disease", "insect", "fungicide", "insecticide",
            "stem borer", "blast", "aphid", "whitefly", "management of",
        ),
        "weed_management": (
            "weed", "herbicide", "weeding", "weedicide",
        ),
        "seed_management": (
            "seed treatment", "seed rate", "seed quality",
            "seed soaking", "seed selection",
        ),
        "sowing": (
            "sowing", "transplanting", "spacing", "planting", "seedling",
        ),
        "harvesting": (
            "harvesting", "harvest", "maturity", "grain yield",
        ),
        "soil_health": (
            "soil health", "soil test", "soil testing", "soil fertility",
        ),
    }

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 150,
        min_chunk_chars: int = 40,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_overlap must be between 0 and chunk_size - 1")
        if min_chunk_chars < 1:
            raise ValueError("min_chunk_chars must be positive")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_chars = min_chunk_chars

    @staticmethod
    def clean_text(text: str) -> str:
        """Normalize safe whitespace and common presentation ligatures."""
        if not isinstance(text, str):
            return ""

        text = text.replace("\u00a0", " ")
        text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
        text = text.replace("\u2010", "-").replace("\u2011", "-")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def split_text(self, text: str) -> list[str]:
        """Split text near paragraph/sentence boundaries with overlap."""
        text = self.clean_text(text)
        if not text:
            return []

        chunks: list[str] = []
        start = 0
        text_length = len(text)

        while start < text_length:
            end = min(start + self.chunk_size, text_length)

            if end < text_length:
                candidates = [
                    text.rfind("\n\n", start, end),
                    text.rfind("\n", start, end),
                    text.rfind(". ", start, end),
                    text.rfind("; ", start, end),
                ]
                boundary = max(candidates)
                if boundary > start + self.chunk_size // 2:
                    end = boundary + (2 if text[boundary:boundary + 2] in ("\n\n", ". ", "; ") else 1)

            chunk = text[start:end].strip()
            if len(chunk) >= self.min_chunk_chars or (not chunks and chunk):
                chunks.append(chunk)

            if end >= text_length:
                break

            next_start = end - self.chunk_overlap
            start = max(start + 1, next_start)

        return chunks

    @staticmethod
    def normalize_heading(text: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip().casefold()
        return text.rstrip(":").strip()

    @staticmethod
    def normalize_crop_name(crop_heading: str) -> str:
        normalized = (crop_heading or "").strip().casefold().replace(" ", "_")
        aliases = {
            "paddy": "rice",
            "finger_millet": "ragi",
            "sorghum": "jowar",
            "green_gram": "mung_bean",
            "black_gram": "urd_bean",
            "pigeon_pea": "arhar",
        }
        return aliases.get(normalized, normalized)

    @classmethod
    def classify_topic(cls, text: str) -> str:
        """Return a preliminary single topic label; not a verified classification."""
        normalized = (text or "").casefold()
        scores = {
            topic: sum(normalized.count(keyword) for keyword in keywords)
            for topic, keywords in cls.TOPIC_KEYWORDS.items()
        }
        if not scores or max(scores.values()) == 0:
            return "crop_care"
        return max(scores, key=scores.get)

    def detect_heading(self, text: str) -> tuple[str, str] | None:
        normalized = self.normalize_heading(text)

        if normalized in self.STATES:
            return "region", normalized.title()

        if normalized in self.CATEGORIES:
            return "category", self.CATEGORIES[normalized]

        # Remove a trailing parenthetical subtitle, e.g. "Mango (New Planting)".
        base_heading = re.sub(r"\s*\([^)]*\)\s*$", "", normalized).strip()

        if base_heading == "coconut & betel nut":
            return "crop", "coconut_betel_nut"

        if base_heading in self.CROPS:
            return "crop", base_heading.replace(" ", "_")

        return None

    @staticmethod
    def detect_paragraph_context(text: str) -> tuple[str, str] | None:
        normalized = (text or "").casefold().strip()

        if normalized.startswith(("for fisheries", "for fishery")):
            return "fisheries", "unknown"

        if normalized.startswith("in animal husbandry"):
            return "livestock", "unknown"

        if normalized.startswith("for better yield"):
            return "fruits_vegetables", "vegetables"

        return None

    @staticmethod
    def _is_caption(text: str, extra_caption_hints: tuple[str, ...]) -> bool:
        normalized = text.casefold().strip()
        hints = PDFIngestionService.CAPTION_HINTS + extra_caption_hints
        return any(normalized.startswith(hint) for hint in hints)

    @staticmethod
    def _stable_document_id(source_name: str, page: int, block: int, chunk: int, content: str) -> str:
        digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:10]
        safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(source_name).stem)[:50]
        return f"{safe_stem}_p{page}_b{block}_c{chunk}_{digest}"

    def extract_pdf_blockwise(
        self,
        pdf_path: str | Path,
        start_page: int = 1,
        end_page: int | None = None,
        region: str = "unknown",
        region_scope: str = "unknown",
        language: str = "en",
        extra_caption_hints: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        """Extract text blocks in physical page order (page numbers are 1-based).

        Heading context is carried across pages. If extraction starts midway
        through a document, pass the known region/crop context separately or
        start from an earlier page so preceding headings are not lost.
        """
        path = Path(pdf_path).expanduser()

        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")
        if path.suffix.casefold() != ".pdf":
            raise ValueError("The supplied file must be a PDF")
        if start_page < 1:
            raise ValueError("start_page must be at least 1")
        if end_page is not None and end_page < start_page:
            raise ValueError("end_page must be greater than or equal to start_page")

        records: list[dict[str, Any]] = []
        current_region = region
        current_crop = "unknown"
        current_crop_heading = "unknown"
        current_category = "general"

        with pymupdf.open(path) as pdf:
            page_count = len(pdf)
            last_page = end_page if end_page is not None else page_count

            if start_page > page_count or last_page > page_count:
                raise ValueError(
                    f"Invalid page range {start_page}-{last_page}; PDF has {page_count} pages"
                )

            for page_index in range(start_page - 1, last_page):
                page = pdf[page_index]
                blocks = page.get_text("blocks")

                # Approximate reading order. Complex multi-column pages should
                # be visually checked before indexing.
                blocks = sorted(blocks, key=lambda block: (round(block[1], 1), block[0]))

                for block_index, block in enumerate(blocks):
                    if len(block) < 5:
                        continue

                    text = self.clean_text(block[4])
                    if not text:
                        continue

                    if re.fullmatch(r"\d{1,4}", text):
                        continue

                    if self._is_caption(text, extra_caption_hints):
                        continue

                    heading = self.detect_heading(text)
                    if heading:
                        heading_type, heading_value = heading

                        if heading_type == "region":
                            current_region = heading_value
                            current_category = "general"
                            current_crop = "unknown"
                            current_crop_heading = "unknown"
                        elif heading_type == "category":
                            current_category = heading_value
                            current_crop = "unknown"
                            current_crop_heading = "unknown"
                        elif heading_type == "crop":
                            current_crop_heading = heading_value
                            current_crop = self.normalize_crop_name(heading_value)
                        continue

                    paragraph_context = self.detect_paragraph_context(text)
                    if paragraph_context:
                        current_category, current_crop = paragraph_context
                        current_crop_heading = current_crop

                    chunks = self.split_text(text)

                    for chunk_index, chunk in enumerate(chunks):
                        if not chunk:
                            continue

                        record_id = self._stable_document_id(
                            path.name, page_index + 1, block_index, chunk_index, chunk
                        )

                        records.append({
                            "id": record_id,
                            "content": chunk,
                            "source": path.name,
                            "source_path": str(path),
                            "source_page": page_index + 1,
                            "source_pages": [page_index + 1],
                            "region": current_region,
                            "region_scope": region_scope,
                            "crop": current_crop,
                            "crop_heading": current_crop_heading,
                            "category": current_category,
                            "topic": self.classify_topic(chunk),
                            "topic_is_preliminary": True,
                            "language": language,
                            "block_index": block_index,
                            "chunk_index": chunk_index,
                            "coordinates": {
                                "x0": round(block[0], 2),
                                "y0": round(block[1], 2),
                                "x1": round(block[2], 2),
                                "y1": round(block[3], 2),
                            },
                            "verified": False,
                            "review_status": "pending",
                        })

        if not records:
            raise ValueError(
                "No extractable text blocks were found. This PDF may be scanned "
                "or image-based and may require OCR."
            )

        return records
