
"use client";

import { useRef, useState } from "react";

const API_BASE = "http://127.0.0.1:8000/api/v1";

type AnalysisResult = {
  answer: string;
  language: string;
  session_id: string;
  intent: string;
  confidence: number;
  entities: {
    crops: string[];
    topics: string[];
    locations: string[];
  };
  classification_model: string;
  fallback_used: boolean;
  source_name: string | null;
  source_url: string | null;
};

export default function Home() {
  const [mode, setMode] = useState<"text" | "audio">("text");
  const [text, setText] = useState("");
  const [language, setLanguage] = useState("auto");
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [isListening, setIsListening] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  function clearAudioUrl() {
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl("");
  }





async function analyzeText(event: React.FormEvent) {
  event.preventDefault();

  if (!text.trim() || loading) return;

  setLoading(true);
  setError("");
  setResult(null);
  clearAudioUrl();

  try {
    let selectedLanguage = language;

    // Detect the language when Automatic Detection is selected.
    if (language === "auto") {
      const detectResponse = await fetch(
        `${API_BASE}/detect-language`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            text: text.trim(),
          }),
        }
      );

      if (!detectResponse.ok) {
        throw new Error("Language detection failed.");
      }

      const detectedData = await detectResponse.json();

      if (!["hi", "kn", "en"].includes(detectedData.language)) {
        throw new Error(
          "Language could not be detected. Please select Hindi, Kannada, or English manually."
        );
      }

      selectedLanguage = detectedData.language;
    }

    const response = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: text.trim(),
        language: selectedLanguage,
        session_id: sessionId,
      }),
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(
        `Backend returned HTTP ${response.status}: ${detail}`
      );
    }

    const data: AnalysisResult = await response.json();
    setResult(data);
    setSessionId(data.session_id);
  } catch (err) {
    setError(
      err instanceof Error
        ? err.message
        : "Unable to analyze text."
    );
  } finally {
    setLoading(false);
  }
}




async function startListening() {
  if (mediaRecorderRef.current?.state === "recording") {
    mediaRecorderRef.current.stop();
    return;
  }

  let stream: MediaStream | undefined;

  try {
    setError("");

    stream = await navigator.mediaDevices.getUserMedia({
      audio: true,
    });

    const mimeType = MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
      : "";

    const recorder = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);

    mediaRecorderRef.current = recorder;
    audioChunksRef.current = [];

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        audioChunksRef.current.push(event.data);
      }
    };

    recorder.onstop = async () => {
      stream?.getTracks().forEach((track) => track.stop());

      setIsListening(false);
      setLoading(true);
      setError("");
      setResult(null);
      clearAudioUrl();

      try {
        const audioBlob = new Blob(audioChunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });

        if (!audioBlob.size) {
          throw new Error("No audio recorded. Please try again.");
        }

        const formData = new FormData();
        formData.append("file", audioBlob, "microphone.webm");

        // Step 1: Transcribe the audio and detect its language.
        const speechResponse = await fetch(
          `${API_BASE}/speech/transcribe`,
          {
            method: "POST",
            body: formData,
          }
        );

        if (!speechResponse.ok) {
          throw new Error(
            `Speech API error (${speechResponse.status}): ${
              await speechResponse.text()
            }`
          );
        }

        const speechData = await speechResponse.json();
        const transcript = speechData.transcript?.trim();
        const detectedLanguage = speechData.detected_language;

        if (!transcript) {
          throw new Error("No speech was recognized.");
        }

        if (!["hi", "kn", "en"].includes(detectedLanguage)) {
          throw new Error(
            "Language not supported. Please try again or select a language manually."
          );
        }

        const selectedLanguage =
          language === "auto" ? detectedLanguage : language;

        setText(transcript);

        // Step 2: Analyze the transcript.
        const chatResponse = await fetch(`${API_BASE}/chat`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: transcript,
            language: selectedLanguage,
            session_id: sessionId,
          }),
        });

        if (!chatResponse.ok) {
          throw new Error(
            `Chat API error (${chatResponse.status}): ${
              await chatResponse.text()
            }`
          );
        }

        const chatData: AnalysisResult = await chatResponse.json();

        setResult({
          ...chatData,
          transcript,
        });
        setSessionId(chatData.session_id);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Unable to process the recording."
        );
      } finally {
        setLoading(false);
        mediaRecorderRef.current = null;
      }
    };

    recorder.start();
    setIsListening(true);
  } catch (err) {
    stream?.getTracks().forEach((track) => track.stop());

    setIsListening(false);
    setError(
      err instanceof Error
        ? err.message
        : "Unable to access the microphone."
    );
  }
}




  
async function analyzeAudio(event: React.FormEvent) {
  event.preventDefault();
  if (!audioFile || loading) return;

  setLoading(true);
  setError("");
  setResult(null);
  clearAudioUrl();

  try {
    const formData = new FormData();
    formData.append("file", audioFile);

    // Step 1: Transcribe the uploaded audio.
    const speechResponse = await fetch(
      `${API_BASE}/speech/transcribe`,
      {
        method: "POST",
        body: formData,
      }
    );

    if (!speechResponse.ok) {
      const detail = await speechResponse.text();
      throw new Error(
        `Speech API returned HTTP ${speechResponse.status}: ${detail}`
      );
    }

    const speechData = await speechResponse.json();
    const transcript = speechData.transcript;

    if (typeof transcript !== "string" || !transcript.trim()) {
      throw new Error("The speech API returned no transcript.");
    }

    // Use the detected language when supported; otherwise use the selector.
    const detectedLanguage = ["hi", "kn", "en"].includes(
      speechData.detected_language
    )
      ? speechData.detected_language
      : language;

    // Step 2: Classify the transcribed question.
    const chatResponse = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: transcript,
        language: detectedLanguage,
        session_id: sessionId,
      }),
    });

    

    if (!chatResponse.ok) {
      const detail = await chatResponse.text();
      throw new Error(
        `Chat API returned HTTP ${chatResponse.status}: ${detail}`
      );
    }

    const chatData = await chatResponse.json();
    setSessionId(chatData.session_id);

    // Keep the transcript available for display.
    setResult({
      ...chatData,
      transcript,
    });
  } catch (err) {
    setError(
      err instanceof Error
        ? err.message
        : "Unable to process audio."
    );
  } finally {
    setLoading(false);
  }
}

function checkVoices() {
  const voices = window.speechSynthesis.getVoices();

  console.table(
    voices.map((voice) => ({
      name: voice.name,
      language: voice.lang,
    }))
  );
}
  
  

function synthesizeSpeech() {
  if (!result || typeof window === "undefined") return;

  const synth = window.speechSynthesis;

  if (!("speechSynthesis" in window)) {
    setError("Speech playback is not supported in this browser.");
    return;
  }

  const textToSpeak = result.answer?.trim();

  if (!textToSpeak) {
    setError("There is no answer to speak.");
    return;
  }

  synth.cancel();
  setError("");

  const selectedLanguage = result.language ?? language;

  const languageMap: Record<string, string> = {
    en: "en-IN",
    hi: "hi-IN",
    kn: "kn-IN",
  };

  const targetLanguage = languageMap[selectedLanguage] ?? "en-IN";

  const speakWithVoice = () => {
    const voices = synth.getVoices();

    const preferredVoice =
      voices.find((voice) =>
        selectedLanguage === "hi"
          ? voice.name === "Google हिन्दी"
          : selectedLanguage === "kn"
            ? voice.name === "Soumya"
            : voice.name === "Rishi"
      ) ??
      voices.find((voice) =>
        voice.lang.toLowerCase() === targetLanguage.toLowerCase()
      );

    const utterance = new SpeechSynthesisUtterance(textToSpeak);
    utterance.lang = targetLanguage;
    utterance.rate = 0.9;

    if (preferredVoice) {
      utterance.voice = preferredVoice;
    }

    utterance.onerror = (event) => {
      setError(`Speech playback failed: ${event.error}`);
    };

    synth.speak(utterance);
  };

  if (synth.getVoices().length > 0) {
    speakWithVoice();
  } else {
    synth.addEventListener("voiceschanged", function onVoicesChanged() {
      synth.removeEventListener("voiceschanged", onVoicesChanged);
      speakWithVoice();
    }, { once: true });
  }
}




  return (
    <main className="mx-auto min-h-screen max-w-5xl space-y-6 p-6">
      <header>
        <p className="text-sm font-semibold text-green-700">
          INFOSYS · AI DOMAIN
        </p>
        <h1 className="mt-2 text-3xl font-bold">
          Smart Farming Assistant
        </h1>
        <p className="mt-2 text-gray-600">
          Multilingual speech processing and agricultural NLP
        </p>
      </header>

      <section className="space-y-4 rounded-xl border bg-white p-5">
        <h2 className="text-xl font-semibold">
          Input and language
        </h2>

        <label className="block space-y-2">
          <span className="font-medium">Language</span>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="w-full rounded-lg border p-3"
          >
            <option value="auto">Automatic detection</option>
            <option value="hi">Hindi</option>
            <option value="kn">Kannada</option>
            <option value="en">English</option>
          </select>
        </label>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setMode("text")}
            className={`rounded-lg px-4 py-2 ${
              mode === "text"
                ? "bg-green-700 text-white"
                : "border"
            }`}
          >
            Text input
          </button>
          <button
            type="button"
            onClick={() => setMode("audio")}
            className={`rounded-lg px-4 py-2 ${
              mode === "audio"
                ? "bg-green-700 text-white"
                : "border"
            }`}
          >
            Audio input
          </button>
        </div>

        {mode === "text" ? (
          <form onSubmit={analyzeText} className="space-y-3">
            <label htmlFor="question" className="font-medium">
              Enter a farming question
            </label>
            <textarea
              id="question"
              value={text}
              onChange={(event) => setText(event.target.value)}
              maxLength={2000}
              required
              rows={4}
              placeholder="मेरी धान की फसल में कीट लग रहे हैं।"
              className="w-full rounded-lg border p-3"
            />


<button
  type="button"
  onClick={startListening}
  disabled={loading}
  className="rounded-lg border px-4 py-2 disabled:opacity-50"
>
  {isListening ? "Listening..." : "🎤 Speak your question"}
</button>



            <button
              disabled={loading || !text.trim()}
              className="rounded-lg bg-green-700 px-5 py-3 text-white disabled:opacity-50"
            >
              {loading ? "Processing..." : "Analyze text"}
            </button>
          </form>
        ) : (
          <form onSubmit={analyzeAudio} className="space-y-3">
            <label htmlFor="audio" className="font-medium">
              Upload a recorded question
            </label>
            <input
              id="audio"
              type="file"
              accept="audio/*"
              required
              onChange={(event) =>
                setAudioFile(event.target.files?.[0] ?? null)
              }
              className="block w-full rounded-lg border p-3"
            />
            <p className="text-sm text-gray-500">
              Audio is sent to the backend for transcription.
            </p>
            <button
              disabled={loading || !audioFile}
              className="rounded-lg bg-green-700 px-5 py-3 text-white disabled:opacity-50"
            >
              {loading ? "Processing..." : "Transcribe and analyze"}
            </button>
          </form>
        )}
      </section>

      {error && (
        <p role="alert" className="rounded-lg bg-red-50 p-4 text-red-700">
          {error}
        </p>
      )}



{result && (
  <section className="space-y-4 rounded-xl border p-5">
    <h2 className="text-xl font-semibold">
      Smart Farming Assistant Response
    </h2>

    <div className="rounded-lg bg-green-50 p-4">
      <p className="text-sm font-medium text-green-800">
        Agricultural guidance
      </p>
      <p className="mt-2 text-gray-900">{result.answer}</p>
      <button
  type="button"
  onClick={synthesizeSpeech}
  className="mt-3 rounded-lg bg-green-700 px-4 py-2 text-white"
>
  🔊 Listen to Answer
</button>

<button type="button" onClick={checkVoices}>
  Check Available Voices
</button>


      {result.source_name && (
  <div className="mt-3 border-t border-green-200 pt-3">
    <p className="text-sm font-medium text-green-800">
      Source: {result.source_name}
    </p>

    {result.source_url && (
      <a
        href={result.source_url}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-1 inline-block text-sm text-blue-700 underline"
      >
        View source
      </a>
    )}
  </div>
)}



    </div>

    <div className="grid gap-3 sm:grid-cols-3">
      <div className="rounded-lg bg-gray-50 p-3">
        <p className="text-sm text-gray-500">Language</p>
        <p>{result.language}</p>
      </div>

      <div className="rounded-lg bg-gray-50 p-3">
        <p className="text-sm text-gray-500">Intent</p>
        <p>{result.intent}</p>
      </div>

      <div className="rounded-lg bg-gray-50 p-3">
        <p className="text-sm text-gray-500">Confidence</p>
        <p>{(result.confidence * 100).toFixed(2)}%</p>
      </div>
    </div>

    <h3 className="font-semibold">Extracted entities</h3>
    <div className="space-y-2">
      <p>Crops: {result.entities.crops.join(", ") || "None"}</p>
      <p>Topics: {result.entities.topics.join(", ") || "None"}</p>
      <p>
        Locations: {result.entities.locations.join(", ") || "None"}
      </p>
    </div>

    <p className="break-all text-sm text-gray-500">
      Session ID: {result.session_id}
    </p>

    <p className="text-sm text-gray-500">
      Keyword fallback used: {result.fallback_used ? "Yes" : "No"}
    </p>

    <h3 className="font-semibold">Complete JSON response</h3>
    <pre className="overflow-x-auto rounded-lg bg-gray-950 p-4 text-sm text-green-300">
      {JSON.stringify(result, null, 2)}
    </pre>
  </section>
)}


      

      <footer className="text-sm text-gray-500">
        Prototype: model predictions must be validated before using
        them for real agricultural advice.
      </footer>
    </main>
  );
}
