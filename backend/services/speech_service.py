
import tempfile
from functools import lru_cache
from pathlib import Path

import librosa
import sherpa_onnx
import whisper
from huggingface_hub import hf_hub_download


# Whisper is used for language detection and fallback transcription.
whisper_model = whisper.load_model("small")

INDIC_REPO = "parismitaglobalsolutions/indicconformer-sherpa-onnx"

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm"}
MAX_AUDIO_BYTES = 10 * 1024 * 1024

INDIC_LANGUAGES = {"hi", "kn"}


@lru_cache(maxsize=2)
def load_indic_recognizer(language: str):
    """Load and cache one IndicConformer recognizer per language."""

    if language not in INDIC_LANGUAGES:
        raise ValueError(f"Unsupported IndicConformer language: {language}")

    model_path = hf_hub_download(
        repo_id=INDIC_REPO,
        filename=f"{language}/model.int8.onnx",
    )

    tokens_path = hf_hub_download(
        repo_id=INDIC_REPO,
        filename="tokens.txt",
    )

    return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
        model=model_path,
        tokens=tokens_path,
        num_threads=2,
        decoding_method="greedy_search",
    )


def transcribe_with_indic(audio_path: str, language: str) -> str:
    """Transcribe Hindi or Kannada audio using IndicConformer."""

    recognizer = load_indic_recognizer(language)

    audio, _ = librosa.load(
        audio_path,
        sr=16000,
        mono=True,
    )

    if len(audio) == 0:
        raise ValueError("No audio data could be decoded.")

    stream = recognizer.create_stream()
    stream.accept_waveform(16000, audio)
    recognizer.decode_stream(stream)

    return stream.result.text.strip()


def transcribe_audio(audio_bytes: bytes, filename: str) -> dict:
    """Detect the language with Whisper and select a transcription model."""

    if not audio_bytes:
        raise ValueError("Audio file is empty.")

    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise ValueError("Audio file must be 10 MB or smaller.")

    suffix = Path(filename).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported audio format.")

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
        ) as temp:
            temp.write(audio_bytes)
            temp_path = Path(temp.name)

        # Step 1: Whisper detects the likely language.
        whisper_result = whisper_model.transcribe(
            str(temp_path),
            task="transcribe",
            fp16=False,
        )

        detected_language = whisper_result["language"]
        whisper_transcript = whisper_result["text"].strip()

        # Step 2: Use IndicConformer for Hindi and Kannada.
        if detected_language in INDIC_LANGUAGES:
            try:
                transcript = transcribe_with_indic(
                    str(temp_path),
                    detected_language,
                )
                transcription_model = "indicconformer"

                # Preserve a usable fallback if IndicConformer returns
                # an empty transcript.
                if not transcript:
                    transcript = whisper_transcript
                    transcription_model = "whisper_fallback"

            except Exception:
                # If IndicConformer fails to load or transcribe,
                # return the Whisper transcript instead.
                transcript = whisper_transcript
                transcription_model = "whisper_fallback"

        else:
            transcript = whisper_transcript
            transcription_model = "whisper"

        return {
            "transcript": transcript,
            "detected_language": detected_language,
            "language_detection_model": "whisper",
            "transcription_model": transcription_model,
        }

    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
