
import sys

import librosa
import sherpa_onnx
from huggingface_hub import hf_hub_download

REPO = "parismitaglobalsolutions/indicconformer-sherpa-onnx"

def transcribe(audio_path: str, language: str) -> str:
    if language not in {"hi", "kn", "en"}:
        raise ValueError("Supported test languages: hi, kn, en")

    if language == "en":
        model_file = "en/model.int8.onnx"
        tokens_file = "en/tokens.txt"
    else:
        model_file = f"{language}/model.int8.onnx"
        tokens_file = "tokens.txt"

    print(f"Downloading/loading {language} model...")
    model_path = hf_hub_download(REPO, model_file)
    tokens_path = hf_hub_download(REPO, tokens_file)

    recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
        model=model_path,
        tokens=tokens_path,
        num_threads=2,
        decoding_method="greedy_search",
    )

    audio, sample_rate = librosa.load(
        audio_path,
        sr=16000,
        mono=True,
    )

    stream = recognizer.create_stream()
    stream.accept_waveform(16000, audio)
    recognizer.decode_stream(stream)

    return stream.result.text.strip()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python test_indic_asr.py AUDIO.wav hi|kn|en")
        raise SystemExit(1)

    print("Transcript:", transcribe(sys.argv[1], sys.argv[2]))
