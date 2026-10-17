
import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

SUPPORTED_LANGUAGES = {"hi", "kn", "en"}

LANGUAGE_NAMES = {
    "hi": "Hindi",
    "kn": "Kannada",
    "en": "English",
}


def _translate(text: str, source: str, target: str) -> str:
    text = text.strip()

    if not text:
        raise ValueError("Text cannot be empty.")

    if source not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported source language: {source}")

    if target not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported target language: {target}")

    if source == target:
        return text

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing from backend/.env")

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)

    prompt = f"""
Translate the following agricultural question or answer
from {LANGUAGE_NAMES[source]} into {LANGUAGE_NAMES[target]}.

Preserve crop names, quantities, units, dates, and agricultural
meaning. Do not answer the question or add any new information.
Return only the translated text.

Text:
{text}
"""

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
        ),
    )

    result = (response.text or "").strip()

    if not result:
        raise RuntimeError("Translation returned an empty result.")

    return result


def translate_to_english(text: str, source_language: str) -> str:
    return _translate(text, source_language, "en")


def translate_from_english(text: str, target_language: str) -> str:
    return _translate(text, "en", target_language)
