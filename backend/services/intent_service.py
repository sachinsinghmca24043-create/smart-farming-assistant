from __future__ import annotations  # lets `str | None` work on Python 3.8+

import logging
import re
from functools import lru_cache

from transformers import pipeline

logger = logging.getLogger(__name__)

MODEL_NAME = "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli"

# Used with hypothesis_template below, so each label is a short noun phrase:
# "This message is about {label}."
HYPOTHESIS_TEMPLATE = "This message is about {}."

# Labels are written to be distinct (no overlapping wording such as
# "soil nutrition" appearing in both fertilizer and soil_health).
INTENT_LABELS = {
    "crop_care": "general crop care and cultivation practices",
    "pest_disease": "crop pests, insects, or plant diseases",
    "fertilizer": "fertilizers, manure, and crop nutrients",
    "irrigation": "watering crops and irrigation schedules",
    "weather": "weather, rain, or weather forecasts",
    "government_schemes": "government agricultural schemes or subsidies",
    "soil_health": "soil health, soil quality, and soil testing",
    "crop_rotation": "crop rotation or alternating crops",
    "yield_management": "increasing or improving crop yield and productivity",
    "post_harvest": "post-harvest activities or what to do after harvesting",
    "waterlogging": "waterlogged fields or standing water after rain",
    "market_prices": "crop market prices, mandi rates, or minimum support price",
    "expert_support": "agricultural expert advice or Kisan Call Centre support",
    "other": "something unrelated or unclear",
}

DESCRIPTION_TO_INTENT = {desc: key for key, desc in INTENT_LABELS.items()}

# Confidence thresholds
HIGH_CONFIDENCE = 0.80  # model result is trusted even if a keyword disagrees
LOW_CONFIDENCE = 0.35   # below this the result is flagged as uncertain

# Basic multilingual crop-name dictionary.
CROP_ALIASES = {
    "rice": [
        "rice", "paddy", "paddy crop", "rice crop",
        "धान", "भात", "ಭತ್ತ", "ಅಕ್ಕಿ",
    ],
    "wheat": ["wheat", "गेहूं", "गेंहूँ", "ಗೋಧಿ"],
    "maize": ["maize", "corn", "मक्का", "ಮೆಕ್ಕೆಜೋಳ"],
    "cotton": ["cotton", "कपास", "ಹತ್ತಿ"],
    "sugarcane": ["sugarcane", "गन्ना", "ಕಬ್ಬು"],
    "tomato": ["tomato", "टमाटर", "ಟೊಮೇಟೊ", "ಟೊಮ್ಯಾಟೊ"],
    "potato": ["potato", "आलू", "ಆಲೂಗಡ್ಡೆ"],
    "onion": ["onion", "प्याज", "प्याज़", "ಈರುಳ್ಳಿ"],
    "soybean": ["soybean", "soyabean", "सोयाबीन", "ಸೋಯಾಬೀನ್"],
}

# Single source of truth for topic keywords. It is used for entity extraction
# AND as the rule-based fallback, so the two can never drift apart.
# Broad words ("water", "पानी", "ಮಳೆಯ ನಂತರ") were removed because they
# matched almost any water- or rain-related sentence.
TOPIC_ALIASES = {
    "fertilizer": [
        "fertilizer", "fertiliser", "manure", "nutrient", "urea",
        "खाद", "उर्वरक", "पोषक", "गोबर की खाद",
        "ಗೊಬ್ಬರ", "ಪೋಷಕಾಂಶ",
    ],
    "irrigation": [
        "irrigation", "irrigate", "watering", "water crops", "how much water",
        "सिंचाई", "पानी देना", "पानी कब",
        "ನೀರಾವರಿ", "ನೀರು ಹಾಕು", "ನೀರು ಹಾಕಬೇಕು", "ನೀರು ಹಾಯಿಸು",
        "ನೀರನ್ನು ಹಾಕಬೇಕು",
    ],
    "weather": [
        "weather", "rain", "rainfall", "forecast", "strong winds",
        "मौसम", "बारिश", "वर्षा", "तेज हवा",
        "ಹವಾಮಾನ", "ಮಳೆ", "ಗಾಳಿ",
    ],
    "crop_care": [
        "crop care", "crop cultivation", "crop health", "crop management",
        "take care of my crop", "care of the crop", "how to grow", "cultivate",
        "देखभाल", "फसल की देखभाल", "खेती कैसे करें",
       "ಬೆಳೆ ಆರೈಕೆ", "ಬೆಳೆಗಳನ್ನು ಆರೋಗ್ಯಕರವಾಗಿ",
       "ಭತ್ತದ ಬೆಳೆಯನ್ನು ಹೇಗೆ ಆರೈಕೆ ಮಾಡಬೇಕು",
      "ಬೆಳೆ ಆರೈಕೆ ಮಾಡುವುದು",
    ],
    "soil_health": [
        "soil health", "soil quality", "soil test", "soil testing",
        "मिट्टी की सेहत", "मिट्टी की जांच", "मृदा स्वास्थ्य",
        "ಮಣ್ಣಿನ ಆರೋಗ್ಯ", "ಮಣ್ಣಿನ ಪರೀಕ್ಷೆ",
    ],
    "crop_rotation": [
        "crop rotation", "rotate crops", "crop sequence",
        "फसल चक्र", "फसल बदलना",
        "ಬೆಳೆ ಪರಿವರ್ತನೆ", "ಬೆಳೆ ಸರದಿ",  # "ಬೆಳೆ ಸರದಿ" = crop rotation
    ],
    "yield_management": [
        "crop yield", "increase yield", "improve yield",
        "उपज बढ़ाना", "पैदावार बढ़ाना",
        "ಇಳುವರಿ", "ಇಳುವರಿ ಹೆಚ್ಚಿಸುವುದು",
    ],
    "post_harvest": [
        "after harvest", "post harvest", "post-harvest", "next crop after rice",
        "after rice harvest", "what to do after harvest",
        "कटाई के बाद", "फसल कटाई के बाद", "ಕೊಯ್ಲಿನ ನಂತರ",
    ],
    "pest_disease": [
        "pest", "pests", "insect", "insects", "disease",
        "yellow spots", "yellow spot", "spotted leaves", "yellow leaves",
        "leaf spots",
        "पीले धब्बे", "पीली पत्तियां", "पत्तियों पर धब्बे",
        "कीट", "कीड़े", "कीड़ा", "रोग", "बीमारी",
        "ಹಳದಿ ಕಲೆ", "ಹಳದಿ ಕಲೆಗಳು", "ಎಲೆಗಳಲ್ಲಿ ಹಳದಿ", "ಕೀಟ", "ರೋಗ",
    ],
    "waterlogging": [
        "waterlogging", "waterlogged field", "waterlogged", "standing water",
        "excess water", "water filled field", "standing water after heavy rain",
        "खेत में पानी भरना", "खेत में पानी भर गया", "जलभराव", "पानी भर गया",
        "ಹೊಲದಲ್ಲಿ ನೀರು ನಿಲ್ಲುವುದು", "ನೀರು ನಿಂತಿದೆ",
    ],
    "market_prices": [
        "market price", "market prices", "mandi", "mandi rate", "mandi price",
        "selling price", "crop price", "price of", "msp",
        "minimum support price",
        "बाजार भाव", "मंडी भाव", "मंडी", "फसल का भाव", "न्यूनतम समर्थन मूल्य",
        "ಮಾರುಕಟ್ಟೆ ಬೆಲೆ", "ಮಾರುಕಟ್ಟೆ", "ಬೆಲೆ", "ಕನಿಷ್ಠ ಬೆಂಬಲ ಬೆಲೆ",
    ],
    "government_schemes": [
        "government scheme", "government schemes", "government subsidy",
        "subsidy", "subsidies", "farmer scheme", "farming scheme",
        "agricultural scheme", "scheme eligibility",
        "सरकारी योजना", "सरकारी योजनाएं", "सब्सिडी", "किसान योजना", "कृषि योजना",
        "ಯೋಜನೆ", "ಸಬ್ಸಿಡಿ", "ಸರ್ಕಾರಿ ಯೋಜನೆ",
    ],
    "expert_support": [
        "agricultural expert", "expert advice", "agricultural advice",
        "farmer advice", "get agricultural advice", "kisan call centre",
        "कृषि विशेषज्ञ", "किसान कॉल सेंटर", "कृषि सलाह", "कृषि सलाहकार",
        "ಕೃಷಿ ತಜ್ಞ", "ಕಿಸಾನ್ ಕಾಲ್ ಸೆಂಟರ್", "ಕೃಷಿ ಸಲಹೆ",
    ],
}

# When several topics are mentioned, the more specific one wins.
TOPIC_PRIORITY = [
    "waterlogging",
    "pest_disease",
    "fertilizer",
    "crop_care",
    "soil_health",
    "crop_rotation",
    "yield_management",
    "post_harvest",
    "expert_support",
    "market_prices",
    "government_schemes",
    "weather",
    "irrigation",
    
]

LOCATION_ALIASES = {
    "Kanpur": ["kanpur", "कानपुर", "ಕಾನ್ಪುರ"],
    "Bengaluru": ["bengaluru", "bangalore", "ಬೆಂಗಳೂರು"],
    "Mysuru": ["mysuru", "mysore", "ಮೈಸೂರು"],
    "Delhi": ["delhi", "दिल्ली", "ದೆಹಲಿ"],
    "Lucknow": ["lucknow", "लखनऊ", "ಲಕ್ನೋ"],
}


@lru_cache(maxsize=1)
def get_classifier():
    """Load the Hugging Face model once and reuse it."""
    return pipeline(
        "zero-shot-classification",
        model=MODEL_NAME,
        device=-1,  # CPU; works without a CUDA GPU
    )


def detect_language(text: str) -> str:
    """Guess the language from the dominant script (en / hi / kn)."""
    devanagari = sum("\u0900" <= ch <= "\u097F" for ch in text)
    kannada = sum("\u0C80" <= ch <= "\u0CFF" for ch in text)

    if devanagari == 0 and kannada == 0:
        return "en"
    return "hi" if devanagari >= kannada else "kn"


def contains_alias(text: str, alias: str) -> bool:
    """Match English terms as whole words and Indic terms by phrase."""
    alias = alias.casefold().strip()

    if not alias:
        return False

    if re.fullmatch(r"[a-z0-9 \-]+", alias):
        pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None

    return alias in text


def _match(normalized: str, alias_map: dict) -> list:
    return [
        key
        for key, aliases in alias_map.items()
        if any(contains_alias(normalized, alias) for alias in aliases)
    ]


def extract_entities(text: str) -> dict:
    """Extract supported crop, topic, and location entities."""
    normalized = text.casefold()

    return {
        "crops": _match(normalized, CROP_ALIASES),
        "topics": _match(normalized, TOPIC_ALIASES),
        "locations": _match(normalized, LOCATION_ALIASES),
    }


def pick_topic_by_priority(topics: list) -> str | None:
    """Return the highest-priority topic from a list of detected topics."""
    return next((t for t in TOPIC_PRIORITY if t in topics), None)


def detect_intent_from_keywords(text: str) -> str | None:
    """Keyword-only intent detection (used if the model is unavailable)."""
    topics = extract_entities(text)["topics"]
    return pick_topic_by_priority(topics)


def run_model(text: str) -> tuple[str | None, float]:
    """Run the zero-shot model. Returns (None, 0.0) if it cannot be used."""
    try:
        result = get_classifier()(
            text,
            candidate_labels=list(INTENT_LABELS.values()),
            hypothesis_template=HYPOTHESIS_TEMPLATE,
            multi_label=False,
        )
    except Exception:  # model download/load/inference failure
        logger.exception("Zero-shot classifier failed; using keyword rules only.")
        return None, 0.0

    intent = DESCRIPTION_TO_INTENT[result["labels"][0]]
    return intent, float(result["scores"][0])


def resolve_intent(
    model_intent: str | None, confidence: float, topics: list
) -> tuple[str, str]:
    """Combine model prediction and keyword rules. Returns (intent, source)."""
    keyword_intent = pick_topic_by_priority(topics)

    # Model unavailable: rely on keywords only.
    if model_intent is None:
        return (keyword_intent or "other"), "keyword_rule"

    # Model and keywords agree (or the model's pick is one of the mentioned topics).
    if model_intent != "other" and model_intent in topics:
        return model_intent, "model"

    if keyword_intent:
        # A very confident, specific model prediction beats a conflicting keyword.
        if model_intent != "other" and confidence >= HIGH_CONFIDENCE:
            return model_intent, "model"
        return keyword_intent, "keyword_rule"

    return model_intent, "model"


def analyze_intent(text: str, language: str | None = None) -> dict:
    """Classify a request and extract agricultural entities.

    `language` may be "en", "hi", "kn", or None/"auto" to detect it from the script.
    """
    if not isinstance(text, str):
        raise TypeError("Message must be a string.")

    text = text.strip()
    if not text:
        raise ValueError("Message cannot be empty.")

    if language is None or language == "auto":
        language = detect_language(text)

    entities = extract_entities(text)
    model_intent, confidence = run_model(text)
    intent, source = resolve_intent(model_intent, confidence, entities["topics"])

    return {
        "language": language,
        "intent": intent,
        "intent_source": source,
        "confidence": round(confidence, 4),
        "low_confidence": source == "model" and confidence < LOW_CONFIDENCE,
        "entities": entities,
        "classification_model": MODEL_NAME,
        "fallback_used": source == "keyword_rule",
    }


if __name__ == "__main__":
    samples = [
        "How much urea should I apply to my wheat crop in Kanpur?",
        "What is the mandi price of tomato today?",
        "मेरे धान के खेत में पानी भर गया है",
        "ನನ್ನ ಬೆಳೆಯ ಎಲೆಗಳಲ್ಲಿ ಹಳದಿ ಕಲೆಗಳು ಕಾಣಿಸುತ್ತಿವೆ",
    ]
    for sample in samples:
        print(sample, "->", analyze_intent(sample))