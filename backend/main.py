import copy
import hmac
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from dotenv import load_dotenv

# Must run BEFORE the service imports below, in case any of them read
# environment variables at import time.
load_dotenv()

from fastapi import (  # noqa: E402
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from services.knowledge_service import search_knowledge
from services.knowledge_admin_service import (
    list_records,
    add_record,
    update_record,
    delete_record,
)

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse
from schemas.chat import ChatRequest, ChatResponse
from schemas.auth import (
    FarmerRegisterRequest,
    FarmerLoginRequest,
    FarmerResponse,
    AuthResponse,
    AuthUserResponse,
)
from services.auth_service import (
    register_farmer,
    login_farmer,
    get_farmer_from_token,
    logout_farmer,
)
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from langdetect import DetectorFactory, LangDetectException, detect  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from database import (  # noqa: E402
    check_db,
    clear_session_history,
    ensure_session_owner,
    get_connection,
    get_session_history,
    init_db,
    prune_old_history,
    save_chat_turn,
    save_feedback,
    get_feedback_stats,
    save_messages,
    get_admin_farmer_list,
    get_admin_query_list,
    get_admin_token_usage,
)
from schemas.chat import ChatRequest, ChatResponse  # noqa: E402
from services.intent_service import analyze_intent  # noqa: E402
from services.knowledge_service import search_knowledge  # noqa: E402
from services.mandi_service import get_mandi_prices  # noqa: E402
from services.session_service import (  # noqa: E402
    add_message,
    delete_session,
    get_or_create_session,
)
from services.speech_service import transcribe_audio, warmup as warm_up_speech  # noqa: E402
from services.quality_service import (  # noqa: E402
    VALID_STATUSES,
    init_quality_table,
    list_flags,
    quality_stats,
    resolve_flag,
    save_flag,
    score_response,
)
from services.scheme_service import (  # noqa: E402
    FarmerProfile,
    evaluate_schemes,
    list_schemes,
)
from services.weather_service import get_weather  # noqa: E402

_startup_log = logging.getLogger("smart_farming.api")

try:
    from services.llm_service import (  # noqa: E402
        generate_grounded_answer,
        is_configured as llm_is_configured,
    )
except Exception:  # e.g. google-genai missing; answers fall back to stored passages
    generate_grounded_answer = None

    def llm_is_configured() -> bool:
        return False

    _startup_log.warning("llm_service could not be imported.", exc_info=True)

try:
    from services import ner_service  # noqa: E402
except Exception:  # transformers missing; the dictionary matcher still works
    ner_service = None

try:
    from services.knowledge_service import ensure_index  # noqa: E402
except ImportError:  # older knowledge_service without the startup helper
    ensure_index = None

try:
    from services.translation_service import translate_from_english  # noqa: E402
except Exception:  # e.g. google-genai not installed; the API must still start
    translate_from_english = None
    logging.getLogger("smart_farming.api").warning(
        "translation_service could not be imported; Hindi/Kannada answers "
        "will only use translations stored in the knowledge base.",
        exc_info=True,
    )

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("smart_farming.api")

# HTTP client libraries log every request URL at INFO level, which floods the
# console (Hugging Face downloads) and could expose API keys in query strings.
for _noisy in ("httpx", "httpx2", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# langdetect is random by default; a fixed seed makes results repeatable.
DetectorFactory.seed = 0


# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()
WARMUP_ON_STARTUP = os.getenv("WARMUP_ON_STARTUP", "false").strip().lower() == "true"

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]

try:
    # 0 (default) keeps history forever; e.g. 30 deletes messages older than 30 days at startup.
    HISTORY_RETENTION_DAYS = int(os.getenv("HISTORY_RETENTION_DAYS", "0"))
except ValueError:
    HISTORY_RETENTION_DAYS = 0

# Message length/whitespace rules live in schemas.chat.ChatRequest.
# Re-index agriculture.json at startup when it is new or has changed.
INDEX_ON_STARTUP = os.getenv("INDEX_ON_STARTUP", "true").strip().lower() == "true"

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024  # Includes multipart overhead for 10 MB audio uploads.
# 128 matches the limit enforced by database.py.
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_farmer(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
        )

    farmer = get_farmer_from_token(credentials.credentials)

    if farmer is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired authentication token.",
        )

    return farmer
# -------------------------------------------------------------------
# Application lifecycle and middleware
# -------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()

    try:
        await run_in_threadpool(init_quality_table)
    except Exception:
        logger.exception("Could not create the review-queue table.")

    if INDEX_ON_STARTUP and ensure_index is not None:
        try:
            stats = await run_in_threadpool(ensure_index)
            if stats:
                logger.info("Knowledge base indexed: %s", stats)
        except Exception:
            logger.exception("Knowledge base indexing failed; search may be out of date.")

    if HISTORY_RETENTION_DAYS > 0:
        try:
            await run_in_threadpool(prune_old_history, HISTORY_RETENTION_DAYS)
        except Exception:
            logger.exception("Pruning old chat history failed.")

    if WARMUP_ON_STARTUP:
        # Load the models now so the first farmer does not wait for them.
        try:
            await run_in_threadpool(analyze_intent, "wheat fertilizer", "en")
            logger.info("Intent model warmed up.")
        except Exception:
            logger.exception("Intent model warm-up failed; continuing without it.")

        # The first run downloads the speech models (hundreds of MB). Doing it
        # here keeps the first microphone request from timing out.
        try:
            await run_in_threadpool(warm_up_speech)
            logger.info("Speech models warmed up.")
        except Exception:
            logger.exception("Speech model warm-up failed; continuing without it.")

    yield


app = FastAPI(
    title="Smart Farming Assistant API",
    description="Backend for multilingual agricultural guidance",
    version="1.0.0",
    lifespan=lifespan,
)

limiter = Limiter(key_func=get_remote_address)

app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    _rate_limit_exceeded_handler,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
)


@app.middleware("http")
async def security_and_request_size_middleware(request: Request, call_next):
    """Apply baseline browser security headers and reject oversized bodies early."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body is too large."},
                )
        except ValueError:
            return JSONResponse(
                status_code=400, content={"detail": "Invalid Content-Length header."}
            )

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
    response.headers.setdefault("Cache-Control", "no-store")
    if APP_ENV == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# -------------------------------------------------------------------
# Localised fallback messages (en / hi / kn)
# NOTE: have a native speaker review the Hindi and Kannada wording.
# -------------------------------------------------------------------

MESSAGES = {
    "ask_city": {
        "en": "Please specify your city or town so I can fetch the current weather forecast.",
        "hi": "कृपया अपना शहर या कस्बा बताएं, ताकि मैं मौजूदा मौसम का पूर्वानुमान बता सकूं।",
        "kn": "ದಯವಿಟ್ಟು ನಿಮ್ಮ ನಗರ ಅಥವಾ ಪಟ್ಟಣದ ಹೆಸರನ್ನು ತಿಳಿಸಿ, ಇದರಿಂದ ನಾನು ಪ್ರಸ್ತುತ ಹವಾಮಾನ ಮುನ್ಸೂಚನೆಯನ್ನು ನೀಡಬಲ್ಲೆ.",
    },
    "weather_unavailable": {
        "en": "Live weather data is temporarily unavailable. Please try again shortly.",
        "hi": "लाइव मौसम की जानकारी अभी उपलब्ध नहीं है। कृपया थोड़ी देर बाद फिर कोशिश करें।",
        "kn": "ನೇರ ಹವಾಮಾನ ಮಾಹಿತಿ ಸದ್ಯಕ್ಕೆ ಲಭ್ಯವಿಲ್ಲ. ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
    },
    "ask_crop": {
        "en": "Which crop's market price would you like to check? For example, wheat, paddy, potato, or onion.",
        "hi": "आप किस फसल का मंडी भाव जानना चाहते हैं? जैसे गेहूं, धान, आलू या प्याज।",
        "kn": "ನೀವು ಯಾವ ಬೆಳೆಯ ಮಾರುಕಟ್ಟೆ ಬೆಲೆಯನ್ನು ತಿಳಿಯಲು ಬಯಸುತ್ತೀರಿ? ಉದಾಹರಣೆಗೆ ಗೋಧಿ, ಭತ್ತ, ಆಲೂಗಡ್ಡೆ ಅಥವಾ ಈರುಳ್ಳಿ.",
    },
    "market_unavailable": {
        "en": "Live mandi prices are temporarily unavailable. Please try again shortly.",
        "hi": "मंडी भाव की लाइव जानकारी अभी उपलब्ध नहीं है। कृपया थोड़ी देर बाद फिर कोशिश करें।",
        "kn": "ಮಂಡಿ ಬೆಲೆಗಳ ನೇರ ಮಾಹಿತಿ ಸದ್ಯಕ್ಕೆ ಲಭ್ಯವಿಲ್ಲ. ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
    },
    "no_market_records": {
        "en": "No matching mandi price records were found for {commodity}{place}. Try another crop or state.",
        "hi": "{commodity}{place} के लिए मंडी भाव का कोई रिकॉर्ड नहीं मिला। कृपया दूसरी फसल या राज्य आज़माएं।",
        "kn": "{commodity}{place} ಬಗ್ಗೆ ಯಾವುದೇ ಮಂಡಿ ಬೆಲೆ ದಾಖಲೆಗಳು ಸಿಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಬೇರೆ ಬೆಳೆ ಅಥವಾ ರಾಜ್ಯವನ್ನು ಪ್ರಯತ್ನಿಸಿ.",
    },
    "no_answer": {
        "en": "I don't have verified information on this yet. For help with your question, you can call the Kisan Call Centre at 1800-180-1551.",
        "hi": "इस विषय पर अभी मेरे पास सत्यापित जानकारी नहीं है। अपने प्रश्न के लिए आप किसान कॉल सेंटर 1800-180-1551 पर संपर्क कर सकते हैं।",
        "kn": "ಈ ವಿಷಯದ ಬಗ್ಗೆ ಸದ್ಯಕ್ಕೆ ನನ್ನ ಬಳಿ ಪರಿಶೀಲಿತ ಮಾಹಿತಿ ಇಲ್ಲ. ನಿಮ್ಮ ಪ್ರಶ್ನೆಗೆ ಸಹಾಯಕ್ಕಾಗಿ ನೀವು ಕಿಸಾನ್ ಕಾಲ್ ಸೆಂಟರ್ ಅನ್ನು 1800-180-1551 ಸಂಖ್ಯೆಯಲ್ಲಿ ಸಂಪರ್ಕಿಸಬಹುದು.",
    },
    "scheme_hint": {
        "en": "To check whether you may be eligible, use the Scheme Eligibility Checker on this page.",
        "hi": "आप पात्र हैं या नहीं, यह जानने के लिए इस पेज पर 'योजना पात्रता जाँच' का उपयोग करें।",
        "kn": "ನೀವು ಅರ್ಹರೇ ಎಂದು ತಿಳಿಯಲು ಈ ಪುಟದಲ್ಲಿರುವ 'ಯೋಜನೆ ಅರ್ಹತಾ ಪರಿಶೀಲನೆ' ಬಳಸಿ.",
    },
    "pest_advice": {
        "en": "Please tell me the crop, its growth stage and the symptoms you see. Do not apply any pesticide until the problem has been identified.",
        "hi": "कृपया फसल, उसकी अवस्था और दिख रहे लक्षण बताएं। समस्या की पहचान होने तक कोई कीटनाशक न डालें।",
        "kn": "ದಯವಿಟ್ಟು ಬೆಳೆ, ಅದರ ಬೆಳವಣಿಗೆಯ ಹಂತ ಮತ್ತು ಕಾಣುತ್ತಿರುವ ಲಕ್ಷಣಗಳನ್ನು ತಿಳಿಸಿ. ಸಮಸ್ಯೆಯನ್ನು ಗುರುತಿಸುವವರೆಗೆ ಯಾವುದೇ ಕೀಟನಾಶಕ ಬಳಸಬೇಡಿ.",
    },
    "server_error": {
        "en": "Something went wrong while processing your request. Please try again.",
        "hi": "आपके अनुरोध को संसाधित करते समय कुछ गलत हो गया। कृपया फिर से कोशिश करें।",
        "kn": "ನಿಮ್ಮ ವಿನಂತಿಯನ್ನು ಪ್ರಕ್ರಿಯೆಗೊಳಿಸುವಾಗ ಏನೋ ತಪ್ಪಾಗಿದೆ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
    },
}

PLACE_FORMATS = {"en": " in {state}", "hi": " ({state})", "kn": " ({state})"}


def msg(key: str, language: str, **kwargs) -> str:
    table = MESSAGES[key]
    text = table.get(language) or table["en"]
    return text.format(**kwargs) if kwargs else text


# -------------------------------------------------------------------
# Text matching helpers
# -------------------------------------------------------------------

_ASCII_TERM = re.compile(r"[a-z0-9 .'\-]+")


def find_first(text: str, terms) -> int | None:
    """Position of the earliest matching term in an already-casefolded text.

    English terms must match as whole words (so "rice" never matches inside
    "price", and "rain" never matches inside "grain"); Indic terms match as
    phrases.
    """
    best = None

    for term in terms:
        term = term.casefold()

        if _ASCII_TERM.fullmatch(term):
            found = re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text)
            position = found.start() if found else None
        else:
            index = text.find(term)
            position = index if index >= 0 else None

        if position is not None and (best is None or position < best):
            best = position

    return best


# -------------------------------------------------------------------
# Weather: intent detection and city extraction
# -------------------------------------------------------------------

WEATHER_KEYWORDS = (
    "weather", "temperature", "forecast", "rain", "rainfall",
    "humidity", "wind speed",
    "मौसम", "बारिश", "वर्षा", "तापमान",
    "ಹವಾಮಾನ", "ಮಳೆ", "ತಾಪಮಾನ",
)

# city -> (state as named in AGMARKNET, aliases)
CITIES = {
    "Meerut": ("Uttar Pradesh", ["meerut", "मेरठ", "ಮೀರತ್"]),
    "Delhi": ("NCT of Delhi", ["delhi", "दिल्ली", "ದೆಹಲಿ"]),
    "Noida": ("Uttar Pradesh", ["noida", "नोएडा", "ನೋಯ್ಡಾ"]),
    "Lucknow": ("Uttar Pradesh", ["lucknow", "लखनऊ", "ಲಕ್ನೋ"]),
    "Kanpur": ("Uttar Pradesh", ["kanpur", "कानपुर", "ಕಾನ್ಪುರ"]),
    "Bengaluru": ("Karnataka", ["bengaluru", "bangalore", "बेंगलुरु", "बैंगलोर", "ಬೆಂಗಳೂರು"]),
    "Mysuru": ("Karnataka", ["mysuru", "mysore", "मैसूर", "ಮೈಸೂರು"]),
    "Mumbai": ("Maharashtra", ["mumbai", "bombay", "मुंबई", "ಮುಂಬೈ"]),
    "Pune": ("Maharashtra", ["pune", "पुणे", "ಪುಣೆ"]),
    "Chennai": ("Tamil Nadu", ["chennai", "madras", "चेन्नई", "ಚೆನ್ನೈ"]),
    "Hyderabad": ("Telangana", ["hyderabad", "हैदराबाद", "ಹೈದರಾಬಾದ್"]),
    "Kolkata": ("West Bengal", ["kolkata", "calcutta", "कोलकाता", "ಕೋಲ್ಕತ್ತಾ"]),
}

# Words that follow "in/for/at" but are not place names.
NON_CITY_FIRST_WORDS = {
    "my", "the", "this", "our", "your", "a", "an", "these", "those",
    "next", "last", "field", "farm", "area", "general",
}


def is_weather_query(message: str) -> bool:
    return find_first(message.casefold(), WEATHER_KEYWORDS) is not None


def detect_city(message: str) -> str | None:
    """Return the first known city mentioned (English, Hindi or Kannada)."""
    text = message.casefold()
    best_city, best_pos = None, None

    for city, (_, aliases) in CITIES.items():
        position = find_first(text, aliases)
        if position is not None and (best_pos is None or position < best_pos):
            best_city, best_pos = city, position

    return best_city


def extract_weather_city(message: str) -> str | None:
    """Extract a city from English, Hindi or Kannada weather questions."""
    known = detect_city(message)
    if known:
        return known

    # English patterns such as "weather in Agra tomorrow" / "forecast for Varanasi".
    match = re.search(
        r" (?:in|for|at|of)\s+"
        r"([A-Za-z][A-Za-z .'-]{1,50}?)"
        r"(?=\s+(?:today|tomorrow|tonight|now|currently|"
        r"this week|next week) |[?.!,]|$)",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None

    candidate = match.group(1).strip().rstrip(" .,'")
    if not candidate:
        return None

    first_word = candidate.split()[0].casefold()
    if first_word in NON_CITY_FIRST_WORDS:
        return None

    # Reject things like "forecast for wheat" or "rain in the forecast".
    if is_weather_query(candidate) or extract_commodity(candidate):
        return None

    return candidate


# -------------------------------------------------------------------
# Market prices: intent detection, commodity and state extraction
# -------------------------------------------------------------------

MARKET_KEYWORDS = (
    "mandi", "market price", "market prices", "crop price",
    "commodity price", "selling price", "current price",
    "latest price", "today price", "price today",
    "price of", "prices of",
    "wheat price", "wheat prices",
    "rice price", "rice prices",
    "paddy price", "paddy prices",
    "latest paddy", "today paddy",
    "latest rice", "today rice",
    "गेहूं का भाव", "धान का भाव", "चावल का भाव",
    "आज का भाव", "आज का मंडी भाव", "मंडी भाव",
    "ಮಾರುಕಟ್ಟೆ ಬೆಲೆ", "ಮಂಡಿ ಬೆಲೆ", "ಇಂದಿನ ಬೆಲೆ",
)

# AGMARKNET commodity name -> aliases (English / Hindi / Kannada).
COMMODITY_ALIASES = {
    "Wheat": ["wheat", "गेहूं", "गेहूँ", "गेंहूँ", "ಗೋಧಿ"],
    "Paddy": ["paddy", "rice", "धान", "भात", "ಭತ್ತ", "ಅಕ್ಕಿ"],
    "Potato": ["potato", "आलू", "ಆಲೂಗಡ್ಡೆ"],
    "Onion": ["onion", "प्याज", "प्याज़", "ಈರುಳ್ಳಿ"],
    "Tomato": ["tomato", "टमाटर", "ಟೊಮೇಟೊ", "ಟೊಮ್ಯಾಟೊ"],
    "Mustard": ["mustard", "सरसों", "सरसो", "ಸಾಸಿವೆ"],
    "Maize": ["maize", "corn", "मक्का", "ಮೆಕ್ಕೆಜೋಳ"],
    "Soyabean": ["soybean", "soyabean", "सोयाबीन", "ಸೋಯಾಬೀನ್"],
    "Cotton": ["cotton", "कपास", "ಹತ್ತಿ"],
    "Pumpkin": ["pumpkin", "कद्दू", "ಕುಂಬಳಕಾಯಿ"],
}

# Crop keys produced by the intent analyzer -> commodity names above.
CROP_TO_COMMODITY = {
    "rice": "Paddy",
    "wheat": "Wheat",
    "maize": "Maize",
    "cotton": "Cotton",
    "tomato": "Tomato",
    "potato": "Potato",
    "onion": "Onion",
    "soybean": "Soyabean",
}

# AGMARKNET state name -> aliases (the names are spelled the way the API spells them).
STATE_ALIASES = {
    "Andaman and Nicobar": ["andaman and nicobar", "andaman"],
    "Andhra Pradesh": ["andhra pradesh", "आंध्र प्रदेश"],
    "Assam": ["assam", "असम"],
    "Bihar": ["bihar", "बिहार", "ಬಿಹಾರ"],
    "Chandigarh": ["chandigarh", "चंडीगढ़"],
    "Chattisgarh": ["chhattisgarh", "chattisgarh", "छत्तीसगढ़", "छत्तीसगढ"],
    "Goa": ["goa", "गोवा"],
    "Gujarat": ["gujarat", "गुजरात", "ಗುಜರಾತ್"],
    "Haryana": ["haryana", "हरियाणा", "ಹರಿಯಾಣ"],
    "Himachal Pradesh": ["himachal pradesh", "हिमाचल प्रदेश"],
    "Jammu and Kashmir": ["jammu and kashmir", "jammu & kashmir", "जम्मू और कश्मीर"],
    "Jharkhand": ["jharkhand", "झारखंड"],
    "Karnataka": ["karnataka", "कर्नाटक", "ಕರ್ನಾಟಕ"],
    "Kerala": ["kerala", "केरल"],
    "Madhya Pradesh": ["madhya pradesh", "मध्य प्रदेश", "ಮಧ್ಯ ಪ್ರದೇಶ"],
    "Maharashtra": ["maharashtra", "महाराष्ट्र", "ಮಹಾರಾಷ್ಟ್ರ"],
    "Manipur": ["manipur"],
    "Meghalaya": ["meghalaya"],
    "Mizoram": ["mizoram"],
    "NCT of Delhi": ["nct of delhi"],
    "Nagaland": ["nagaland"],
    "Odisha": ["odisha", "orissa", "ओडिशा"],
    "Pondicherry": ["pondicherry", "puducherry"],
    "Punjab": ["punjab", "पंजाब", "ಪಂಜಾಬ್"],
    "Rajasthan": ["rajasthan", "राजस्थान", "ರಾಜಸ್ಥಾನ"],
    "Sikkim": ["sikkim"],
    "Tamil Nadu": ["tamil nadu", "तमिलनाडु", "तमिल नाडु", "ತಮಿಳುನಾಡು"],
    "Telangana": ["telangana", "तेलंगाना"],
    "Tripura": ["tripura"],
    "Uttar Pradesh": ["uttar pradesh", "उत्तर प्रदेश", "ಉತ್ತರ ಪ್ರದೇಶ"],
    "Uttrakhand": ["uttarakhand", "uttrakhand", "उत्तराखंड"],
    "West Bengal": ["west bengal", "पश्चिम बंगाल"],
}


def is_market_query(message: str) -> bool:
    return find_first(message.casefold(), MARKET_KEYWORDS) is not None


def extract_commodity(text: str) -> str | None:
    """Return the AGMARKNET commodity mentioned first in the text."""
    lowered = text.casefold()
    best_name, best_pos = None, None

    for name, aliases in COMMODITY_ALIASES.items():
        position = find_first(lowered, aliases)
        if position is not None and (best_pos is None or position < best_pos):
            best_name, best_pos = name, position

    return best_name


def extract_market_state(message: str) -> str | None:
    lowered = message.casefold()
    best_state, best_pos = None, None

    for state, aliases in STATE_ALIASES.items():
        position = find_first(lowered, aliases)
        if position is not None and (best_pos is None or position < best_pos):
            best_state, best_pos = state, position

    return best_state


# -------------------------------------------------------------------
# Routing: weather / market prices / knowledge base
# -------------------------------------------------------------------

# Topics answered from the knowledge base. If one of these is mentioned
# explicitly it wins over live-data routing, so "standing water after heavy
# rain" is a waterlogging question, not a weather lookup, and "price of urea"
# is a fertilizer question, not a mandi lookup.
KNOWLEDGE_TOPIC_PRIORITY = (
    "waterlogging", "pest_disease", "fertilizer", "soil_health",
    "crop_rotation", "yield_management", "post_harvest",
    "expert_support", "government_schemes",
)
NON_KNOWLEDGE_INTENTS = {"weather", "market_prices", "other"}

# Knowledge questions go to a specialised sub-agent by topic:
#   crop_advisory (default) | schemes | pest_control
# Live-data questions go to weather and market_prices.
SUB_AGENTS = {"government_schemes": "schemes", "pest_disease": "pest_control"}


def resolve_route(message: str, analysis: dict) -> tuple[str, str]:
    """Decide how to answer. Returns (route, intent)."""
    model_intent = analysis["intent"]
    topics = set(analysis.get("entities", {}).get("topics", []))
    specific = [t for t in KNOWLEDGE_TOPIC_PRIORITY if t in topics]

    if specific:
        if model_intent in NON_KNOWLEDGE_INTENTS:
            return "knowledge", specific[0]
        return "knowledge", model_intent

    wants_weather = model_intent == "weather" or is_weather_query(message)
    wants_market = model_intent == "market_prices" or is_market_query(message)

    # Never hit both live services for one message.
    if wants_weather and wants_market:
        if model_intent == "market_prices":
            wants_weather = False
        else:
            wants_market = False

    if wants_weather:
        return "weather", "weather"
    if wants_market:
        return "market_prices", "market_prices"

    return "knowledge", model_intent


# -------------------------------------------------------------------
# Route handlers used by the chat endpoint
# -------------------------------------------------------------------

@dataclass
class Outcome:
    """What a route handler produced, including signals used for quality scoring."""

    answer: str
    weather: dict | None = None
    market: dict | None = None
    records: list[dict] = field(default_factory=list)
    # answer | clarification | unavailable | fallback
    kind: str = "answer"
    grounded: bool | None = None
    machine_translated: bool = False


async def handle_weather(
    message: str, language: str, session, extra_locations: list[str]
) -> Outcome:
    city = (
        extract_weather_city(message)
        or (extra_locations[0] if extra_locations else None)
        or session.context.get("city")
    )

    if not city:
        return Outcome(msg("ask_city", language), kind="clarification")

    try:
        weather_data = await get_weather(city)
    except ValueError as exc:
        return Outcome(str(exc), kind="unavailable")
    except RuntimeError as exc:
        logger.warning("Weather API error: %s", exc)
        return Outcome(msg("weather_unavailable", language), kind="unavailable")

    session.context["city"] = city

    current = weather_data.get("current", {})
    daily_forecast = weather_data.get("daily_forecast", [])
    units = weather_data.get("units", {})
    current_units = units.get("current", {})

    location = weather_data.get("location", {})
    city_name = location.get("name", city)
    region = location.get("region", "")

    answer_lines = [
    f"Current weather in {city_name}, {region}:",
    f"Temperature: {current.get('temperature_2m', 'N/A')} "
    f"{current_units.get('temperature_2m', '°C')}",
    f"Feels like: {current.get('apparent_temperature', 'N/A')} "
    f"{current_units.get('apparent_temperature', '°C')}",
    f"Humidity: {current.get('relative_humidity_2m', 'N/A')}%",
    f"Rainfall: {current.get('precipitation', 'N/A')} mm",
    f"Wind speed: {current.get('wind_speed_10m', 'N/A')} km/h",
    f"Condition: {current.get('weather_description', 'Not available')}",
    "",
    "3-Day Weather Forecast:",
    ]
    for day in daily_forecast:
        answer_lines.append(
            f"{day.get('date')}: "
        f"{day.get('weather_description', 'Not available')}, "
        f"Minimum: {day.get('temperature_2m_min', 'N/A')}°C, "
        f"Maximum: {day.get('temperature_2m_max', 'N/A')}°C"
    )

    answer = "".join(answer_lines)

    return Outcome(answer, weather=weather_data)


async def handle_market(message: str, language: str, crop: str | None) -> Outcome:
    # Fall back to the crop discussed earlier ("and what is the price today?").
    commodity = extract_commodity(message) or CROP_TO_COMMODITY.get(crop or "")

    if not commodity:
        return Outcome(msg("ask_crop", language), kind="clarification")

    # A known city implies its state ("wheat price in Kanpur").
    city = detect_city(message)
    state = extract_market_state(message) or (CITIES[city][0] if city else None)

    try:
        market_data = await get_mandi_prices(
            commodity=commodity,
            state=state,
            district=None,
            limit=5,
        )
    except ValueError as exc:
        return Outcome(str(exc), kind="unavailable")
    except RuntimeError as exc:
        logger.warning("Mandi API error: %s", exc)
        return Outcome(msg("market_unavailable", language), kind="unavailable")

    records = market_data.get("records", []) if isinstance(market_data, dict) else []

    if not records:
        place = PLACE_FORMATS.get(language, PLACE_FORMATS["en"]).format(state=state) if state else ""
        return Outcome(
            msg("no_market_records", language, commodity=commodity, place=place),
            kind="clarification",  # nothing wrong with the service; ask for another crop/state
        )

        # Format live mandi records into a farmer-friendly response.
    latest_date = market_data.get("latest_arrival_date", "Not available")
    currency_unit = "₹/quintal"

    lines = [
        f"Latest available {commodity} market prices in {state or 'India'}:",
        f"Arrival date: {latest_date}",
        "",
    ]

    for record in records:
        lines.append(
            f"{record.get('district', 'Unknown district')} "
            f"({record.get('market', 'Unknown market')}): "
            f"Modal price {currency_unit} "
            f"{record.get('modal_price', 'N/A')}. "
            f"Minimum: ₹{record.get('min_price', 'N/A')}, "
            f"Maximum: ₹{record.get('max_price', 'N/A')}. "
            f"Variety: {record.get('variety', 'Not specified')}."
        )

    lines.append(
        "Prices vary by market, variety and quality. "
        "Please confirm the latest rate with your local mandi."
    )

    return Outcome("".join(lines), market=market_data)


async def localize_record(record: dict, language: str) -> tuple[str, bool]:
    """Hindi/Kannada text for a knowledge record: (text, was_machine_translated).

    Uses the translation stored in agriculture.json when there is one;
    otherwise translates the English passage with translation_service.
    Returns ("", False) if neither is possible.
    """
    stored = coerce_answer((record.get("translations") or {}).get(language)).strip()
    if stored:
        return stored, False

    english = str(record.get("content", "")).strip()
    if not english or translate_from_english is None:
        return "", False

    try:
        # Network call to the translation API: keep it off the event loop.
        translated = await run_in_threadpool(translate_from_english, english, language)
        return coerce_answer(translated).strip(), True
    except Exception:
        logger.warning(
            "Could not translate record %s to %s.",
            record.get("id"), language, exc_info=True,
        )
        return "", False


async def handle_knowledge(
    message: str, language: str, intent: str, crop: str | None, context: str = ""
) -> Outcome:
    """RAG answer: retrieve passages, then let the LLM write a grounded reply."""
    results: list[dict] = []

    # "other" has no matching topic; skip straight to the fallback message
    # instead of returning loosely related passages.
    if intent != "other":
        try:
            # Embedding + vector search is blocking work: keep it off the event loop.
            results = await run_in_threadpool(
                search_knowledge,
                query=message,
                crop=crop,
                topic=None if intent == "crop_care" else intent,
                limit=3,
                state=extract_market_state(message),
            )
        except Exception:
            logger.exception("Knowledge search failed; using fallback response.")

    if not results:
        return Outcome(msg("no_answer", language), kind="fallback", grounded=False)

    # 1) Preferred path: the LLM writes the answer using ONLY the retrieved passages.
    if generate_grounded_answer is not None and llm_is_configured():
        passages = [
            {
                "text": r.get("content", ""),
                "source": r.get("source_name", ""),
                "note": r.get("scope_note", ""),
            }
            for r in results
            if str(r.get("content", "")).strip()
        ]
        try:
            generated = await run_in_threadpool(
                generate_grounded_answer, message, passages, language, context
            )
            if generated.grounded:
                return Outcome(generated.text, records=results, grounded=True)

            # The passages do not answer this question: say so instead of guessing.
            return Outcome(msg("no_answer", language), kind="fallback", grounded=False)
        except Exception:
            logger.warning("LLM answer failed; using stored passages.", exc_info=True)

    # 2) Fallback when the LLM is unavailable: show the stored passage.
    selected = results[0]
    used: list[dict] = []
    answer = ""
    machine = False

    if language == "en":
        if intent == "crop_care":
            used = [r for r in results if str(r.get("content", "")).strip()]
            answer = "".join(str(r["content"]).strip() for r in used)
        else:
            content = str(selected.get("content", "")).strip()
            if content:
                answer, used = content, [selected]
    else:
        answer, machine = await localize_record(selected, language)
        if answer:
            used = [selected]

    if not answer:
        return Outcome(msg("no_answer", language), kind="fallback", grounded=False)

    return Outcome(answer, records=used, machine_translated=machine)


async def run_route(
    route: str,
    message: str,
    language: str,
    intent: str,
    crop: str | None,
    session,
    locations: list[str],
) -> Outcome:
    """Send the question to the right specialised sub-agent."""
    if route == "weather":
        return await handle_weather(message, language, session, locations)

    if route == "market_prices":
        return await handle_market(message, language, crop)

    context_parts = []
    if crop:
        context_parts.append(f"Crop: {crop}.")
    if locations:
        context_parts.append(f"Location: {', '.join(locations)}.")

    outcome = await handle_knowledge(message, language, intent, crop, " ".join(context_parts))

    # Scheme lookup points to the eligibility checker; pest control adds the
    # safety rule from the knowledge base (never guess a pesticide).
    if route == "schemes":
        outcome.answer = f"{outcome.answer}{msg('scheme_hint', language)}"
    elif route == "pest_control":
        outcome.answer = f"{outcome.answer}{msg('pest_advice', language)}"

    return outcome


def coerce_answer(value) -> str:
    """Return answer text no matter what a response function hands back.

    Helper functions may return plain text or a dict such as
    {"answer": "...", "confidence": 1.0}; ChatResponse and the session store
    both require a string.
    """
    if isinstance(value, str):
        return value

    if isinstance(value, dict):
        for key in ("answer", "text", "message", "response"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate

    return str(value) if value is not None else ""


def build_sources(
    weather_data: dict | None,
    market_data: dict | None,
    used_records: list[dict],
) -> list[dict]:
    """Collect de-duplicated, traceable sources used in the answer."""

    sources: list[dict] = []
    seen: set[tuple] = set()

    def add(
        name: str | None,
        url: str | None,
        **metadata,
    ) -> None:
        if not name:
            return

        key = (
            name,
            url,
            metadata.get("page_start"),
            metadata.get("page_end"),
            metadata.get("source_type"),
        )

        if key in seen:
            return

        seen.add(key)

        source = {
            "source_name": name,
            "source_url": url,
        }

        for field in (
            "page_start",
            "page_end",
            "source_file",
            "source_type",
            "verified",
            "review_status",
        ):
            if metadata.get(field) is not None:
                source[field] = metadata[field]

        sources.append(source)

    if weather_data:
        add(
            "Open-Meteo",
            "https://open-meteo.com/",
            source_type="live_weather",
            verified=True,
            review_status="not_applicable",
        )

    if market_data:
        add(
            "AGMARKNET / data.gov.in",
            "https://www.data.gov.in/",
            source_type="live_market",
            verified=True,
            review_status="not_applicable",
        )

    for record in used_records:
        add(
            record.get("source_name"),
            record.get("source_url") or None,
            page_start=record.get("page_start"),
            page_end=record.get("page_end"),
            source_file=record.get("source_file"),
            source_type=record.get("_source_type"),
            verified=bool(record.get("verified", False)),
            review_status=record.get("review_status"),
        )

    return sources


async def persist_turn(
    session_id: str,
    language: str,
    intent: str,
    user_message: str,
    assistant_message: str,
    user_id: int,
) -> int | None:
    """Save one complete chat turn and return the assistant message ID."""
    try:
        return await run_in_threadpool(
            save_chat_turn,
            session_id,
            language,
            intent,
            user_message,
            assistant_message,
            user_id,
        )
    except Exception:
        logger.exception("Could not save chat turn.")
        return None


async def persist_flag(**fields) -> None:
    """Queue a low-confidence answer for human review (never breaks the chat)."""
    try:
        await run_in_threadpool(save_flag, **fields)
    except Exception:
        logger.exception("Could not queue the answer for review.")


def validate_session_id(session_id: str) -> None:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id.")


# -------------------------------------------------------------------
# Access control for bulk history endpoints
# -------------------------------------------------------------------

def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """Require a configured, constant-time-checked admin API key in every env.

    Development mode is not an authentication bypass: configure ADMIN_API_KEY
    in the backend environment and keep it out of source control.
    """
    if not ADMIN_API_KEY:
        logger.error("ADMIN_API_KEY is not configured; refusing admin request.")
        raise HTTPException(
            status_code=503,
            detail="Admin API is not configured. Set ADMIN_API_KEY on the backend.",
        )

    supplied = (x_admin_key or "").encode("utf-8")
    expected = ADMIN_API_KEY.encode("utf-8")
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Admin access required.")


# -------------------------------------------------------------------
# Farmer authentication
# -------------------------------------------------------------------

@app.post(
    "/api/v1/auth/register",
    response_model=AuthResponse,
)
@limiter.limit("5/minute")
async def farmer_register(
    request: Request,
    data: FarmerRegisterRequest,
):
    try:
        farmer = await run_in_threadpool(
            register_farmer,
            data.name,
            str(data.email),
            data.password,
        )

        return {
            "status": "success",
            "message": "Farmer registered successfully.",
            "user": farmer,
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@app.post("/api/v1/auth/login")
@limiter.limit("10/minute")
async def farmer_login(
    request: Request,
    data: FarmerLoginRequest,
):
    try:
        result = await run_in_threadpool(
            login_farmer,
            str(data.email),
            data.password,
        )
        return {
            "status": "success",
            **result,
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
        ) from exc


@app.get(
    "/api/v1/auth/me",
    response_model=AuthUserResponse,
)
async def farmer_me(
    farmer: dict = Depends(get_current_farmer),
):
    return {
        "authenticated": True,
        "user": farmer,
    }


@app.post("/api/v1/auth/logout")
async def farmer_logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    farmer: dict = Depends(get_current_farmer),
):
    revoked = await run_in_threadpool(
        logout_farmer,
        credentials.credentials,
    )

    if not revoked:
        raise HTTPException(
            status_code=400,
            detail="Could not end the authentication session.",
        )

    return {
        "status": "success",
        "message": "Logged out successfully.",
    }




# -------------------------------------------------------------------
# Root and health endpoints
# -------------------------------------------------------------------



@app.get("/")
def home():
    return {"message": "Smart Farming Assistant API is running"}


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "smart-farming-backend"}


@app.get("/health/ready")
async def readiness_check():
    """Readiness probe: confirms the database is reachable."""
    if not await run_in_threadpool(check_db):
        raise HTTPException(status_code=503, detail="Database unavailable.")

    return {"status": "ready"}


# -------------------------------------------------------------------
# Weather API
# -------------------------------------------------------------------

@app.get("/api/v1/weather")
async def weather(city: str = Query(..., min_length=2, max_length=100)):
    try:
        return await get_weather(city.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.warning("Weather API error: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# -------------------------------------------------------------------
# Agricultural market prices API
# -------------------------------------------------------------------

@app.get("/api/v1/market-prices")
async def market_prices(
    commodity: str = Query(..., min_length=1, max_length=100),
    state: str | None = Query(None, max_length=100),
    district: str | None = Query(None, max_length=100),
    limit: int = Query(10, ge=1, le=100),
):
    try:
        return await get_mandi_prices(
            commodity=commodity.strip(),
            state=state.strip() if state else None,
            district=district.strip() if district else None,
            limit=limit,
        )
    except ValueError as exc:
        # e.g. a commodity made only of spaces
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.warning("Mandi API error: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# -------------------------------------------------------------------
# Language detection
# -------------------------------------------------------------------

class DetectLanguageRequest(BaseModel):
    text: str = Field(..., max_length=5000)

class FeedbackRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=128)
    message_id: int = Field(gt=0)
    rating: Literal["up", "down"]


class KnowledgeRecordRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=100)
    crop: str = Field(..., min_length=1, max_length=100)
    topic: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1, max_length=10000)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    language: Literal["en", "hi", "kn"] = "en"
    source_name: str = Field(default="", max_length=300)
    source_url: str = Field(default="", max_length=1000)
    verified: bool = False
    scope_note: str = Field(default="", max_length=2000)
    translations: dict[str, str] = Field(default_factory=dict)
    translation_note: str = Field(default="", max_length=1000)

    model_config = {"extra": "allow"}


def detect_language_code(text: str) -> str:
    """Return en / hi / kn, or "unknown".

    The script is checked first: langdetect is unreliable on short Indic text
    and often labels Hindi as Marathi or Nepali.
    """
    devanagari = sum("ऀ" <= ch <= "ॿ" for ch in text)
    kannada = sum("ಀ" <= ch <= "೿" for ch in text)
    latin = sum(ch.isascii() and ch.isalpha() for ch in text)

    if (devanagari or kannada) and (devanagari + kannada) >= latin:
        return "hi" if devanagari >= kannada else "kn"

    try:
        detected = detect(text)
    except LangDetectException:
        return "unknown"

    return detected if detected in {"hi", "kn", "en"} else "unknown"


@app.post("/api/v1/detect-language")
def detect_language_endpoint(request: DetectLanguageRequest):
    text = request.text.strip()

    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    return {"language": detect_language_code(text)}


# -------------------------------------------------------------------
# Main multilingual chat endpoint
# -------------------------------------------------------------------

@app.post("/api/v1/chat", response_model=ChatResponse)
@limiter.limit("30/minute")
async def chat(request: Request, chat_data: ChatRequest, farmer: dict = Depends(get_current_farmer),):
    started = time.perf_counter()
    language = chat_data.language

    message = chat_data.message  # already stripped and length-checked by ChatRequest

    session_id = chat_data.session_id or str(uuid4())
    validate_session_id(session_id)

    is_owner = await run_in_threadpool(
        ensure_session_owner,
        session_id,
        farmer["id"],
    )

    if not is_owner:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this conversation.",
        )






    session = get_or_create_session(session_id, language)
    session.preferences["language"] = language
    add_message(session, "user", message)

    try:
        # Transformer inference is CPU-bound and blocking: run it in a thread
        # so other requests (weather, mandi, health checks) keep flowing.
        analysis = await run_in_threadpool(analyze_intent, message, language)

        entities = copy.deepcopy(analysis["entities"])

        # Reuse the crop mentioned earlier in the conversation.
        if not entities["crops"]:
            previous_crop = session.context.get("crop")
            if previous_crop:
                entities["crops"] = [previous_crop]

        crop = entities["crops"][0] if entities["crops"] else None

        # Optional Hugging Face NER finds place names the dictionary does not know.
        ner_locations: list[str] = []
        if ner_service is not None:
            ner_locations = await run_in_threadpool(ner_service.extract_locations, message)
            for place in ner_locations:
                if place not in entities["locations"]:
                    entities["locations"].append(place)

        route, intent = resolve_route(message, analysis)
        if route == "knowledge":
            route = SUB_AGENTS.get(intent, "crop_advisory")

        outcome = await run_route(
            route, message, language, intent, crop, session, ner_locations
        )

        answer = coerce_answer(outcome.answer).strip() or msg("server_error", language)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unhandled error while processing chat message.")
        raise HTTPException(
            status_code=500,
            detail=msg("server_error", language),
        ) from exc

    sources = build_sources(outcome.weather, outcome.market, outcome.records)
    source_name = sources[0]["source_name"] if sources else None
    source_url = sources[0]["source_url"] if sources else None

    if crop:
        session.context["crop"] = crop
    session.context["last_intent"] = intent

    # Response quality scoring: low-confidence answers go to the review queue.
    quality = score_response(
        route=route,
        kind=outcome.kind,
        intent_confidence=analysis["confidence"],
        distance=outcome.records[0].get("_distance") if outcome.records else None,
        sources_verified=(
            all(bool(r.get("verified")) for r in outcome.records) if outcome.records else None
        ),
        grounded=outcome.grounded,
        machine_translated=outcome.machine_translated,
        data_stale=bool(outcome.market and outcome.market.get("data_stale")),
    )

    add_message(session, "assistant", answer)
    answer_message_id = await persist_turn(
        session_id, language, intent, message, answer, farmer["id"],
    )

    if quality.needs_review:
        await persist_flag(
            session_id=session_id,
            question=message,
            answer=answer,
            intent=intent,
            language=language,
            route=route,
            result=quality,
        )

    logger.info(
        "chat session=%s route=%s intent=%s lang=%s quality=%.2f review=%s %.0fms",
        session_id,
        route,
        intent,
        language,
        quality.score,
        quality.needs_review,
        (time.perf_counter() - started) * 1000,
    )
    return ChatResponse(
        answer=answer,
        language=language,
        session_id=session_id,
        answer_message_id=answer_message_id,
        intent=intent,
        confidence=analysis["confidence"],
        entities=entities,
        classification_model=analysis["classification_model"],
        # True whenever rules, not the model alone, decided the final intent.
        fallback_used=analysis["fallback_used"] or intent != analysis["intent"],
        source_name=source_name,
        source_url=source_url,
        sources=sources,
        quality_score=quality.score,
        needs_review=quality.needs_review,
    )

# -------------------------------------------------------------------
# Response feedback
# -------------------------------------------------------------------

@app.post("/api/v1/feedback")
async def submit_feedback(request: FeedbackRequest, farmer: dict = Depends(get_current_farmer),):
    validate_session_id(request.session_id)

    try:
        await run_in_threadpool(
            save_feedback,
            request.session_id,
            request.message_id,
            request.rating,
            farmer["id"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    return {
        "status": "success",
        "message": "Feedback saved successfully.",
        "message_id": request.message_id,
        "rating": request.rating,
    }


@app.get("/api/v1/admin/feedback-stats")
async def admin_feedback_stats(
    _: None = Depends(require_admin),
):
    try:
        stats = await run_in_threadpool(get_feedback_stats)
        return {
            "status": "success",
            "data": stats,
        }
    except Exception:
        logger.exception("Could not retrieve feedback statistics.")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve feedback statistics.",
        )


# -------------------------------------------------------------------
# Speech transcription
# -------------------------------------------------------------------

@app.post("/api/v1/speech/transcribe")
@limiter.limit("10/minute")
async def speech_to_text(
    request: Request,
    file: UploadFile = File(...),
    farmer: dict = Depends(get_current_farmer),
):
    """Authenticated, bounded speech transcription for a signed-in farmer."""
    try:
        audio_bytes = await file.read(MAX_AUDIO_BYTES + 1)

        if not audio_bytes:
            raise HTTPException(status_code=400, detail="The audio file is empty.")
        if len(audio_bytes) > MAX_AUDIO_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Audio file is too large (maximum {MAX_AUDIO_BYTES // (1024 * 1024)} MB).",
            )

        result = await run_in_threadpool(
            transcribe_audio, audio_bytes, file.filename or "audio.webm"
        )
        return {"status": "success", **result}

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("Speech transcription failed for farmer_id=%s", farmer["id"])
        raise HTTPException(
            status_code=503,
            detail="Speech transcription is temporarily unavailable.",
        ) from exc
    finally:
        await file.close()


# -------------------------------------------------------------------
# Chat history
# -------------------------------------------------------------------

@app.get("/api/v1/chat/history", dependencies=[Depends(require_admin)])
def list_chat_history(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    # get_connection() hands back an open connection that the caller must
    # close, so wrap it in closing() instead of a bare `with`.
    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT
                session_id,
                (
                    SELECT message
                    FROM chat_history AS first_message
                    WHERE first_message.session_id = chat_history.session_id
                      AND first_message.role = 'user'
                    ORDER BY first_message.id ASC
                    LIMIT 1
                ) AS first_question,
                MAX(created_at) AS last_message_at,
                MAX(id) AS last_message_id
            FROM chat_history
            GROUP BY session_id
            ORDER BY last_message_id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    return {"sessions": [dict(row) for row in rows]}



@app.get("/api/v1/chat/my-history")
def list_farmer_chat_history(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    farmer: dict = Depends(get_current_farmer),
):
    """List only the logged-in farmer's conversations."""

    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT
                chat_history.session_id,
                (
                    SELECT message
                    FROM chat_history AS first_message
                    WHERE first_message.session_id = chat_history.session_id
                      AND first_message.role = 'user'
                    ORDER BY first_message.id ASC
                    LIMIT 1
                ) AS first_question,
                MAX(chat_history.created_at) AS last_message_at,
                MAX(chat_history.id) AS last_message_id
            FROM chat_history
            INNER JOIN sessions
                ON sessions.session_id = chat_history.session_id
            WHERE sessions.user_id = ?
            GROUP BY chat_history.session_id
            ORDER BY last_message_id DESC
            LIMIT ? OFFSET ?
            """,
            (farmer["id"], limit, offset),
        ).fetchall()

    return {"sessions": [dict(row) for row in rows]}



@app.get("/api/v1/chat/history/{session_id}")
def get_chat_history(
    session_id: str,
    limit: int = Query(200, ge=1, le=200),
    before_id: int | None = Query(None, ge=1),
    farmer: dict = Depends(get_current_farmer),
):
    """Return conversation history belonging to the authenticated farmer."""
    validate_session_id(session_id)

    messages = get_session_history(
        session_id,
        user_id=farmer["id"],
        limit=limit,
        before_id=before_id,
    )

    return {
        "session_id": session_id,
        "messages": messages,
    }


@app.delete("/api/v1/chat/history/{session_id}")
def delete_chat(
    session_id: str,
    farmer: dict = Depends(get_current_farmer),
):
    validate_session_id(session_id)

    deleted = clear_session_history(
        session_id,
        user_id=farmer["id"],
    )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    # Clear in-memory context only after ownership is verified.
    delete_session(session_id)

    return {"message": "Conversation deleted successfully."}


@app.delete("/api/v1/chat/history", dependencies=[Depends(require_admin)])
def delete_all_chats():
    with closing(get_connection()) as connection:
        deleted = connection.execute("DELETE FROM chat_history").rowcount

    logger.warning("All chat history (%d messages) was deleted via the API.", deleted)
    return {
        "message": "All chat history deleted successfully.",
        "deleted_messages": deleted,
    }


# -------------------------------------------------------------------
# Government scheme eligibility checker
# -------------------------------------------------------------------

@app.get("/api/v1/schemes")
def schemes_catalog():
    """Facts about the supported schemes (editable in knowledge_base/schemes.json)."""
    try:
        return {"schemes": list_schemes()}
    except (FileNotFoundError, ValueError) as exc:
        logger.exception("Scheme data could not be loaded.")
        raise HTTPException(status_code=503, detail="Scheme data is unavailable.") from exc


@app.post("/api/v1/schemes/eligibility")
def scheme_eligibility(profile: FarmerProfile):
    """Tell a farmer whether they are likely, possibly or not eligible for each scheme."""
    try:
        return evaluate_schemes(profile)
    except (FileNotFoundError, ValueError) as exc:
        logger.exception("Scheme data could not be loaded.")
        raise HTTPException(status_code=503, detail="Scheme data is unavailable.") from exc


# -------------------------------------------------------------------
# Review queue for extension workers (admin only)
# -------------------------------------------------------------------

class FlagReview(BaseModel):
    status: Literal["reviewed", "dismissed"] = "reviewed"
    note: str | None = Field(default=None, max_length=2000)


@app.get("/api/v1/admin/flagged", dependencies=[Depends(require_admin)])
def flagged_answers(
    status: str = Query("open", description="open, reviewed, dismissed or all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    if status != "all" and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Unknown status filter.")

    items = list_flags(None if status == "all" else status, limit, offset)
    return {"flagged": items, "count": len(items)}


@app.post("/api/v1/admin/flagged/{flag_id}/review", dependencies=[Depends(require_admin)])
def review_flagged_answer(flag_id: int, review: FlagReview):
    if not resolve_flag(flag_id, review.status, review.note):
        raise HTTPException(status_code=404, detail="Flagged answer not found.")
    return {"message": "Flagged answer updated.", "id": flag_id, "status": review.status}


@app.get("/api/v1/admin/stats", dependencies=[Depends(require_admin)])
def admin_stats():
    return quality_stats()


@app.get("/api/v1/admin/farmers", dependencies=[Depends(require_admin)])
def admin_farmers(limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    """Paginated farmer directory; password hashes and credentials are never exposed."""
    return get_admin_farmer_list(limit=limit, offset=offset)


@app.get("/api/v1/admin/queries", dependencies=[Depends(require_admin)])
def admin_queries(limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    """Recent farmer questions with linked farmer identity where available."""
    return get_admin_query_list(limit=limit, offset=offset)


@app.get("/api/v1/admin/token-usage", dependencies=[Depends(require_admin)])
def admin_token_usage():
    """Provider-reported LLM token totals, model breakdown and daily totals."""
    return get_admin_token_usage()


@app.get("/api/v1/admin/knowledge")
def admin_list_knowledge(
    _: None = Depends(require_admin),
):
    records = list_records()
    return {
        "count": len(records),
        "records": records,
    }


@app.post("/api/v1/admin/knowledge")
def admin_add_knowledge(
    record: KnowledgeRecordRequest,
    _: None = Depends(require_admin),
):
    try:
        saved = add_record(record.model_dump())
        return {
            "message": "Knowledge record added successfully.",
            "record": saved,
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Could not update the knowledge index.",
        )


@app.put("/api/v1/admin/knowledge/{record_id}")
def admin_update_knowledge(
    record_id: str,
    record: KnowledgeRecordRequest,
    _: None = Depends(require_admin),
):
    try:
        updated = update_record(record_id, record.model_dump())
        return {
            "message": "Knowledge record updated successfully.",
            "record": updated,
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="Knowledge record not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Could not update the knowledge index.",
        )


@app.delete("/api/v1/admin/knowledge/{record_id}")
def admin_delete_knowledge(
    record_id: str,
    _: None = Depends(require_admin),
):
    try:
        delete_record(record_id)
        return {"message": "Knowledge record deleted successfully."}
    except KeyError:
        raise HTTPException(status_code=404, detail="Knowledge record not found.")
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Could not update the knowledge index.",
        )