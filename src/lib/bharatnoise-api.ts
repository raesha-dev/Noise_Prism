export type AccuracyMode = "fast" | "max";

export type ProcessingResult = {
  selected_candidate?: string;
  transcription?: string;
  candidates?: Record<string, string>;
  analysis?: {
    snr_db?: number;
    noise_floor_db?: number;
    speech_energy_db?: number;
    vad_segments?: number[];
    guard_score?: number;
    guard_status?: string;
  };
  evaluation?: {
    wer?: number;
    cer?: number;
    latency_ms?: number;
    calls?: number;
    note?: string;
  };
  pipeline?: string[];
};

const STORAGE_KEY = "bharatnoise.api-base-url";

export function getApiBaseUrl(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(STORAGE_KEY) ?? "";
}

export function saveApiBaseUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "");
  if (trimmed) {
    const url = new URL(trimmed);
    if (url.protocol !== "https:" && url.hostname !== "localhost" && url.hostname !== "127.0.0.1") {
      throw new Error("Use HTTPS for a hosted proxy, or localhost for development.");
    }
    window.localStorage.setItem(STORAGE_KEY, url.toString().replace(/\/+$/, ""));
  } else {
    window.localStorage.removeItem(STORAGE_KEY);
  }
  return trimmed;
}

async function readError(response: Response): Promise<string> {
  const payload = (await response.json().catch(() => null)) as { detail?: unknown; message?: unknown } | null;
  if (typeof payload?.detail === "string") return payload.detail;
  if (typeof payload?.message === "string") return payload.message;
  return `The audio service returned ${response.status}.`;
}

export async function checkApiConnection(baseUrl: string): Promise<void> {
  if (!baseUrl) throw new Error("Add your FastAPI or Flask proxy address first.");
  const response = await fetch(`${baseUrl}/health`, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(await readError(response));
}

export async function processAudioFile(
  baseUrl: string,
  file: File,
  mode: AccuracyMode,
): Promise<ProcessingResult> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("mode", mode);
  form.append("sample_rate", "8000");
  form.append("channels", "1");
  form.append("sample_format", "pcm_s16le");
  form.append("asr_provider", "gnani_prisma");
  form.append("candidates", mode === "fast" ? "auto" : "C0,C1,C2");
  form.append("compare_models", mode === "max" ? "Prisma v2.5,v3.0" : "none");

  const response = await fetch(`${baseUrl}/process`, { method: "POST", body: form });
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as ProcessingResult;
}

export function makeLiveSocketUrl(baseUrl: string): string {
  const url = new URL(`${baseUrl}/live`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("sample_rate", "8000");
  url.searchParams.set("vad_preset", "telephony");
  return url.toString();
}