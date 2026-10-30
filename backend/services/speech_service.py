from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any

import librosa
import numpy as np
import sherpa_onnx
import whisper
from huggingface_hub import hf_hub_download

logger = logging.getLogger(__name__)

# --- Tunables ---
INDIC_REPO = "parismitaglobalsolutions/indicconformer-sherpa-onnx"
WHISPER_MODEL_NAME = "small"
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".flac"}
MAX_AUDIO_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_AUDIO_DURATION_SECONDS = 120     # Reject longer audio
MIN_AUDIO_DURATION_SECONDS = 0.3     # Reject near-empty clips
INDIC_LANGUAGES = {"hi", "kn"}
# The app only handles these languages, so language ID chooses among them
# instead of among all ~99 Whisper languages (short Hindi/Kannada clips are
# often mislabelled as Urdu, Marathi, Telugu, ...).
SUPPORTED_LANGUAGES = ("en", "hi", "kn")
SAMPLE_RATE = 16000
MAX_CONCURRENT_TRANSCRIPTIONS = 2    # Protects CPU/RAM from request bursts
SLOT_WAIT_SECONDS = 60               # How long a request waits for a free slot
FFMPEG_TIMEOUT_SECONDS = 30


class AudioValidationError(ValueError):
    """Bad input from the caller (map to HTTP 400)."""


class TranscriptionError(RuntimeError):
    """Server-side failure while transcribing (map to HTTP 503/500)."""


# --- Lazy, thread-safe model holders ---
_whisper_model = None
_whisper_lock = Lock()

_indic_recognizers: dict[str, Any] = {}
_indic_lock = Lock()

_slots = BoundedSemaphore(MAX_CONCURRENT_TRANSCRIPTIONS)


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            logger.info("Loading Whisper '%s' model...", WHISPER_MODEL_NAME)
            _whisper_model = whisper.load_model(WHISPER_MODEL_NAME)
        return _whisper_model


def load_indic_recognizer(language: str):
    """Load and cache one IndicConformer recognizer per language (thread-safe)."""
    if language not in INDIC_LANGUAGES:
        raise ValueError(f"Unsupported IndicConformer language: {language}")

    recognizer = _indic_recognizers.get(language)
    if recognizer is not None:
        return recognizer

    with _indic_lock:
        recognizer = _indic_recognizers.get(language)
        if recognizer is None:
            logger.info("Loading IndicConformer for language: %s", language)
            model_path = hf_hub_download(
                repo_id=INDIC_REPO, filename=f"{language}/model.int8.onnx"
            )
            tokens_path = hf_hub_download(repo_id=INDIC_REPO, filename="tokens.txt")
            recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
                model=model_path,
                tokens=tokens_path,
                num_threads=2,
                decoding_method="greedy_search",
            )
            _indic_recognizers[language] = recognizer
        return recognizer


def warmup(languages: set[str] | None = None) -> None:
    """Call once at app startup so the first user request isn't slow.

    The first run downloads the models (hundreds of MB), which can take longer
    than a browser is willing to wait for a single request.
    """
    _get_whisper_model()
    for lang in languages or INDIC_LANGUAGES:
        load_indic_recognizer(lang)


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------
def _load_waveform(path: Path) -> np.ndarray:
    # Decode at most MAX+1 seconds so huge/malicious files aren't fully decoded.
    audio, _ = librosa.load(
        str(path),
        sr=SAMPLE_RATE,
        mono=True,
        duration=float(MAX_AUDIO_DURATION_SECONDS + 1),
    )
    return audio


def _convert_with_ffmpeg(source: Path) -> Path:
    """Convert any format ffmpeg understands to 16 kHz mono WAV."""
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise FileNotFoundError("ffmpeg is not installed or not on PATH")

    target = source.with_name(source.stem + ".converted.wav")
    completed = subprocess.run(
        [
            executable, "-nostdin", "-v", "error", "-y",
            "-i", str(source),
            "-t", str(MAX_AUDIO_DURATION_SECONDS + 1),
            "-ar", str(SAMPLE_RATE), "-ac", "1",
            str(target),
        ],
        capture_output=True,
        timeout=FFMPEG_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg exited with {completed.returncode}: {detail}")
    return target


def _decode_audio(audio_bytes: bytes, suffix: str) -> np.ndarray:
    """Decode once to 16 kHz mono float32, enforcing duration limits."""
    temp_path: Path | None = None
    converted_path: Path | None = None
    audio: np.ndarray | None = None

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
            temp.write(audio_bytes)
            temp_path = Path(temp.name)

        try:
            audio = _load_waveform(temp_path)
        except Exception as first_error:
            # libsndfile (used first by librosa) reads WAV/FLAC/OGG/MP3 but NOT
            # WebM or M4A, which is what browsers record. Those need ffmpeg.
            # The real reason used to be swallowed; log it.
            logger.warning(
                "Direct decode of %s (%d bytes) failed: %s",
                suffix, len(audio_bytes), first_error,
            )

            has_ffmpeg = shutil.which("ffmpeg") is not None
            if has_ffmpeg:
                try:
                    converted_path = _convert_with_ffmpeg(temp_path)
                    audio = _load_waveform(converted_path)
                except Exception as ffmpeg_error:
                    logger.warning("ffmpeg fallback failed: %s", ffmpeg_error)

            if audio is None:
                hint = (
                    ""
                    if has_ffmpeg
                    else (
                        f" This server cannot read {suffix} files without ffmpeg; "
                        "install ffmpeg or send WAV audio."
                    )
                )
                raise AudioValidationError(
                    "Could not decode audio file." + hint
                ) from first_error
    finally:
        for path in (temp_path, converted_path):
            if path is not None:
                path.unlink(missing_ok=True)

    duration = len(audio) / SAMPLE_RATE
    if duration < MIN_AUDIO_DURATION_SECONDS:
        raise AudioValidationError("Audio is empty or too short.")
    if duration > MAX_AUDIO_DURATION_SECONDS:
        raise AudioValidationError(
            f"Audio exceeds max duration of {MAX_AUDIO_DURATION_SECONDS}s."
        )
    return audio.astype(np.float32, copy=False)


# --------------------------------------------------------------------------
# Recognition
# --------------------------------------------------------------------------
def detect_language(audio: np.ndarray) -> tuple[str, float]:
    """Fast language ID (first 30 s only), limited to the supported languages.

    Returns (language, confidence) where confidence is the share of probability
    the winner has among the supported languages.
    """
    model = _get_whisper_model()
    clipped = whisper.pad_or_trim(audio)
    mel = whisper.log_mel_spectrogram(clipped, n_mels=model.dims.n_mels).to(model.device)
    _, probs = model.detect_language(mel)

    scores = {code: float(probs.get(code, 0.0)) for code in SUPPORTED_LANGUAGES}
    language = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = scores[language] / total if total > 0 else 0.0
    return language, confidence


def transcribe_with_whisper(audio: np.ndarray, language: str | None = None) -> str:
    result = _get_whisper_model().transcribe(
        audio,
        task="transcribe",
        language=language,   # skips Whisper's internal re-detection
        fp16=False,
    )
    return str(result.get("text", "")).strip()


def transcribe_with_indic(audio: np.ndarray, language: str) -> str:
    recognizer = load_indic_recognizer(language)
    stream = recognizer.create_stream()
    stream.accept_waveform(SAMPLE_RATE, audio)
    recognizer.decode_stream(stream)
    return stream.result.text.strip()


def transcribe_audio(audio_bytes: bytes, filename: str) -> dict[str, Any]:
    """Detect language, then route to IndicConformer (hi/kn) or Whisper.

    Raises AudioValidationError for bad input, TranscriptionError otherwise.
    This function is blocking: from async code call it via
    `await run_in_threadpool(transcribe_audio, data, name)`.
    """
    if not audio_bytes:
        raise AudioValidationError("Audio file is empty.")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise AudioValidationError(
            f"Audio file exceeds max allowed size of {MAX_AUDIO_BYTES // (1024 * 1024)}MB."
        )

    suffix = Path(filename or "audio.webm").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise AudioValidationError(f"Unsupported audio format extension: {suffix or '(none)'}")

    # Wait for a free slot, but not forever.
    if not _slots.acquire(timeout=SLOT_WAIT_SECONDS):
        raise TranscriptionError("The speech service is busy. Please try again shortly.")

    try:
        audio = _decode_audio(audio_bytes, suffix)
        detected_language, confidence = detect_language(audio)

        transcript = ""
        model_used = "whisper"

        if detected_language in INDIC_LANGUAGES:
            try:
                transcript = transcribe_with_indic(audio, detected_language)
                model_used = "indicconformer"
            except Exception as exc:
                logger.warning("IndicConformer failed (%s); falling back to Whisper.", exc)
                model_used = "whisper_fallback"

        # Whisper runs only when needed: non-Indic audio, or Indic failure/empty result.
        if not transcript:
            if model_used == "indicconformer":
                logger.warning("IndicConformer returned empty transcript; using Whisper.")
                model_used = "whisper_fallback"
            transcript = transcribe_with_whisper(audio, language=detected_language)

        return {
            "transcript": transcript,
            "detected_language": detected_language,
            "language_confidence": round(confidence, 3),
            "duration_seconds": round(len(audio) / SAMPLE_RATE, 2),
            "language_detection_model": "whisper",
            "transcription_model": model_used,
        }

    except AudioValidationError:
        raise
    except Exception as exc:
        logger.error("Audio transcription failed: %s", exc, exc_info=True)
        raise TranscriptionError(f"Audio processing failed: {exc}") from exc
    finally:
        _slots.release()