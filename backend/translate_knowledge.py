"""Fill in missing Hindi/Kannada text in knowledge_base/agriculture.json.

Run from the backend folder, once after editing the knowledge base:

    python translate_knowledge.py --dry-run     # show what would be translated
    python translate_knowledge.py               # translate and save
    python index_knowledge.py                   # rebuild the vector index

Uses services.translation_service (Gemini), so GEMINI_API_KEY must be set.
Existing translations are never overwritten, and every success is saved
immediately, so it is always safe to run the script again: it only translates
what is still missing. If Gemini is overloaded (503/504) the script waits and
retries, and switches to a backup model. Every machine translation is marked
"translation_reviewed": false so an expert can review it later.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import logging

import services.translation_service as translation_service
from services.translation_service import TranslationError, translate_from_english

# The service logs a full traceback for every failed call; this script prints
# its own short messages instead.
logging.getLogger("services.translation_service").setLevel(logging.CRITICAL)

DEFAULT_MODEL = getattr(translation_service, "DEFAULT_MODEL", "gemini-2.5-flash")

KB_PATH = Path(__file__).resolve().parent / "knowledge_base" / "agriculture.json"
LANGUAGES = ("hi", "kn")
NOTE = (
    "Machine translation (Gemini) of the English summary; "
    "not an official published translation and pending expert review."
)

# Tried in order after the primary model. A model your key cannot use is skipped.
BACKUP_MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash-lite"]
ROUNDS = 4                 # passes over the model list before giving up on one text
WAIT_BETWEEN_ROUNDS = 10   # seconds; grows each round (10, 20, 30, ...)
PAUSE_AFTER_SUCCESS = 1.0  # seconds, to stay gentle with the API


def _model_unavailable(exc: Exception) -> bool:
    text = str(exc)
    return "404" in text or "NOT_FOUND" in text


def translate_with_retries(text: str, language: str, models: list[str]) -> tuple[str, str]:
    """Return (translation, model_used). Raises TranslationError if everything fails."""
    last_error: Exception | None = None
    usable = list(models)

    for round_number in range(1, ROUNDS + 1):
        for model in list(usable):
            os.environ["GEMINI_MODEL"] = model
            try:
                return translate_from_english(text, language), model
            except TranslationError as exc:
                last_error = exc
                if _model_unavailable(exc):
                    print(f"      model {model} is not available to this key; skipping it")
                    usable.remove(model)
                else:
                    print(f"      {model} failed: {str(exc)[:90]}")

        if not usable:
            break

        wait = WAIT_BETWEEN_ROUNDS * round_number
        if round_number < ROUNDS:
            print(f"      Gemini is busy; waiting {wait}s before trying again...")
            time.sleep(wait)

    raise TranslationError(str(last_error) if last_error else "No usable model.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="only list missing translations")
    parser.add_argument("--model", help="primary Gemini model to try first (default: GEMINI_MODEL or the service default)")
    args = parser.parse_args()

    primary = args.model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
    models = [primary] + [m for m in BACKUP_MODELS if m != primary]

    records = json.loads(KB_PATH.read_text(encoding="utf-8"))
    pending = []

    for record in records:
        translations = record.get("translations") or {}
        for language in LANGUAGES:
            if not str(translations.get(language, "")).strip() and str(record.get("content", "")).strip():
                pending.append((record, language))

    print(f"{len(pending)} translation(s) missing across {len(records)} records.")
    if args.dry_run or not pending:
        for record, language in pending:
            print(f"  - {record['id']} -> {language}")
        return 0

    print(f"Models to try, in order: {', '.join(models)}")
    backup_path = KB_PATH.with_suffix(".json.bak")
    if not backup_path.exists():
        shutil.copy(KB_PATH, backup_path)

    done = failed = 0

    for number, (record, language) in enumerate(pending, start=1):
        print(f"[{number}/{len(pending)}] {record['id']} -> {language}")
        try:
            text, model = translate_with_retries(record["content"], language, models)
        except (TranslationError, ValueError) as exc:
            print(f"    FAILED: {str(exc)[:120]}")
            failed += 1
            continue

        record.setdefault("translations", {})[language] = text
        record["translation_note"] = NOTE
        record["translation_reviewed"] = False
        done += 1
        print(f"    ok ({model})")

        # Save after each success so an interruption loses nothing.
        KB_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        time.sleep(PAUSE_AFTER_SUCCESS)

    print(f"\nDone: {done} translated, {failed} failed.")
    if failed:
        print("Run the same command again later: it only retries what is still missing.")
    print("Then rebuild the index: python index_knowledge.py")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())