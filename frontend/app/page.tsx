"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";

// -------------------------------------------------------------------
// Configuration
// -------------------------------------------------------------------

const SESSION_STORAGE_KEY = "smart-farming-session";
const OFFLINE_CHAT_CACHE_KEY = "smart-farming-last-conversation";
const SESSION_ID_PATTERN = /^[A-Za-z0-9_.:-]{8,128}$/;

const MAX_AUDIO_BYTES = 10 * 1024 * 1024;
const MAX_RECORDING_MS = 60_000;
const DEFAULT_TIMEOUT_MS = 20_000;
const CHAT_TIMEOUT_MS = 60_000;
const TRANSCRIBE_TIMEOUT_MS = 120_000;

type LangCode = "hi" | "kn" | "en";
type LanguageChoice = "auto" | LangCode;

function isLangCode(value: unknown): value is LangCode {
  return value === "hi" || value === "kn" || value === "en";
}

const SPEECH_LANG: Record<LangCode, string> = {
  en: "en-IN",
  hi: "hi-IN",
  kn: "kn-IN",
};

const LANG_NAMES: Record<LangCode, string> = {
  en: "English",
  hi: "Hindi",
  kn: "Kannada",
};

const MARKET_WORDS: Record<
  LangCode,
  { min: string; max: string; modal: string; unit: string }
> = {
  en: { min: "minimum", max: "maximum", modal: "modal", unit: "rupees per quintal" },
  hi: { min: "न्यूनतम", max: "अधिकतम", modal: "मोडल", unit: "रुपये प्रति क्विंटल" },
  kn: { min: "ಕನಿಷ್ಠ", max: "ಗರಿಷ್ಠ", modal: "ಮಾದರಿ", unit: "ರೂಪಾಯಿ ಪ್ರತಿ ಕ್ವಿಂಟಾಲ್" },
};

const COMMODITY_OPTIONS = [
  "Wheat", "Paddy(Dhan)(Common)", "Rice", "Potato", "Onion", "Tomato",
  "Mustard", "Maize", "Soyabean", "Cotton", "Pumpkin",
];

const STATE_OPTIONS = [
  "Andaman and Nicobar", "Andhra Pradesh", "Assam", "Bihar", "Chandigarh",
  "Chattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh",
  "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
  "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "NCT of Delhi", "Nagaland",
  "Odisha", "Pondicherry", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
  "Telangana", "Tripura", "Uttar Pradesh", "Uttrakhand", "West Bengal",
];

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

type SourceItem = {
  source_name: string;
  source_url: string | null;
};

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
  sources?: SourceItem[];
  quality_score?: number | null;
  needs_review?: boolean;
};

type HistorySession = {
  session_id: string;
  first_question: string | null;
  last_message_at: string;
  last_message_id?: number;
};

type HistoryMessage = {
  id?: number;
  role: string;
  message: string;
  language: string | null;
  intent: string | null;
  created_at: string;
};

type MarketPriceRecord = {
  commodity?: string;
  state?: string;
  district?: string;
  market?: string;
  variety?: string;
  arrival_date?: string;
  min_price?: string | number;
  max_price?: string | number;
  modal_price?: string | number;
};

type MarketPriceResponse = {
  source: string;
  commodity: string;
  state?: string | null;
  district?: string | null;
  count: number;
  records: MarketPriceRecord[];
  latest_arrival_date?: string | null;
  data_stale?: boolean;
  cached?: boolean;
  partial?: boolean;
  note?: string;
};

type Structured =
  | { kind: "market"; data: MarketPriceResponse }
  | { kind: "weather"; entries: [string, string][] };

type TriState = "" | "yes" | "no";

type SchemeForm = {
  state: string;
  land_hectares: string;
  owns_land: TriState;
  is_tenant_or_sharecropper: TriState;
  grows_notified_crop: TriState;
  pays_income_tax: TriState;
  govt_employee_or_pensioner: TriState;
  institutional_landholder: TriState;
  holds_constitutional_post: TriState;
  practicing_professional: TriState;
};

type SchemeResult = {
  id: string;
  name: string;
  status: "likely_eligible" | "possibly_eligible" | "not_eligible";
  reasons: string[];
  missing_information: string[];
  benefit?: string;
  how_to_apply?: string;
  documents?: string[];
  official_url?: string | null;
  helpline?: string | null;
};

type EligibilityResponse = {
  results: SchemeResult[];
  disclaimer: string;
  state_note: string;
};

const SCHEME_QUESTIONS: { key: keyof Omit<SchemeForm, "state" | "land_hectares">; label: string }[] = [
  { key: "owns_land", label: "Do you own agricultural land?" },
  { key: "is_tenant_or_sharecropper", label: "Do you cultivate land as a tenant or sharecropper?" },
  { key: "grows_notified_crop", label: "Is your crop notified for crop insurance in your area?" },
  { key: "pays_income_tax", label: "Did your family pay income tax last year?" },
  { key: "govt_employee_or_pensioner", label: "Is anyone in your family a government employee or pensioner?" },
  { key: "institutional_landholder", label: "Is the land held by an institution?" },
  { key: "holds_constitutional_post", label: "Does anyone in your family hold a constitutional post (minister, MP, MLA, mayor)?" },
  { key: "practicing_professional", label: "Does anyone practise as a doctor, engineer, lawyer, CA or architect?" },
];

const EMPTY_SCHEME_FORM: SchemeForm = {
  state: "",
  land_hectares: "",
  owns_land: "",
  is_tenant_or_sharecropper: "",
  grows_notified_crop: "",
  pays_income_tax: "",
  govt_employee_or_pensioner: "",
  institutional_landholder: "",
  holds_constitutional_post: "",
  practicing_professional: "",
};

const SCHEME_STATUS: Record<SchemeResult["status"], { label: string; style: string }> = {
  likely_eligible: { label: "Likely eligible", style: "bg-green-100 text-green-800" },
  possibly_eligible: { label: "Possibly eligible", style: "bg-amber-100 text-amber-800" },
  not_eligible: { label: "Not eligible", style: "bg-red-100 text-red-800" },
};

function triToBool(value: TriState): boolean | null {
  return value === "yes" ? true : value === "no" ? false : null;
}

// -------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------

function errMsg(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

async function apiFetch(
  path: string,
  init: RequestInit = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<Response> {
  // Detect offline status before making an API request.
  if (typeof navigator !== "undefined" && !navigator.onLine){
    throw new Error(
      "You are currently offline. Please check your internet connection and try again."
    );
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(`/api/farmer/backend${path}`, {
  ...init,
  credentials: "same-origin",
  signal: controller.signal,
});
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("The request took too long. Please try again.");
    }
    if (typeof navigator !== "undefined" && !navigator.onLine) {
      throw new Error(
        "You are currently offline. Please check your internet connection and try again."
      );
    }

    throw new Error(
      "Cannot reach the server. Please check your connection and that the backend is running."
    );
   
  } finally {
    clearTimeout(timer);
  }
}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;

    if (typeof detail === "string" && detail) return detail;

    if (Array.isArray(detail)) {
      const messages = detail
        .map((item: { msg?: string }) => item?.msg)
        .filter(Boolean);
      if (messages.length) return messages.join("; ");
    }
  } catch {
    // Fall through
  }

  return `${fallback} (HTTP ${response.status}).`;
}

function formatTime(value?: string | null): string {
  if (!value) return "";

  const hasZone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(value);
  const date = new Date(hasZone ? value : `${value.replace(" ", "T")}Z`);

  if (Number.isNaN(date.getTime())) return value;

  return date.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function safeUrl(url?: string | null): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null;
}

function humanize(key: string): string {
  return key
    .replace(/[_\s]+/g, " ")
    .trim()
    .replace(/^./, (char) => char.toUpperCase());
}

function flattenEntries(
  value: unknown,
  prefix = "",
  depth = 0,
  out: [string, string][] = []
): [string, string][] {
  if (value === null || value === undefined || depth > 3) return out;

  if (Array.isArray(value)) {
    if (value.every((item) => item === null || typeof item !== "object")) {
      out.push([humanize(prefix), value.join(", ")]);
    } else {
      value
        .slice(0, 5)
        .forEach((item, index) =>
          flattenEntries(item, `${prefix} ${index + 1}`, depth + 1, out)
        );
    }
    return out;
  }

  if (typeof value === "object") {
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      flattenEntries(child, prefix ? `${prefix} ${key}` : key, depth + 1, out);
    }
    return out;
  }

  out.push([humanize(prefix), String(value)]);
  return out;
}

function parseStructured(
  intent: string | null | undefined,
  answer: string
): Structured | null {
  if (intent !== "weather" && intent !== "market_prices") return null;

  const trimmed = answer.trim();
  if (!trimmed.startsWith("{")) return null;

  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null;
    }

    if (intent === "market_prices") {
      const data = parsed as MarketPriceResponse;
      return Array.isArray(data.records) ? { kind: "market", data } : null;
    }

    const entries = flattenEntries(parsed);
    return entries.length ? { kind: "weather", entries } : null;
  } catch {
    return null;
  }
}

function answerToSpeech(
  intent: string | null | undefined,
  answer: string,
  language: string
): string {
  const structured = parseStructured(intent, answer);
  if (!structured) return answer;

  if (structured.kind === "market") {
    const words = MARKET_WORDS[isLangCode(language) ? language : "en"];

    const lines = structured.data.records.slice(0, 3).map((record) => {
      const place = [record.market, record.district].filter(Boolean).join(", ");
      const prices = [
        record.min_price != null && `${words.min} ${record.min_price}`,
        record.max_price != null && `${words.max} ${record.max_price}`,
        record.modal_price != null && `${words.modal} ${record.modal_price}`,
      ]
        .filter(Boolean)
        .join(", ");
      return `${place}: ${prices} ${words.unit}`;
    });

    return [structured.data.commodity, ...lines].join(". ");
  }

  return structured.entries
    .slice(0, 10)
    .map(([label, value]) => `${label}: ${value}`)
    .join(". ");
}

function pickMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "";

  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
}

function recordingFileName(mimeType: string): string {
  if (mimeType.includes("mp4")) return "microphone.m4a";
  if (mimeType.includes("ogg")) return "microphone.ogg";
  return "microphone.webm";
}

function micErrorMessage(err: unknown): string {
  if (err instanceof DOMException) {
    if (err.name === "NotAllowedError") {
      return "Microphone access was blocked. Allow it in your browser settings and try again.";
    }
    if (err.name === "NotFoundError") {
      return "No microphone was found on this device.";
    }
  }
  return errMsg(err, "Unable to access the microphone.");
}

function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const writeText = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) {
      view.setUint8(offset + i, value.charCodeAt(i));
    }
  };

  writeText(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, samples.length * 2, true);

  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }

  return new Blob([buffer], { type: "audio/wav" });
}

async function convertToWav(blob: Blob): Promise<Blob> {
  const AudioContextClass: typeof AudioContext | undefined =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;

  if (!AudioContextClass) throw new Error("Web Audio is not supported.");

  const context = new AudioContextClass();

  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());

    const targetRate = 16000;
    const frames = Math.max(1, Math.ceil(decoded.duration * targetRate));
    const offline = new OfflineAudioContext(1, frames, targetRate);

    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();

    const rendered = await offline.startRendering();
    return encodeWav(rendered.getChannelData(0), targetRate);
  } finally {
    void context.close();
  }
}

// -------------------------------------------------------------------
// Small presentational components
// -------------------------------------------------------------------

function MarketTable({ records }: { records: MarketPriceRecord[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="bg-green-50">
          <tr>
            <th className="p-3">Market</th>
            <th className="p-3">District</th>
            <th className="p-3">Date</th>
            <th className="p-3">Min. (₹/quintal)</th>
            <th className="p-3">Max. (₹/quintal)</th>
            <th className="p-3">Modal (₹/quintal)</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record, index) => (
            <tr key={`${record.market ?? "market"}-${index}`} className="border-t">
              <td className="p-3">{record.market ?? "—"}</td>
              <td className="p-3">{record.district ?? "—"}</td>
              <td className="p-3">{record.arrival_date ?? "—"}</td>
              <td className="p-3">{record.min_price ?? "—"}</td>
              <td className="p-3">{record.max_price ?? "—"}</td>
              <td className="p-3 font-semibold">{record.modal_price ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EntryList({ entries }: { entries: [string, string][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
      {entries.map(([label, value], index) => (
        <div key={`${label}-${index}`} className="flex justify-between gap-3 border-b border-green-100 py-1">
          <dt className="text-gray-500">{label}</dt>
          <dd className="text-right font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function AnswerBody({
  intent,
  answer,
}: {
  intent?: string | null;
  answer: string;
}) {
  const structured = parseStructured(intent, answer);

  if (structured?.kind === "market") {
    const { data } = structured;
    return (
      <div>
        <p className="mb-2 text-sm text-gray-600">
          {data.commodity}
          {data.state ? ` · ${data.state}` : ""} · {data.count} records
        </p>
        <MarketTable records={data.records} />
        {data.data_stale && (
          <p className="mt-2 text-xs text-amber-700">
            These prices may be out of date
            {data.latest_arrival_date ? ` (latest: ${data.latest_arrival_date})` : ""}.
          </p>
        )}
      </div>
    );
  }

  if (structured?.kind === "weather") {
    return <EntryList entries={structured.entries} />;
  }

  return <p className="whitespace-pre-wrap">{answer}</p>;
}

function SchemeCard({ scheme }: { scheme: SchemeResult }) {
  const status = SCHEME_STATUS[scheme.status];
  const url = safeUrl(scheme.official_url);

  return (
    <div className="rounded-xl border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">{scheme.name}</h3>
        <span className={`rounded-full px-3 py-1 text-xs font-medium ${status.style}`}>
          {status.label}
        </span>
      </div>

      {scheme.reasons.map((reason) => (
        <p key={reason} className="mt-2 text-sm text-gray-700">{reason}</p>
      ))}

      {scheme.missing_information.length > 0 && (
        <div className="mt-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
          <p className="font-medium">To be sure, please answer:</p>
          <ul className="ml-5 list-disc">
            {scheme.missing_information.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {scheme.benefit && <p className="mt-3 text-sm"><span className="font-medium">Benefit: </span>{scheme.benefit}</p>}
      {scheme.how_to_apply && <p className="mt-1 text-sm"><span className="font-medium">How to apply: </span>{scheme.how_to_apply}</p>}
      {scheme.documents && scheme.documents.length > 0 && (
        <p className="mt-1 text-sm"><span className="font-medium">Documents: </span>{scheme.documents.join(", ")}</p>
      )}
      {scheme.helpline && <p className="mt-1 text-sm"><span className="font-medium">Helpline: </span>{scheme.helpline}</p>}
      {url && (
        <a href={url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-block text-sm text-blue-700 underline">
          Official website
        </a>
      )}
    </div>
  );
}

// -------------------------------------------------------------------
// Icons
// -------------------------------------------------------------------

const ICONS = {
  home: (<><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><polyline points="9 22 9 12 15 12 15 22" /></>),
  sprout: (<><path d="M7 20h10" /><path d="M10 20c5.5-2.5.8-6.4 3-10" /><path d="M9.5 9.4c1.1.8 1.8 2.2 2.3 3.7-2 .4-3.5.4-4.8-.3-1.2-.6-2.3-1.9-3-4.2 2.8-.5 4.4 0 5.5.8z" /><path d="M14.1 6a7 7 0 0 0-1.1 4c1.9-.1 3.3-.6 4.3-1.4 1-1 1.6-2.3 1.7-4.6-2.7.1-4 1-4.9 2z" /></>),
  bug: (<><path d="m8 2 1.88 1.88" /><path d="M14.12 3.88 16 2" /><path d="M9 7.13v-1a3.003 3.003 0 1 1 6 0v1" /><path d="M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6" /><path d="M12 20v-9" /><path d="M6.53 9C4.6 8.8 3 7.1 3 5" /><path d="M6 13H2" /><path d="M3 21c0-2.1 1.7-3.9 3.8-4" /><path d="M20.97 5c0 2.1-1.6 3.8-3.5 4" /><path d="M22 13h-4" /><path d="M17.2 17c2.1.1 3.8 1.9 3.8 4" /></>),
  cloud: (<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" />),
  chart: (<><line x1="12" x2="12" y1="20" y2="10" /><line x1="18" x2="18" y1="20" y2="4" /><line x1="6" x2="6" y1="20" y2="16" /></>),
  landmark: (<><line x1="3" x2="21" y1="22" y2="22" /><line x1="6" x2="6" y1="18" y2="11" /><line x1="10" x2="10" y1="18" y2="11" /><line x1="14" x2="14" y1="18" y2="11" /><line x1="18" x2="18" y1="18" y2="11" /><polygon points="12 2 20 7 4 7" /></>),
  bulb: (<><path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5" /><path d="M9 18h6" /><path d="M10 22h4" /></>),
  clock: (<><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></>),
  settings: (<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></>),
  help: (<><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><line x1="12" x2="12.01" y1="17" y2="17" /></>),
  info: (<><circle cx="12" cy="12" r="10" /><line x1="12" x2="12" y1="16" y2="12" /><line x1="12" x2="12.01" y1="8" y2="8" /></>),
  mic: (<><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><line x1="12" x2="12" y1="19" y2="22" /></>),
  stop: (<rect x="6" y="6" width="12" height="12" rx="2" />),
  clip: (<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />),
  up: (<><path d="m5 12 7-7 7 7" /><path d="M12 19V5" /></>),
  globe: (<><circle cx="12" cy="12" r="10" /><path d="M2 12h20" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /></>),
  bell: (<><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" /></>),
  trend: (<><polyline points="22 7 13.5 15.5 8.5 10.5 2 17" /><polyline points="16 7 22 7 22 13" /></>),
  right: (<><path d="M5 12h14" /><path d="m12 5 7 7-7 7" /></>),
  plus: (<><path d="M5 12h14" /><path d="M12 5v14" /></>),
  chevron: (<polyline points="6 9 12 15 18 9" />),
  chat: (<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />),
};

const ICONS_SOLID = {
  homeS: "M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z",
  cropS: "M6.05 8.05c-2.73 2.73-2.73 7.15-.02 9.88 1.47-3.4 4.09-6.24 7.36-7.93-2.77 2.34-4.71 5.61-5.39 9.32 2.6 1.23 5.8.78 7.95-1.37C19.43 14.47 20 4 20 4S9.53 4.57 6.05 8.05z",
  bugS: "M20 8h-2.81a5.985 5.985 0 0 0-1.82-1.96L17 4.41 15.59 3l-2.17 2.17a5.9 5.9 0 0 0-2.84 0L8.41 3 7 4.41l1.62 1.63C7.88 6.55 7.26 7.22 6.81 8H4v2h2.09c-.05.33-.09.66-.09 1v1H4v2h2v1c0 .34.04.67.09 1H4v2h2.81c1.04 1.79 2.97 3 5.19 3s4.15-1.21 5.19-3H20v-2h-2.09c.05-.33.09-.66.09-1v-1h2v-2h-2v-1c0-.34-.04-.67-.09-1H20V8zm-6 8h-4v-2h4v2zm0-4h-4v-2h4v2z",
  cloudS: "M19.35 10.04A7.49 7.49 0 0 0 12 4C9.11 4 6.6 5.64 5.35 8.04A5.994 5.994 0 0 0 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z",
  chartS: "M5 9.2h3V19H5zM10.6 5h2.8v14h-2.8zm5.6 8H19v6h-2.8z",
  landmarkS: "M4 10v7h3v-7H4zm6 0v7h3v-7h-3zM2 22h19v-3H2v3zm14-12v7h3v-7h-3zm-4.5-9L2 6v2h19V6l-9.5-5z",
  bulbS: "M9 21c0 .55.45 1 1 1h4c.55 0 1-.45 1-1v-1H9v1zm3-19C8.14 2 5 5.14 5 9c0 2.38 1.19 4.47 3 5.74V17c0 .55.45 1 1 1h6c.55 0 1-.45 1-1v-2.26c1.81-1.27 3-3.36 3-5.74 0-3.86-3.14-7-7-7z",
  settingsS: "M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.488.488 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z",
  helpS: "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 17h-2v-2h2v2zm2.07-7.75l-.9.92C13.45 12.9 13 13.5 13 15h-2v-.5c0-1.1.45-2.1 1.17-2.83l1.24-1.26c.37-.36.59-.86.59-1.41 0-1.1-.9-2-2-2s-2 .9-2 2H8c0-2.21 1.79-4 4-4s4 1.79 4 4c0 .88-.36 1.68-.93 2.25z",
};

type IconName = keyof typeof ICONS | keyof typeof ICONS_SOLID;

function Icon({ name, className = "h-5 w-5" }: { name: IconName; className?: string }) {
  if (name in ICONS_SOLID) {
    return (
      <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
        <path fillRule="evenodd" d={ICONS_SOLID[name as keyof typeof ICONS_SOLID]} />
      </svg>
    );
  }
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {ICONS[name as keyof typeof ICONS] as ReactNode}
    </svg>
  );
}

function Logo({ className = "h-12 w-12" }: { className?: string }) {
  const uid = useId().replace(/:/g, "");

  return (
    <svg viewBox="0 0 48 48" aria-hidden="true" className={className}>
      <defs>
        <linearGradient id={`${uid}-a`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#22c55e" />
          <stop offset="1" stopColor="#15803d" />
        </linearGradient>
        <linearGradient id={`${uid}-b`} x1="1" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#86efac" />
          <stop offset="1" stopColor="#16a34a" />
        </linearGradient>
      </defs>
      <path d="M24 44C23 30 15 23 5 10c14-2 24 6 19 34Z" fill={`url(#${uid}-a)`} />
      <path d="M24 44c1-14 9-21 19-34-14-2-24 6-19 34Z" fill={`url(#${uid}-b)`} />
      <path d="M24 44V22" stroke="#f0fdf4" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

// -------------------------------------------------------------------
// Page Component
// -------------------------------------------------------------------

export default function Home() {
  // ----- chat state -----
  const [authChecking, setAuthChecking] = useState(true);
  const [mode, setMode] = useState<"text" | "audio">("text");
  const [text, setText] = useState("");
  const [language, setLanguage] = useState<LanguageChoice>("auto");
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioInputKey, setAudioInputKey] = useState(0);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activeMessages, setActiveMessages] = useState<HistoryMessage[]>([]);
  const [activeNav, setActiveNav] = useState("Home");
  const [showHistory, setShowHistory] = useState(false);
  const [feedbackRatings, setFeedbackRatings] = useState<
  Record<number, "up" | "down">
  >({});
  const [feedbackLoading, setFeedbackLoading] = useState<number | null>(null);
  const [feedbackError, setFeedbackError] = useState("");

  // ----- history state -----
  const [history, setHistory] = useState<HistorySession[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [search, setSearch] = useState("");

  // ----- market state -----
  const [commodity, setCommodity] = useState("Rice");
  const [marketState, setMarketState] = useState("Uttar Pradesh");
  const [district, setDistrict] = useState("");
  const [marketPrices, setMarketPrices] = useState<MarketPriceResponse | null>(null);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketError, setMarketError] = useState("");

  // ----- weather state -----
  const [weatherCity, setWeatherCity] = useState("Bengaluru");
  const [weatherEntries, setWeatherEntries] = useState<[string, string][]>([]);
  const [weatherLoading, setWeatherLoading] = useState(false);
  const [weatherError, setWeatherError] = useState("");

  // ----- scheme eligibility state -----
  const [schemeForm, setSchemeForm] = useState<SchemeForm>(EMPTY_SCHEME_FORM);
  const [schemeResults, setSchemeResults] = useState<EligibilityResponse | null>(null);
  const [schemeLoading, setSchemeLoading] = useState(false);
  const [schemeError, setSchemeError] = useState("");

  // ----- refs -----
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const recordTimerRef = useRef<number | null>(null);
  const requestIdRef = useRef(0);
  const sessionIdRef = useRef<string | null>(null);
  const languageRef = useRef<LanguageChoice>("auto");
  const lastLanguageRef = useRef<LangCode>("en");
  const conversationRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
 


  useEffect(() => {
  let active = true;

  async function checkFarmerAuth() {
    try {
      const response = await fetch("/api/farmer/me", {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
      });

      if (!response.ok) {
        window.location.replace("/farmer/login");
        return;
      }

      if (active) {
        setAuthChecking(false);
      }
    } catch {
      if (active) {
        window.location.replace("/farmer/login");
      }
    }
  }

  void checkFarmerAuth();

  return () => {
    active = false;
  };
}, []);



  useEffect(() => {
    languageRef.current = language;
  }, [language]);

  useEffect(() => {
    const scroller = conversationRef.current;
    if (!scroller) return;

    const isTool = ["Weather", "Market Prices", "Schemes", "Farming Tips"].includes(activeNav);
    if (isTool || (activeMessages.length === 0 && !loading)) {
      scroller.scrollTo({ top: 0 });
      return;
    }

    if (!loading && result) {
      document.getElementById("latest-answer")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
    }
  }, [activeMessages, loading, result, activeNav]);

  const applySession = useCallback((id: string | null) => {
    sessionIdRef.current = id;
    setSessionId(id);
    try {
      if (id) localStorage.setItem(SESSION_STORAGE_KEY, id);
      else localStorage.removeItem(SESSION_STORAGE_KEY);
    } catch {
      // Storage unavailable
    }
  }, []);

  const stopSpeaking = useCallback(() => {
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    setIsSpeaking(false);
  }, []);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError("");

    try {
      const response = await apiFetch("/chat/my-history");

      if (response.status === 403) {
        throw new Error("Listing previous chats is turned off on this server.");
      }
      if (!response.ok) {
        throw new Error(await readError(response, "Unable to load previous chats."));
      }

      const data = await response.json();
      setHistory(data.sessions ?? []);
    } catch (err) {
      setHistoryError(errMsg(err, "Unable to load previous chats."));
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const loadSession = useCallback(
    async (id: string, silent = false): Promise<HistoryMessage[] | null> => {
      try {
        const response = await apiFetch(`/chat/history/${encodeURIComponent(id)}`);
        if (!response.ok) {
          throw new Error(await readError(response, "Unable to open this conversation."));
        }
        const data = await response.json();
        return (data.messages ?? []) as HistoryMessage[];
      } catch (err) {
        if (!silent) setError(errMsg(err, "Unable to open this conversation."));
        return null;
      }
    },
    []
  );

  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(SESSION_STORAGE_KEY);
    } catch {
      saved = null;
    }

    if (saved && SESSION_ID_PATTERN.test(saved)) {
      const id = saved;
      applySession(id);
      void (async () => {
        const messages = await loadSession(id, true);
        if (messages) setActiveMessages(messages);
      })();
    } else if (saved) {
      applySession(null);
    }

    void loadHistory();
  }, [applySession, loadHistory, loadSession]);


  // Restore the last conversation from local storage when offline.
useEffect(() => {
  if (navigator.onLine) return;

  try {
    const cached = localStorage.getItem(OFFLINE_CHAT_CACHE_KEY);

    if (cached) {
      const messages = JSON.parse(cached);

      if (Array.isArray(messages) && messages.length > 0) {
        setActiveMessages(messages);
      }
    }
  } catch {
    console.warn("Unable to restore the offline conversation.");
  }
}, []);

// Keep the latest conversation available offline.
useEffect(() => {
  if (activeMessages.length === 0) return;

  try {
    localStorage.setItem(
      OFFLINE_CHAT_CACHE_KEY,
      JSON.stringify(activeMessages)
    );
  } catch {
    console.warn("Unable to save the offline conversation.");
  }
}, [activeMessages]);




  useEffect(() => {
    return () => {
      if (recordTimerRef.current) window.clearTimeout(recordTimerRef.current);

      const recorder = mediaRecorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        recorder.onstop = null;
        recorder.stop();
      }
      streamRef.current?.getTracks().forEach((track) => track.stop());

      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    };
  }, []);

  const submitMessage = useCallback(
    async (message: string, lang: LangCode): Promise<boolean> => {
      const requestId = ++requestIdRef.current;

      setLoading(true);
      setError("");
      setResult(null);
      stopSpeaking();

      const pending: HistoryMessage = {
        role: "user",
        message,
        language: lang,
        intent: null,
        created_at: new Date().toISOString(),
      };
      setActiveMessages((old) => [...old, pending]);

      try {
        const response = await apiFetch(
          "/chat",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              message,
              language: lang,
              session_id: sessionIdRef.current,
            }),
          },
          CHAT_TIMEOUT_MS
        );

        if (!response.ok) {
          throw new Error(await readError(response, "The assistant could not answer"));
        }

        const data: AnalysisResult = await response.json();
        if (requestId !== requestIdRef.current) return false;

        setResult(data);
        applySession(data.session_id);
        if (isLangCode(data.language)) lastLanguageRef.current = data.language;

        const messages = await loadSession(data.session_id, true);
        if (requestId !== requestIdRef.current) return false;

        if (messages) {
          setActiveMessages(messages);
        } else {
          const now = new Date().toISOString();
          setActiveMessages((old) => [
            ...old.filter((item) => item !== pending),
            { role: "user", message, language: lang, intent: data.intent, created_at: now },
            { role: "assistant", message: data.answer, language: data.language, intent: data.intent, created_at: now },
          ]);
        }

        void loadHistory();
        return true;
      } catch (err) {
        if (requestId === requestIdRef.current) {
          setError(errMsg(err, "Unable to get an answer."));
          setActiveMessages((old) => old.filter((item) => item !== pending));
        }
        return false;
      } finally {
        if (requestId === requestIdRef.current) setLoading(false);
      }
    },
    [applySession, loadHistory, loadSession, stopSpeaking]
  );

  async function detectLanguage(message: string): Promise<LangCode | null> {
    try {
      const response = await apiFetch("/detect-language", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: message }),
      });
      if (!response.ok) return null;

      const data = await response.json();
      return isLangCode(data.language) ? data.language : null;
    } catch {
      return null;
    }
  }


  async function submitFeedback(
  messageId: number,
  rating: "up" | "down"
) {
  if (!sessionId || feedbackLoading !== null) return;

  setFeedbackLoading(messageId);
  setFeedbackError("");

  try {
    const response = await apiFetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        message_id: messageId,
        rating,
      }),
    });

    if (!response.ok) {
      throw new Error(
        await readError(response, "Unable to submit feedback.")
      );
    }

    setFeedbackRatings((old) => ({
      ...old,
      [messageId]: rating,
    }));
  } catch (err) {
    setFeedbackError(
      errMsg(err, "Unable to submit feedback.")
    );
  } finally {
    setFeedbackLoading(null);
  }
}

  function chooseLanguage(detected: unknown): LangCode {
    const selected = languageRef.current;
    if (isLangCode(selected)) return selected;
    if (isLangCode(detected)) return detected;

    setNotice(
      `Could not detect the language, so ${LANG_NAMES[lastLanguageRef.current]} was used. ` +
        "Pick a language above if that is wrong."
    );
    return lastLanguageRef.current;
  }

  async function analyzeText(event: FormEvent) {
    event.preventDefault();

    const message = text.trim();
    if (!message || loading) return;

    setLoading(true);
    setNotice("");

    const detected = isLangCode(languageRef.current)
      ? null
      : await detectLanguage(message);

    setText("");
    const before = requestIdRef.current;
    const ok = await submitMessage(message, chooseLanguage(detected));

    if (!ok && requestIdRef.current === before + 1) {
      setText((current) => current || message);
    }
  }

  async function transcribeAndSend(file: Blob, filename: string): Promise<boolean> {
    if (file.size > MAX_AUDIO_BYTES) {
      setError("The audio is too large (maximum 10 MB).");
      return false;
    }

    setLoading(true);
    setError("");
    setNotice("");
    setResult(null);

    let transcript = "";
    let detected: unknown;

    try {
      const formData = new FormData();
      formData.append("file", file, filename);

      const response = await apiFetch(
        "/speech/transcribe",
        { method: "POST", body: formData },
        TRANSCRIBE_TIMEOUT_MS
      );

      if (!response.ok) {
        throw new Error(await readError(response, "Speech recognition failed"));
      }

      const data = await response.json();
      transcript = typeof data.transcript === "string" ? data.transcript.trim() : "";
      detected = data.detected_language;

      if (!transcript) {
        throw new Error("No speech was recognized. Please try again.");
      }
    } catch (err) {
      setError(errMsg(err, "Unable to process the audio."));
      setLoading(false);
      return false;
    }

    const ok = await submitMessage(transcript, chooseLanguage(detected));
    setText(ok ? "" : transcript);
    return ok;
  }

  function releaseMicrophone() {
    if (recordTimerRef.current) window.clearTimeout(recordTimerRef.current);
    recordTimerRef.current = null;

    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;

    setIsListening(false);
  }

  async function handleRecordingStop(recorder: MediaRecorder) {
    releaseMicrophone();

    const type = recorder.mimeType || "audio/webm";
    const blob = new Blob(audioChunksRef.current, { type });
    audioChunksRef.current = [];
    mediaRecorderRef.current = null;

    if (!blob.size) {
      setError("No audio was recorded. Please try again.");
      return;
    }

    let upload: Blob = blob;
    let filename = recordingFileName(type);

    try {
      upload = await convertToWav(blob);
      filename = "microphone.wav";

      if (upload.size < 10_000) {
        setError("The recording was too short. Please speak for at least a second.");
        return;
      }
    } catch (err) {
      console.warn("WAV conversion failed; sending the original recording.", err);
    }

    await transcribeAndSend(upload, filename);
  }

  async function toggleRecording() {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
      return;
    }
    if (loading) return;

    if (
      typeof MediaRecorder === "undefined" ||
      !navigator.mediaDevices?.getUserMedia
    ) {
      setError("Voice recording is not supported in this browser.");
      return;
    }

    setError("");
    setNotice("");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mimeType = pickMimeType();
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);

      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        void handleRecordingStop(recorder);
      };

      recorder.start();
      setIsListening(true);

      recordTimerRef.current = window.setTimeout(() => {
        if (recorder.state === "recording") recorder.stop();
      }, MAX_RECORDING_MS);
    } catch (err) {
      releaseMicrophone();
      mediaRecorderRef.current = null;
      setError(micErrorMessage(err));
    }
  }

  function onAudioFileChange(file: File | null) {
    if (file && file.size > MAX_AUDIO_BYTES) {
      setError("That file is too large (maximum 10 MB).");
      setAudioFile(null);
      setAudioInputKey((key) => key + 1);
      return;
    }
    setError("");
    setAudioFile(file);
  }

  async function analyzeAudio(event: FormEvent) {
    event.preventDefault();
    if (!audioFile || loading) return;

    const ok = await transcribeAndSend(audioFile, audioFile.name);
    if (ok) {
      setAudioFile(null);
      setAudioInputKey((key) => key + 1);
    }
  }

  function toggleSpeech() {
    if (isSpeaking) {
      stopSpeaking();
      return;
    }
    if (!result) return;

    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setError("Speech playback is not supported in this browser.");
      return;
    }

    const textToSpeak = answerToSpeech(result.intent, result.answer, result.language).trim();
    if (!textToSpeak) {
      setError("There is no answer to speak.");
      return;
    }

    const synth = window.speechSynthesis;
    synth.cancel();
    setError("");

    const lang: LangCode = isLangCode(result.language) ? result.language : "en";
    const targetLanguage = SPEECH_LANG[lang];

    let started = false;
    const speak = () => {
      if (started) return;
      started = true;

      const voices = synth.getVoices();
      const voice =
        voices.find(
          (item) => item.lang.replace("_", "-").toLowerCase() === targetLanguage.toLowerCase()
        ) ?? voices.find((item) => item.lang.toLowerCase().startsWith(lang));

      if (!voice && lang !== "en") {
        setNotice(
          `No ${LANG_NAMES[lang]} voice is installed in this browser, so playback may sound wrong.`
        );
      }

      const utterance = new SpeechSynthesisUtterance(textToSpeak);
      utterance.lang = targetLanguage;
      utterance.rate = 0.9;
      if (voice) utterance.voice = voice;

      utterance.onend = () => setIsSpeaking(false);
      utterance.onerror = (event) => {
        setIsSpeaking(false);
        if (event.error !== "canceled" && event.error !== "interrupted") {
          setError(`Speech playback failed: ${event.error}`);
        }
      };

      setIsSpeaking(true);
      synth.speak(utterance);
    };

    if (synth.getVoices().length > 0) {
      speak();
    } else {
      synth.addEventListener("voiceschanged", speak, { once: true });
      window.setTimeout(speak, 1000);
    }
  }


  async function handleLogout() {
  const confirmed = window.confirm("Are you sure you want to logout?");
  if (!confirmed) return;

  try {
    await fetch("/api/farmer/logout", {
      method: "POST",
      credentials: "same-origin",
    });
  } finally {
    // Clear farmer-specific local conversation data.
    localStorage.removeItem(SESSION_STORAGE_KEY);
    localStorage.removeItem(OFFLINE_CHAT_CACHE_KEY);

    window.location.replace("/farmer/login");
  }
}

  function startNewChat() {
  requestIdRef.current++;
  stopSpeaking();
  applySession(null);

  // Clear the previous offline conversation.
  try {
    localStorage.removeItem(OFFLINE_CHAT_CACHE_KEY);
  } catch {
    console.warn("Unable to clear the offline conversation.");
  }

  setActiveMessages([]);
  setResult(null);
  setText("");
  setError("");
  setNotice("");
  setLoading(false);
}

  async function openPreviousChat(id: string) {
    requestIdRef.current++;
    stopSpeaking();
    setError("");
    setNotice("");
    setLoading(false);

    const messages = await loadSession(id);
    if (!messages) return;

    applySession(id);
    setActiveMessages(messages);
    setResult(null);
    setText("");

    const lastLang = [...messages].reverse().find((item) => isLangCode(item.language));
    if (lastLang && isLangCode(lastLang.language)) {
      lastLanguageRef.current = lastLang.language;
    }
  }

  async function deletePreviousChat(id: string) {
    if (!window.confirm("Delete this conversation permanently?")) return;

    setError("");

    try {
      const response = await apiFetch(`/chat/history/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });

      if (!response.ok && response.status !== 404) {
        throw new Error(await readError(response, "Unable to delete this conversation."));
      }

      setHistory((old) => old.filter((item) => item.session_id !== id));

      if (sessionIdRef.current === id) startNewChat();
    } catch (err) {
      setError(errMsg(err, "Unable to delete the conversation."));
    }
  }

  async function deleteAllChats() {
    if (
      !window.confirm(
        "Permanently delete ALL saved conversations? This cannot be undone."
      )
    ) {
      return;
    }

    setError("");

    try {
      const response = await apiFetch("/chat/history", { method: "DELETE" });

      if (!response.ok) {
        throw new Error(await readError(response, "Unable to clear chat history."));
      }

      setHistory([]);
      startNewChat();
    } catch (err) {
      setError(errMsg(err, "Unable to clear chat history."));
    }
  }

  async function searchMarketPrices(event: FormEvent) {
    event.preventDefault();

    const crop = commodity.trim();
    if (!crop || marketLoading) return;

    setMarketLoading(true);
    setMarketError("");
    setMarketPrices(null);

    try {
      const params = new URLSearchParams({ commodity: crop, limit: "5" });
      if (marketState.trim()) params.set("state", marketState.trim());
      if (district.trim()) params.set("district", district.trim());

      const response = await apiFetch(`/market-prices?${params.toString()}`, {}, 40_000);

      if (!response.ok) {
        if (response.status === 503) {
          throw new Error(
            "Live mandi prices are temporarily unavailable. Please try again later."
          );
        }
        throw new Error(await readError(response, "Market prices could not be loaded"));
      }

      setMarketPrices((await response.json()) as MarketPriceResponse);
    } catch (err) {
      setMarketError(errMsg(err, "Unable to load market prices."));
    } finally {
      setMarketLoading(false);
    }
  }

  async function searchWeather(event: FormEvent) {
    event.preventDefault();

    const city = weatherCity.trim();
    if (city.length < 2 || weatherLoading) return;

    setWeatherLoading(true);
    setWeatherError("");
    setWeatherEntries([]);

    try {
      const params = new URLSearchParams({ city });
      const response = await apiFetch(`/weather?${params.toString()}`);

      if (!response.ok) {
        if (response.status === 503) {
          throw new Error("Live weather is temporarily unavailable. Please try again later.");
        }
        throw new Error(await readError(response, "Weather could not be loaded"));
      }

      const entries = flattenEntries(await response.json());
      if (!entries.length) throw new Error("No weather details were returned.");
      setWeatherEntries(entries.slice(0, 14));
    } catch (err) {
      setWeatherError(errMsg(err, "Unable to load the weather."));
    } finally {
      setWeatherLoading(false);
    }
  }

  async function checkSchemes(event: FormEvent) {
    event.preventDefault();
    if (schemeLoading) return;

    setSchemeLoading(true);
    setSchemeError("");
    setSchemeResults(null);

    try {
      const hectares = Number.parseFloat(schemeForm.land_hectares);

      const response = await apiFetch("/schemes/eligibility", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          state: schemeForm.state || null,
          land_hectares: Number.isFinite(hectares) && hectares >= 0 ? hectares : null,
          owns_land: triToBool(schemeForm.owns_land),
          is_tenant_or_sharecropper: triToBool(schemeForm.is_tenant_or_sharecropper),
          grows_notified_crop: triToBool(schemeForm.grows_notified_crop),
          pays_income_tax: triToBool(schemeForm.pays_income_tax),
          govt_employee_or_pensioner: triToBool(schemeForm.govt_employee_or_pensioner),
          institutional_landholder: triToBool(schemeForm.institutional_landholder),
          holds_constitutional_post: triToBool(schemeForm.holds_constitutional_post),
          practicing_professional: triToBool(schemeForm.practicing_professional),
        }),
      });

      if (!response.ok) {
        throw new Error(await readError(response, "The eligibility check failed"));
      }

      setSchemeResults((await response.json()) as EligibilityResponse);
    } catch (err) {
      setSchemeError(errMsg(err, "Unable to check eligibility."));
    } finally {
      setSchemeLoading(false);
    }
  }

  function fillPrompt(prompt: string) {
    setMode("text");
    setActiveNav("Home");
    setText(prompt);
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  const navItems: { label: string; icon: IconName; onClick: () => void }[] = [
    { label: "Home", icon: "homeS", onClick: () => {} },
    { label: "Crops", icon: "cropS", onClick: () => fillPrompt("Best practices for crop care") },
    { label: "Pest & Disease", icon: "bugS", onClick: () => fillPrompt("How do I manage pests in my crop?") },
    { label: "Weather", icon: "cloudS", onClick: () => {} },
    { label: "Market Prices", icon: "chartS", onClick: () => {} },
    { label: "Schemes", icon: "landmarkS", onClick: () => {} },
    { label: "Farming Tips", icon: "bulbS", onClick: () => {} },
  ];

  const toolView = ["Weather", "Market Prices", "Schemes", "Farming Tips"].includes(activeNav);

  const filteredHistory = history.filter((item) =>
    (item.first_question || "Conversation")
      .toLowerCase()
      .includes(search.toLowerCase())
  );

  const resultSources: SourceItem[] = result
    ? result.sources && result.sources.length > 0
      ? result.sources
      : result.source_name
        ? [{ source_name: result.source_name, source_url: result.source_url }]
        : []
    : [];

  const entities = result?.entities ?? { crops: [], topics: [], locations: [] };

  const chatActive = activeMessages.length > 0 || loading;
  const lastAssistantIndex = activeMessages.map((item) => item.role).lastIndexOf("assistant");

  if (authChecking) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-green-50">
      <p className="text-lg font-medium text-green-800">
        Verifying your account...
      </p>
    </main>
  );
}


  return (
    <div className="h-dvh overflow-hidden bg-[#f6faf6] text-slate-800">
      <div className="flex h-full flex-col lg:flex-row">
        {/* ============ Sidebar ============ */}
        <aside className="relative w-full shrink-0 overflow-hidden border-b border-slate-100 bg-white/90 lg:h-full lg:w-[265px] lg:border-b-0 lg:border-r">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/images/leaf-bottom.png"
            alt=""
            className={`side-leaf pointer-events-none absolute bottom-0 left-0 hidden w-full transition-opacity ${
              showHistory ? "lg:opacity-0" : "lg:block"
            }`}
          />

          <div className="relative z-10 flex min-h-0 flex-col px-3 pb-3 pt-4 lg:h-full lg:overflow-y-auto lg:px-4 lg:pb-0 lg:pt-6">
            <div className="flex items-center gap-2.5 px-0.5">
              <span className="shrink-0 text-green-600">
                <Logo className="h-12 w-12" />
              </span>
              <div>
                <p className="text-[19px] font-semibold leading-[1.15] text-slate-900">
                  Smart Farming
                  <br />
                  Assistant
                </p>
                <p className="mt-1 whitespace-nowrap text-[12px] text-slate-500">AI guidance for Indian farmers</p>
              </div>

              {chatActive && !toolView && (
                <button
                  type="button"
                  onClick={startNewChat}
                  className="ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm lg:hidden"
                >
                  <Icon name="plus" className="h-4 w-4" /> New chat
                </button>
              )}
            </div>

            <nav aria-label="Sections" className={`-mx-1 mt-3 flex gap-1 overflow-x-auto px-1 pb-1 lg:mx-0 lg:mt-7 lg:flex-col lg:gap-[3px] lg:overflow-visible lg:px-0 lg:pb-0 ${chatActive && !toolView ? "max-lg:hidden" : ""}`}>
              {navItems.map((item) => {
                const active = activeNav === item.label;
                return (
                  <button
                    key={item.label}
                    type="button"
                    onClick={() => {
                      setActiveNav(item.label);
                      item.onClick();
                      window.scrollTo({ top: 0, behavior: "smooth" });
                    }}
                    aria-current={active ? "page" : undefined}
                    className={`nav-row flex shrink-0 items-center gap-2.5 whitespace-nowrap rounded-xl px-3.5 py-2.5 text-left text-[15.5px] transition-colors lg:gap-4 lg:px-5 lg:py-3.5 ${
                      active
                        ? "bg-[#e6f4e8] font-medium text-green-800"
                        : "text-slate-800 hover:bg-slate-50"
                    }`}
                  >
                    <Icon name={item.icon} className={`h-6 w-6 ${active ? "text-green-700" : "text-slate-500"}`} />
                    {item.label}
                  </button>
                );
              })}
            </nav>


            <button
  type="button"
  onClick={handleLogout}
  className="mt-3 flex w-full items-center gap-3 rounded-xl px-5 py-3 text-left text-sm font-medium text-red-600 transition-colors hover:bg-red-50"
>
  <span>↪</span>
  Logout
</button>

            <div className="mx-3 mt-5 hidden border-t border-slate-200 lg:block" />

            <div className={`mt-3 flex min-h-0 flex-col lg:flex-1 ${showHistory ? "lg:min-h-[320px]" : ""} ${chatActive && !toolView ? "max-lg:hidden" : ""}`}>
              <div className="flex items-center">
                <button
                  type="button"
                  onClick={() => setShowHistory((open) => !open)}
                  aria-expanded={showHistory}
                  className="nav-row flex flex-1 items-center gap-2.5 rounded-xl px-3.5 py-2.5 text-left text-[15.5px] text-slate-800 hover:bg-slate-50 lg:gap-4 lg:px-5 lg:py-3.5"
                >
                  <Icon name="clock" className="h-6 w-6 text-slate-500" />
                  Recent Chats
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setActiveNav("Home");
                    startNewChat();
                  }}
                  title="New chat"
                  aria-label="New chat"
                  className="flex h-9 w-9 items-center justify-center rounded-full text-slate-500 hover:bg-slate-100"
                >
                  <Icon name="plus" className="h-5 w-5" />
                </button>
              </div>

              {showHistory && (
                <div className="mt-1 flex min-h-0 flex-col rounded-xl bg-white/80 p-2 lg:flex-1">
                  <div className="mb-2 flex items-center gap-2">
                    <input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Search conversations..."
                      aria-label="Search conversations"
                      className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm placeholder:text-slate-400"
                    />
                    {history.length > 0 && (
                      <button
                        type="button"
                        onClick={deleteAllChats}
                        className="shrink-0 text-xs text-red-500 hover:text-red-700"
                      >
                        Clear all
                      </button>
                    )}
                  </div>

                  {historyLoading && (
                    <p className="text-sm text-slate-500">Loading previous chats...</p>
                  )}

                  {historyError && (
                    <p role="alert" className="text-sm text-red-500">
                      {historyError}
                    </p>
                  )}

                  {!historyLoading && !historyError && history.length === 0 && (
                    <p className="text-sm text-slate-500">
                      No previous conversations yet.
                    </p>
                  )}

                  <div className="mt-2 max-h-64 min-h-0 space-y-1 overflow-y-auto lg:max-h-none lg:min-h-[180px] lg:flex-1">
                    {filteredHistory.map((item) => (
                      <div
                        key={item.session_id}
                        className={`mb-1 flex items-center gap-1 rounded-lg hover:bg-slate-100 ${
                          sessionId === item.session_id ? "bg-green-50" : ""
                        }`}
                      >
                        <button
                          type="button"
                          onClick={() => {
                            setActiveNav("Home");
                            void openPreviousChat(item.session_id);
                          }}
                          className="min-w-0 flex-1 px-3 py-3 text-left"
                        >
                          <span className="line-clamp-2 block text-sm font-medium">
                            {item.first_question || "Conversation"}
                          </span>

                          <span className="mt-1 block text-xs text-slate-500">
                            {formatTime(item.last_message_at)}
                          </span>
                        </button>

                        <button
                          type="button"
                          title="Delete conversation"
                          aria-label={`Delete conversation: ${item.first_question || "Conversation"}`}
                          onClick={() => deletePreviousChat(item.session_id)}
                          className="mr-2 rounded-lg px-3 py-2 text-red-500 hover:bg-red-50"
                        >
                          🗑️
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className={`side-foot mt-4 hidden pb-4 lg:mt-auto lg:block ${showHistory ? "lg:pb-4" : "lg:pb-40"}`}>
              <p className="nav-row flex items-center gap-4 px-5 py-3.5 text-[15.5px] text-slate-800">
                <Icon name="settingsS" className="h-6 w-6 text-slate-600" /> Settings
              </p>
              <p className="nav-row flex items-center gap-4 px-5 py-3.5 text-[15.5px] text-slate-800">
                <Icon name="helpS" className="h-6 w-6 text-slate-600" /> Help
              </p>
            </div>
          </div>
        </aside>

        {/* ============ Main ============ */}
        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/images/leaf-top.png"
            alt=""
            className={`pointer-events-none absolute left-0 top-0 hidden h-40 w-auto transition-opacity duration-500 lg:block ${
              chatActive && !toolView ? "opacity-0" : "opacity-100"
            }`}
          />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/images/landscape.jpg"
            alt=""
            className={`pointer-events-none absolute inset-x-0 bottom-0 z-0 h-[150px] w-full object-cover object-bottom transition-opacity duration-500 sm:h-[230px] ${
              chatActive && !toolView ? "opacity-0" : "opacity-100"
            }`}
            style={{
              maskImage: "linear-gradient(to bottom, transparent 0%, black 30%)",
              WebkitMaskImage: "linear-gradient(to bottom, transparent 0%, black 30%)",
            }}
          />
          {toolView && (
            <p className="pointer-events-none absolute inset-x-0 bottom-2 z-10 px-4 text-center text-[11px] font-medium text-white [text-shadow:0_1px_3px_rgba(0,0,0,0.8)]">
              Prototype: model predictions must be validated before using them for real agricultural advice.
            </p>
          )}

          {/* Top bar */}
          <header className="relative z-10 hidden shrink-0 items-center justify-between gap-4 px-5 pb-2 pt-[18px] sm:flex lg:pr-8">
            {chatActive && !toolView ? (
              <button
                type="button"
                onClick={startNewChat}
                className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white/90 px-4 py-2.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-white"
              >
                <Icon name="plus" className="h-4 w-4" /> New chat
              </button>
            ) : (
              <span />
            )}

            <div className="flex items-center gap-4">
              <label className="relative flex h-[46px] items-center gap-3 rounded-2xl border border-slate-200 bg-white pl-4 pr-3 shadow-sm">
                <Icon name="globe" className="h-6 w-6 text-slate-700" />
                <select
                  value={language}
                  onChange={(e) => setLanguage(e.target.value as LanguageChoice)}
                  aria-label="Language"
                  className="appearance-none bg-transparent pr-6 text-[16px] font-medium text-slate-800 outline-none"
                >
                  <option value="auto">Auto-detect</option>
                  <option value="en">English</option>
                  <option value="hi">हिंदी</option>
                  <option value="kn">ಕನ್ನಡ</option>
                </select>
                <Icon name="chevron" className="pointer-events-none absolute right-3 h-4 w-4 text-slate-600" />
              </label>
              <span className="relative flex h-12 w-12 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-800 shadow-sm">
                <Icon name="bell" className="h-6 w-6" />
                <span className="absolute right-3 top-3 h-2.5 w-2.5 rounded-full bg-red-500" />
              </span>
              <span className="flex h-12 w-12 items-center justify-center rounded-full bg-green-800 text-xl font-medium text-white">
                S
              </span>
            </div>
          </header>

          {/* Scrollable area */}
          <div ref={conversationRef} className="relative z-10 min-h-0 flex-1 overflow-y-auto">
            {toolView ? (
              <div className="mx-auto w-full max-w-4xl space-y-6 px-5 pb-12 pt-4 lg:px-10">
                <button
                  type="button"
                  onClick={() => setActiveNav("Home")}
                  className="inline-flex items-center gap-2 rounded-full bg-white/90 px-4 py-2 text-sm font-medium text-green-800 shadow-sm"
                >
                  <Icon name="right" className="h-4 w-4 rotate-180" /> Back to home
                </button>

                {activeNav === "Weather" && (
                  <section className="rounded-3xl border border-slate-200 bg-white/95 p-5 shadow-sm">
                    <h2 className="font-semibold">Weather by City</h2>

                    <form onSubmit={searchWeather} className="mt-3 flex gap-2">
                      <input
                        value={weatherCity}
                        onChange={(e) => setWeatherCity(e.target.value)}
                        aria-label="City"
                        placeholder="Enter a city"
                        className="min-w-0 flex-1 rounded-lg border px-3 py-2 text-sm"
                      />
                      <button
                        type="submit"
                        disabled={weatherLoading || weatherCity.trim().length < 2}
                        className="rounded-lg bg-green-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
                      >
                        {weatherLoading ? "..." : "Get"}
                      </button>
                    </form>

                    {weatherError && (
                      <p role="alert" className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                        {weatherError}
                      </p>
                    )}

                    {weatherEntries.length > 0 ? (
                      <div className="mt-3">
                        <EntryList entries={weatherEntries} />
                      </div>
                    ) : (
                      !weatherError && (
                        <p className="mt-3 text-sm text-gray-500">
                          Enter a city to see its current weather.
                        </p>
                      )
                    )}
                  </section>
                )}

                {activeNav === "Market Prices" && (
                  <section className="rounded-3xl border border-slate-200 bg-white/95 p-5 shadow-sm">
                    <h2 className="text-lg font-semibold">📊 Live Mandi Market Prices</h2>

                    <p className="mt-1 text-sm text-gray-500">
                      Search wholesale market prices by crop and location.
                    </p>

                    <datalist id="commodity-options">
                      {COMMODITY_OPTIONS.map((name) => (
                        <option key={name} value={name} />
                      ))}
                    </datalist>

                    <form
                      onSubmit={searchMarketPrices}
                      className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
                    >
                      <label className="text-sm">
                        Crop / Commodity
                        <input
                          value={commodity}
                          onChange={(e) => setCommodity(e.target.value)}
                          list="commodity-options"
                          required
                          placeholder="Rice"
                          className="mt-1 w-full rounded-lg border px-3 py-2"
                        />
                      </label>

                      <label className="text-sm">
                        State (optional)
                        <select
                          value={marketState}
                          onChange={(e) => setMarketState(e.target.value)}
                          className="mt-1 w-full rounded-lg border bg-white px-3 py-2"
                        >
                          <option value="">All states</option>
                          {STATE_OPTIONS.map((name) => (
                            <option key={name} value={name}>
                              {name}
                            </option>
                          ))}
                        </select>
                      </label>

                      <label className="text-sm">
                        District (optional)
                        <input
                          value={district}
                          onChange={(e) => setDistrict(e.target.value)}
                          placeholder="Kanpur"
                          className="mt-1 w-full rounded-lg border px-3 py-2"
                        />
                      </label>

                      <button
                        type="submit"
                        disabled={marketLoading}
                        className="self-end rounded-lg bg-green-700 px-4 py-2 font-medium text-white disabled:opacity-50"
                      >
                        {marketLoading ? "Searching..." : "Search Prices"}
                      </button>
                    </form>

                    {marketError && (
                      <p
                        role="alert"
                        className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800"
                      >
                        {marketError}
                      </p>
                    )}

                    {marketPrices && (
                      <div className="mt-5">
                        <p className="mb-3 text-sm text-gray-600">
                          Source: {marketPrices.source} · {marketPrices.count} records
                          {marketPrices.latest_arrival_date
                            ? ` · latest data: ${marketPrices.latest_arrival_date}`
                            : ""}
                        </p>

                        {marketPrices.data_stale && marketPrices.count > 0 && (
                          <p className="mb-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                            These prices are a few days old and may not reflect today&apos;s market.
                          </p>
                        )}

                        {marketPrices.records.length === 0 ? (
                          <p className="text-sm text-gray-500">
                            No matching records found. Try another crop or location.
                          </p>
                        ) : (
                          <MarketTable records={marketPrices.records} />
                        )}

                        {marketPrices.note && (
                          <p className="mt-3 text-xs text-gray-500">{marketPrices.note}</p>
                        )}
                      </div>
                    )}
                  </section>
                )}

                {activeNav === "Schemes" && (
                  <section className="rounded-3xl border border-slate-200 bg-white/95 p-5 shadow-sm">
                    <h2 className="text-lg font-semibold">🏛️ Scheme Eligibility Checker</h2>
                    <p className="mt-1 text-sm text-gray-500">
                      Answer a few questions to see whether PM-KISAN, crop insurance (PMFBY) and the Kisan Credit Card may apply to you.
                    </p>

                    <form onSubmit={checkSchemes} className="mt-4 grid gap-3 sm:grid-cols-2">
                      <label className="text-sm">
                        State (optional)
                        <select
                          value={schemeForm.state}
                          onChange={(e) => setSchemeForm((old) => ({ ...old, state: e.target.value }))}
                          className="mt-1 w-full rounded-lg border bg-white px-3 py-2"
                        >
                          <option value="">Select your state</option>
                          {STATE_OPTIONS.map((name) => (
                            <option key={name} value={name}>{name}</option>
                          ))}
                        </select>
                      </label>

                      <label className="text-sm">
                        Land you own or cultivate, in hectares (optional)
                        <input
                          type="number"
                          min="0"
                          step="0.1"
                          value={schemeForm.land_hectares}
                          onChange={(e) => setSchemeForm((old) => ({ ...old, land_hectares: e.target.value }))}
                          placeholder="1.5"
                          className="mt-1 w-full rounded-lg border px-3 py-2"
                        />
                      </label>

                      {SCHEME_QUESTIONS.map((question) => (
                        <label key={question.key} className="text-sm">
                          {question.label}
                          <select
                            value={schemeForm[question.key]}
                            onChange={(e) =>
                              setSchemeForm((old) => ({ ...old, [question.key]: e.target.value as TriState }))
                            }
                            className="mt-1 w-full rounded-lg border bg-white px-3 py-2"
                          >
                            <option value="">Not sure</option>
                            <option value="yes">Yes</option>
                            <option value="no">No</option>
                          </select>
                        </label>
                      ))}

                      <div className="flex gap-2 sm:col-span-2">
                        <button
                          type="submit"
                          disabled={schemeLoading}
                          className="rounded-lg bg-green-700 px-4 py-2 font-medium text-white disabled:opacity-50"
                        >
                          {schemeLoading ? "Checking..." : "Check eligibility"}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setSchemeForm(EMPTY_SCHEME_FORM);
                            setSchemeResults(null);
                            setSchemeError("");
                          }}
                          className="rounded-lg border px-4 py-2"
                        >
                          Reset
                        </button>
                      </div>
                    </form>

                    {schemeError && (
                      <p role="alert" className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                        {schemeError}
                      </p>
                    )}

                    {schemeResults && (
                      <div className="mt-5 space-y-3">
                        {schemeResults.results.map((scheme) => (
                          <SchemeCard key={scheme.id} scheme={scheme} />
                        ))}
                        <p className="text-xs text-gray-500">{schemeResults.disclaimer}</p>
                        <p className="text-xs text-gray-500">{schemeResults.state_note}</p>
                      </div>
                    )}
                  </section>
                )}

                {activeNav === "Farming Tips" && (
                  <section className="rounded-3xl border border-slate-200 bg-white/95 p-5 shadow-sm">
                    <h2 className="font-semibold">Seasonal Advisory</h2>
                    <div className="mt-3 flex gap-3">
                      <div className="flex-1">
                        <p className="text-sm font-semibold">🌿 Rabi Season</p>
                        <p className="mt-1 text-sm text-gray-500">
                          Good time to sow wheat, chickpea and mustard in your region.
                        </p>
                        <button
                          type="button"
                          onClick={() => fillPrompt("Which crops should I sow in the Rabi season?")}
                          className="mt-3 rounded-lg bg-green-50 px-3 py-2 text-sm font-medium text-green-800"
                        >
                          View Details →
                        </button>
                      </div>
                      <span className="flex h-24 w-24 items-center justify-center rounded-xl bg-green-100 text-5xl">
                        🌱
                      </span>
                    </div>
                  </section>
                )}
              </div>
            ) : chatActive ? (
              <div
                role="log"
                aria-live="polite"
                className="mx-auto w-full max-w-4xl space-y-5 px-4 pb-8 pt-4 sm:px-6"
              >
                {activeMessages.map((item, index) => {
                  const isUser = item.role === "user";
                  const isLatestAnswer = !isUser && index === lastAssistantIndex;

                  return (
                    <div
                      key={item.id ?? `${item.created_at}-${index}`}
                      id={isLatestAnswer ? "latest-answer" : undefined}
                      className={`flex scroll-mt-4 gap-3 ${isUser ? "justify-end" : ""}`}
                    >
                      {!isUser && (
                        <span className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white shadow-sm ring-1 ring-green-100">
                          <Logo className="h-6 w-6" />
                        </span>
                      )}

                      <div
                        className={`min-w-0 max-w-[88%] text-[17px] leading-relaxed sm:max-w-[80%] ${
                          isUser
                            ? "rounded-3xl rounded-br-lg bg-green-700 px-5 py-3 text-white"
                            : "rounded-3xl rounded-tl-lg border border-slate-200 bg-white px-5 py-4 text-slate-800 shadow-sm"
                        }`}
                      >
                        {isUser ? (
                          <p className="whitespace-pre-wrap break-words">{item.message}</p>
                        ) : (
                          <>
                            <AnswerBody intent={item.intent} answer={item.message} />

                            {typeof item.id === "number" && (
                              <div className="mt-3 flex items-center gap-2 border-t border-slate-100 pt-2">
                                <span className="text-xs text-slate-500">Was this helpful?</span>

                                <button
                                  type="button"
                                  aria-label="Helpful answer"
                                  disabled={feedbackLoading === item.id}
                                  onClick={() => void submitFeedback(item.id!, "up")}
                                  className={`rounded-lg px-2 py-1 text-sm ${
                                    feedbackRatings[item.id] === "up"
                                      ? "bg-green-100 text-green-700"
                                      : "text-slate-500 hover:bg-slate-100"
                                  }`}
                                >
                                  👍
                                </button>

                                <button
                                  type="button"
                                  aria-label="Not helpful"
                                  disabled={feedbackLoading === item.id}
                                  onClick={() => void submitFeedback(item.id!, "down")}
                                  className={`rounded-lg px-2 py-1 text-sm ${
                                    feedbackRatings[item.id] === "down"
                                      ? "bg-red-100 text-red-700"
                                      : "text-slate-500 hover:bg-slate-100"
                                  }`}
                                >
                                  👎
                                </button>

                                {feedbackLoading === item.id && (
                                  <span className="text-xs text-slate-400">Saving...</span>
                                )}
                              </div>
                            )}
                          </>
                        )}

                        {item.created_at && (
                          <p className={`mt-1.5 text-right text-xs ${isUser ? "text-green-100" : "text-slate-400"}`}>
                            {formatTime(item.created_at)}
                          </p>
                        )}
                      </div>
                    </div>
                  );
                })}

                {loading && (
                  <div className="flex gap-3" role="status">
                    <span className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white shadow-sm ring-1 ring-green-100">
                      <Logo className="h-6 w-6" />
                    </span>
                    <div className="flex items-center gap-1.5 rounded-3xl rounded-tl-lg border border-slate-200 bg-white px-5 py-4 shadow-sm">
                      <span className="h-2.5 w-2.5 animate-bounce rounded-full bg-green-600 [animation-delay:-0.3s]" />
                      <span className="h-2.5 w-2.5 animate-bounce rounded-full bg-green-600 [animation-delay:-0.15s]" />
                      <span className="h-2.5 w-2.5 animate-bounce rounded-full bg-green-600" />
                      <span className="ml-2 text-sm text-slate-500">Thinking...</span>
                    </div>
                  </div>
                )}

                {!loading && result && (
                  <div className="pl-12">
                    <div className="space-y-4">
                      <div className="rounded-lg bg-green-50 p-4">
                        <p className="text-sm font-medium text-green-800">
                          About this answer
                        </p>

                        <button
                          type="button"
                          onClick={toggleSpeech}
                          className="mt-2 rounded-lg bg-green-700 px-4 py-2 text-white"
                        >
                          {isSpeaking ? "⏹️ Stop" : "🔊 Listen to Answer"}
                        </button>

                        {resultSources.length > 0 && (
                          <div className="mt-3 space-y-1 border-t border-green-200 pt-3">
                            <p className="text-sm font-medium text-green-800">
                              {resultSources.length > 1 ? "Sources" : "Source"}
                            </p>
                            {resultSources.map((source) => {
                              const url = safeUrl(source.source_url);
                              return (
                                <p
                                  key={`${source.source_name}-${source.source_url ?? ""}`}
                                  className="text-sm text-gray-700"
                                >
                                  {source.source_name}
                                  {url && (
                                    <>
                                      {" · "}
                                      <a
                                        href={url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-blue-700 underline"
                                      >
                                        View source
                                      </a>
                                    </>
                                  )}
                                </p>
                              );
                            })}
                          </div>
                        )}
                      </div>

                      {result.needs_review && (
                        <p role="status" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                          This answer has low confidence and has been sent to an extension worker for
                          review. Please verify it with the Kisan Call Centre (1800-180-1551) or your
                          local agriculture office before acting on it.
                        </p>
                      )}

                      <details className="text-sm">
                        <summary className="cursor-pointer font-medium text-gray-700">
                          Technical details
                        </summary>

                        <div className="mt-3 space-y-4">
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

                          <div className="space-y-1">
                            <p className="font-semibold">Extracted entities</p>
                            <p>Crops: {entities.crops.join(", ") || "None"}</p>
                            <p>Topics: {entities.topics.join(", ") || "None"}</p>
                            <p>Locations: {entities.locations.join(", ") || "None"}</p>
                          </div>

                          <p className="break-all text-gray-500">
                            Session ID: {result.session_id}
                          </p>

                          <p className="text-gray-500">
                            Rules decided the intent: {result.fallback_used ? "Yes" : "No"}
                          </p>

                          {typeof result.quality_score === "number" && (
                            <p className="text-gray-500">
                              Answer quality score: {(result.quality_score * 100).toFixed(0)}%
                              {result.needs_review ? " (flagged for review)" : ""}
                            </p>
                          )}

                          <div>
                            <p className="font-semibold">Complete JSON response</p>
                            <pre className="mt-2 overflow-x-auto rounded-lg bg-gray-950 p-4 text-sm text-green-300">
                              {JSON.stringify(result, null, 2)}
                            </pre>
                          </div>
                        </div>
                      </details>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              /* Simplified Home Welcome View */
              <div className="mx-auto flex min-h-full w-full max-w-4xl flex-col justify-center px-5 py-12 text-center lg:px-8">
                <span className="mx-auto inline-flex items-center gap-2 rounded-full border border-white bg-white/80 px-4 py-2 text-[15px] font-semibold text-green-700 shadow-sm">
                  <Logo className="h-6 w-6" /> Your Farming Companion
                </span>

                <h1 className="mt-5 text-5xl font-extrabold leading-tight tracking-tight text-slate-900 sm:text-[60px]">
                  Hello, <span className="text-green-700">Farmer!</span>
                </h1>
                <p className="mt-3 text-[22px] font-medium text-slate-700">
                  Get trusted guidance for better farming decisions.
                </p>
                <p className="mt-2 text-[16px] text-slate-500">
                  Ask questions, explore tools, and access real-time information — all in one place.
                </p>
              </div>
            )}
          </div>

          {/* Question Box (Bottom) */}
          {!toolView && (
            <div
              className={`relative z-10 shrink-0 px-4 pb-3 pt-2 sm:px-6 ${
                chatActive
                  ? "bg-gradient-to-t from-[#f6faf6] via-[#f6faf6]/95 to-transparent"
                  : "sm:pb-5"
              }`}
            >
              <div className="mx-auto w-full max-w-4xl">
                {error && (
                  <p role="alert" className="mb-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">
                    {error}
                  </p>
                )}
                {notice && (
                  <p role="status" className="mb-3 rounded-xl bg-amber-50 p-3 text-sm text-amber-800">
                    {notice}
                  </p>
                )}
                {feedbackError && (
                  <p role="alert" className="mb-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">
                    {feedbackError}
                  </p>
                )}

                <section className="rounded-[28px] border border-slate-200 bg-white/95 shadow-[0_10px_40px_-12px_rgba(22,101,52,0.25)] transition focus-within:border-green-400 focus-within:ring-4 focus-within:ring-green-100">
                  {mode === "text" ? (
                    <form onSubmit={analyzeText} className="px-5 pb-3 pt-4 sm:px-6 sm:pt-5">
                      <textarea
                        id="question"
                        ref={inputRef}
                        value={text}
                        onChange={(event) => setText(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                            event.preventDefault();
                            event.currentTarget.form?.requestSubmit();
                          }
                        }}
                        maxLength={2000}
                        required
                        rows={1}
                        aria-label="Your farming question"
                        placeholder={chatActive ? "Ask another question..." : "Ask anything about farming..."}
                        className="field-sizing-content max-h-40 min-h-[28px] w-full resize-none bg-transparent text-[16px] outline-none placeholder:text-slate-400"
                      />

                      <div className="mt-3 flex flex-wrap items-center gap-2 sm:gap-3">
                        <button
                          type="button"
                          onClick={() => setMode("audio")}
                          aria-label="Upload an audio file"
                          title="Upload an audio file"
                          className="flex h-10 w-10 items-center justify-center rounded-full bg-slate-100 text-slate-700 hover:bg-slate-200 sm:h-12 sm:w-12"
                        >
                          <Icon name="clip" className="h-5 w-5" />
                        </button>
                        <button
                          type="button"
                          onClick={toggleRecording}
                          disabled={loading}
                          aria-label={isListening ? "Stop recording" : "Speak your question"}
                          title={isListening ? "Stop recording" : "Speak your question"}
                          className={`flex h-10 w-10 items-center justify-center rounded-full sm:h-12 sm:w-12 disabled:opacity-50 ${
                            isListening
                              ? "animate-pulse bg-red-100 text-red-600"
                              : "bg-slate-100 text-slate-700 hover:bg-slate-200"
                          }`}
                        >
                          <Icon name={isListening ? "stop" : "mic"} className="h-5 w-5" />
                        </button>
                        <label className="relative flex h-10 items-center gap-2 rounded-full bg-slate-100 pl-3 pr-3 text-[14px] font-medium text-slate-800 sm:h-12 sm:pl-4 sm:pr-4 sm:text-[16px]">
                          <Icon name="globe" className="h-5 w-5" />
                          <select
                            value={language}
                            onChange={(e) => setLanguage(e.target.value as LanguageChoice)}
                            aria-label="Question language"
                            className="appearance-none bg-transparent pr-6 outline-none"
                          >
                            <option value="auto">Auto-detect</option>
                            <option value="en">English</option>
                            <option value="hi">हिंदी</option>
                            <option value="kn">ಕನ್ನಡ</option>
                          </select>
                          <Icon name="chevron" className="pointer-events-none absolute right-3 h-4 w-4" />
                        </label>
                        {isListening && (
                          <span className="text-sm text-red-600">Listening... click to stop</span>
                        )}
                        <button
                          type="submit"
                          disabled={loading || !text.trim()}
                          aria-label="Send question"
                          className="ml-auto flex h-11 w-11 items-center justify-center rounded-full bg-green-700 sm:h-[52px] sm:w-[52px] text-white shadow-md disabled:cursor-not-allowed disabled:opacity-90"
                        >
                          {loading ? "..." : <Icon name="up" className="h-6 w-6" />}
                        </button>
                      </div>
                    </form>
                  ) : (
                    <form
                      onSubmit={analyzeAudio}
                      className="flex flex-wrap items-center gap-3 px-6 py-6"
                    >
                      <input
                        key={audioInputKey}
                        id="audio"
                        type="file"
                        accept="audio/*"
                        required
                        aria-label="Audio file"
                        onChange={(event) =>
                          onAudioFileChange(event.target.files?.[0] ?? null)
                        }
                        className="min-w-0 flex-1 text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-green-50 file:px-3 file:py-2 file:font-medium file:text-green-800"
                      />
                      <button
                        type="submit"
                        disabled={loading || !audioFile}
                        className="rounded-xl bg-green-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                      >
                        {loading ? "Processing..." : "Transcribe and analyze"}
                      </button>
                      <button
                        type="button"
                        onClick={toggleRecording}
                        disabled={loading}
                        className="rounded-xl border px-4 py-2 text-sm disabled:opacity-50"
                      >
                        {isListening ? "Listening... click to stop" : "🎙️ Speak & Analyze"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setMode("text")}
                        className="rounded-xl border px-4 py-2 text-sm"
                      >
                        ✏️ Text input
                      </button>
                    </form>
                  )}
                </section>

                <p
                  className={`mt-2 text-center text-[11px] ${
                    chatActive
                      ? "text-slate-500"
                      : "font-medium text-white [text-shadow:0_1px_3px_rgba(0,0,0,0.8)]"
                  }`}
                >
                  Prototype: model predictions must be validated before using them for real agricultural advice.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}