"""Optional Hugging Face named-entity recognition for place names.

The dictionary matcher in intent_service only knows a handful of cities. This
adds a multilingual NER model that finds other place names in Hindi, Kannada
and English questions. It is OFF by default because the model is a large
download; turn it on with ENABLE_HF_NER=true in .env.

NER_MODEL can be changed; the default is AI4Bharat's IndicNER, which covers
Hindi and Kannada.
"""
from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache

logger = logging.getLogger(__name__)

ENABLED = os.getenv("ENABLE_HF_NER", "false").strip().lower() == "true"
MODEL_NAME = os.getenv("NER_MODEL", "ai4bharat/IndicNER")
MIN_SCORE = 0.7
LOCATION_LABELS = {"LOC", "LOCATION", "GPE"}

_load_lock = threading.Lock()
_load_failed = False


@lru_cache(maxsize=1)
def _pipeline():
    from transformers import pipeline  # imported lazily: heavy

    logger.info("Loading NER model %s ...", MODEL_NAME)
    return pipeline(
        "token-classification",
        model=MODEL_NAME,
        aggregation_strategy="simple",
        device=-1,
    )


def extract_locations(text: str) -> list[str]:
    """Place names found in the text, in order. Returns [] if NER is off or fails."""
    global _load_failed

    if not ENABLED or _load_failed or not text.strip():
        return []

    try:
        with _load_lock:
            ner = _pipeline()
        entities = ner(text[:1000])
    except Exception:
        _load_failed = True  # do not retry a broken model on every request
        logger.warning("NER model unavailable; continuing without it.", exc_info=True)
        return []

    places: list[str] = []
    for entity in entities:
        label = str(entity.get("entity_group", "")).upper().replace("B-", "").replace("I-", "")
        word = str(entity.get("word", "")).replace("▁", " ").strip()
        if label in LOCATION_LABELS and float(entity.get("score", 0)) >= MIN_SCORE and word:
            if word not in places:
                places.append(word)
    return places