
from functools import lru_cache
import re
from transformers import pipeline


MODEL_NAME = (
    "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli"
)

INTENT_LABELS = {
    "crop_care": "asking about general crop care and cultivation",
    "pest_disease": "asking about crop pests, insects, or plant diseases",
    "fertilizer": "asking about fertilizers, nutrients, or soil nutrition",
    "irrigation": "asking about watering crops and irrigation",
    "weather": "asking about weather, rain, or weather forecasts",
    "government_schemes": "asking about government agricultural schemes or subsidies",
    "other": "asking about something unrelated or unclear in agriculture",
}

# Basic multilingual crop-name dictionary.
# We can expand this as we add more agricultural data.
CROP_ALIASES = {
    "rice": [
    "rice", "paddy", "paddy crop", "rice crop",
    "धान", "धन", "भात", "ಭತ್ತ", "ಅಕ್ಕಿ",],
    "wheat": ["wheat", "गेहूं", "गेंहूँ", "ಗೋಧಿ"],
    "maize": ["maize", "corn", "मक्का", "ಮೆಕ್ಕೆಜೋಳ"],
    "cotton": ["cotton", "कपास", "ಹತ್ತಿ"],
    "sugarcane": ["sugarcane", "गन्ना", "ಕಬ್ಬು"],
    "tomato": ["tomato", "टमाटर", "ಟೊಮೇಟೊ", "ಟೊಮ್ಯಾಟೊ"],
}

TOPIC_ALIASES = {
    "fertilizer": [
        "fertilizer", "fertiliser", "manure", "nutrient",
        "खाद", "उर्वरक", "पोषक", "गೊಬ್ಬರ", "ಗೊಬ್ಬರ",
        "ಪೋಷಕಾಂಶ",
    ],
    "irrigation": [
        "irrigation", "watering", "water crops","water","how much water",
        "सिंचाई", "पानी देना","पानी", "ನೀರಾವರಿ", "ನೀರು ಹಾಕು",
        "ನೀರು ಹಾಕು","ನೀರು",
        "ನೀರು ಹಾಕಬೇಕು",
        "ನೀರು ಹಾಯಿಸು",
        "ನೀರನ್ನು ಹಾಕಬೇಕು",
    ],
    "pest_disease": [
        "pest", "pests", "insect", "insects", "disease",
        "कीट", "कीड़े", "कीड़ा", "कीड़े लग गए",
        "रोग", "बीमारी", "कीट लग गए",
        "ಕೀಟ", "ರೋಗ",
    ],
    "weather": [
        "weather", "rain", "forecast",
        "मौसम", "बारिश", "ಹವಾಮಾನ", "ಮಳೆ",
    ],
    "crop_care": [
        "crop care", "crop cultivation", "crop health",
        "take care of my crop","care for rice","rice crop care",
        "देखभाल", "फसल की देखभाल", "ಬೆಳೆ ಆರೈಕೆ","crop management",
        "ಬೆಳೆ ಆರೈಕೆ", "ಬೆಳೆಗಳನ್ನು ಆರೋಗ್ಯಕರವಾಗಿ",
"take care of my rice crop",
"how to grow rice","crop care", "crop cultivation", "crop health",
"crop management", "care of the crop",
"care of the paddy crop", "paddy crop care",
"care of rice", "rice crop care",
"take care of my crop", "how to grow rice",
"देखभाल", "फसल की देखभाल", "ಬೆಳೆ ಆರೈಕೆ",
    ],

    "soil_health": [
        "soil health", "soil quality", "soil test", "soil testing",
        "मिट्टी की सेहत", "मिट्टी की जांच", "मृदा स्वास्थ्य",
        "ಮಣ್ಣಿನ ಆರೋಗ್ಯ", "ಮಣ್ಣಿನ ಪರೀಕ್ಷೆ",
    ],
    "crop_rotation": [
        "crop rotation", "rotate crops", "crop sequence",
        "फसल चक्र", "फसल बदलना",
        "ಬೆಳೆ ಪರಿವರ್ತನೆ", "ಬೆಳೆ ಸರದಿ",
    ],
    "yield_management": [
        "crop yield", "increase yield", "improve yield",
        "उपज बढ़ाना", "पैदावार बढ़ाना",
        "ಇಳುವರಿ", "ಇಳುವರಿ ಹೆಚ್ಚಿಸುವುದು",
    ],
   
    "post_harvest": [
        "after harvest",
        "post harvest",
        "next crop after rice",
        "after rice harvest",
        "what to do after harvest",
        "कटाई के बाद",
        "फसल कटाई के बाद",
        "ಕೊಯ್ಲಿನ ನಂತರ",
    ],

    "waterlogging": [
        "waterlogging", "waterlogged field", "excess water",
        "खेत में पानी भरना", "जलभराव",
        "ಹೊಲದಲ್ಲಿ ನೀರು ನಿಲ್ಲುವುದು",
    ],
    "expert_support": [
        "agricultural expert", "expert advice", "kisan call centre",
        "कृषि विशेषज्ञ", "किसान कॉल सेंटर",
        "ಕೃಷಿ ತಜ್ಞ", "ಕಿಸಾನ್ ಕಾಲ್ ಸೆಂಟರ್",
    ],

}

# Starter dictionary only. Add more locations relevant to your users.
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


def contains_alias(text: str, alias: str) -> bool:
    """Match English terms as whole words and Indic terms by phrase."""
    alias = alias.casefold().strip()

    if not alias:
        return False

    if re.fullmatch(r"[a-z0-9 ]+", alias):
        pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None

    return alias in text


def extract_entities(text: str) -> dict:
    """Extract supported crop, topic, and location entities."""
    normalized = text.casefold()

    crops = [
        crop
        for crop, aliases in CROP_ALIASES.items()
        if any(contains_alias(normalized, alias) for alias in aliases)
    ]

    topics = [
        topic
        for topic, aliases in TOPIC_ALIASES.items()
        if any(contains_alias(normalized, alias) for alias in aliases)
    ]

    locations = [
        location
        for location, aliases in LOCATION_ALIASES.items()
        if any(contains_alias(normalized, alias) for alias in aliases)
    ]

    return {
        "crops": crops,
        "topics": topics,
        "locations": locations,
    }




def detect_intent_from_keywords(text: str) -> str | None:
    """Fallback for clear agricultural terms in supported languages."""

    text = text.casefold()

    
    keyword_groups = {
        "government_schemes": [
            "government scheme", "government schemes",
            "government subsidy", "government subsidies",
            "subsidy", "subsidies", "farmer scheme",
            "farming scheme", "agricultural scheme",
            "agricultural support program", "agricultural support programme",
            "scheme eligibility", "farmer eligibility",
            "सरकारी योजना", "सरकारी योजनाएं", "सब्सिडी",
            "किसान योजना", "कृषि योजना",
            "ಯೋಜನೆ", "ಸಬ್ಸಿಡಿ", "ಸರ್ಕಾರಿ ಯೋಜನೆ",
        ],
        "pest_disease": [
            "pest", "pests", "insect", "insects", "aphid", "aphids",
            "fungal infection", "fungus", "fungal disease",
            "plant disease", "crop disease", "disease", "diseases",
            "कीट", "कीड़े", "कीड़ा", "कीड़े लग गए",
            "रोग", "बीमारी", "फफूंद", "माहू",
            "ಕೀಟ", "ರೋಗ", "ಶಿಲೀಂಧ್ರ",
        ],
        "fertilizer": [
            "fertilizer", "fertiliser", "manure", "nutrient",
            "nutrients", "soil nutrition", "urea",
            "खाद", "उर्वरक", "पोषक", "गोबर की खाद",
            "ಗೊಬ್ಬರ", "ಪೋಷಕಾಂಶ",
        ],
        "irrigation": [
            "irrigation", "irrigate", "irrigated", "watering",
            "water crops", "water the crop", "water the plants",
            "how much water", "when should i water",
            "how often should", "water supply",
            "सिंचाई", "पानी देना", "पानी कब", "पानी",
            "ನೀರಾವರಿ", "ನೀರು ಹಾಕು", "ನೀರು ಹಾಕಬೇಕು",
            "ನೀರು ಹಾಯಿಸು", "ನೀರನ್ನು ಹಾಕಬೇಕು",
        ],
        "weather": [
            "weather", "rain", "rainfall", "forecast",
            "strong winds", "strong wind", "heavy wind",
            "weather conditions", "weather forecast",
            "मौसम", "बारिश", "वर्षा", "तेज हवा", "आंधी",
            "ಹವಾಮಾನ", "ಮಳೆ", "ಗಾಳಿ",
        ],
        "crop_care": [
            "crop care", "crop cultivation", "crop health",
            "crop management", "care of the crop",
            "take care of my crop", "take care of my rice crop",
            "look after", "how to grow", "how do i grow",
            "best way to grow", "cultivate", "cultivation",
            "improve tomato growth", "plant growth",
            "paddy crop care", "rice crop care", "care of rice",
            "देखभाल", "फसल की देखभाल", "खेती कैसे करें",
            "फसल कैसे उगाएं", "फसल उगाने",
            "ಬೆಳೆ ಆರೈಕೆ", "ಬೆಳೆ ಬೆಳೆಸುವುದು",
        ],
        "soil_health": [
            "soil health", "soil quality", "soil test", "soil testing",
            "soil nutrients", "मिट्टी की सेहत", "मिट्टी की जांच",
            "मृदा स्वास्थ्य", "ಮಣ್ಣಿನ ಆರೋಗ್ಯ", "ಮಣ್ಣಿನ ಪರೀಕ್ಷೆ",
        ],
        "crop_rotation": [
            "crop rotation", "rotate crops", "crop sequence",
            "फसल चक्र", "फसल बदलना",
            "ಬೆಳೆ ಪರಿವರ್ತನೆ", "ಬೆಳೆ ಸರದಿ",
        ],
        "yield_management": [
            "crop yield", "increase yield", "improve yield",
            "उपज बढ़ाना", "पैदावार बढ़ाना",
            "ಇಳುವರಿ", "ಇಳುವರಿ ಹೆಚ್ಚಿಸುವುದು",
        ],
        "post_harvest": [
            "after harvest", "post harvest", "next crop after rice",
            "after rice harvest", "what to do after harvest",
            "कटाई के बाद", "फसल कटाई के बाद", "ಕೊಯ್ಲಿನ ನಂತರ",
        ],
        "waterlogging": [
            "waterlogging", "waterlogged field", "excess water",
            "खेत में पानी भरना", "जलभराव",
            "ಹೊಲದಲ್ಲಿ ನೀರು ನಿಲ್ಲುವುದು",
        ],
        "expert_support": [
            "agricultural expert", "expert advice", "kisan call centre",
            "कृषि विशेषज्ञ", "किसान कॉल सेंटर",
            "ಕೃಷಿ ತಜ್ಞ", "ಕಿಸಾನ್ ಕಾಲ್ ಸೆಂಟರ್",
        ],
    }


    for intent, keywords in keyword_groups.items():
        if any(keyword in text for keyword in keywords):
            return intent

    return None





def analyze_intent(text: str, language: str = "en") -> dict:
    """Classify a request and extract agricultural entities."""
    text = text.strip()

    if not text:
        raise ValueError("Message cannot be empty.")

    entities = extract_entities(text)
    classifier = get_classifier()

    result = classifier(
        text,
        candidate_labels=list(INTENT_LABELS.values()),
        multi_label=False,
    )

    predicted_description = result["labels"][0]
    confidence = float(result["scores"][0])

    intent = next(
        key
        for key, description in INTENT_LABELS.items()
        if description == predicted_description
    )

    fallback_used = False

    # Explicitly mentioned topics should take priority over a
    # conflicting general model prediction.
    topic_priority = [
        "pest_disease",
        "fertilizer",
        "irrigation",
        "weather",
        "government_schemes",
        "soil_health",
        "crop_rotation",
        "yield_management",
        "post_harvest",
        "waterlogging",
        "expert_support",
    ]

    explicit_topic = next(
        (topic for topic in topic_priority if topic in entities["topics"]),
        None,
    )

    if explicit_topic:
        intent = explicit_topic
        fallback_used = True
    elif confidence < 0.35 or intent == "other":
        fallback_intent = detect_intent_from_keywords(text)
        if fallback_intent:
            intent = fallback_intent
            fallback_used = True

    return {
        "language": language,
        "intent": intent,
        "confidence": round(confidence, 4),
        "entities": entities,
        "classification_model": MODEL_NAME,
        "fallback_used": fallback_used,
    }
