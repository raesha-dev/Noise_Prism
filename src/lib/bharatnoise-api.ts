/**
 * Browser adapter for a FastAPI/Flask-compatible BharatNoise proxy that forwards audio to
 * Gnani AI Prisma. The full wire contract is documented in docs/proxy-contract.md.
 * Raw responses use snake_case; everything returned from this module is normalized camelCase.
 */
import { z } from "zod";

export type AccuracyMode = "fast" | "max";
export const TRACK_IDS = ["C0", "C1", "C2", "C3"] as const;
export type TrackId = (typeof TRACK_IDS)[number];

export type CandidateResult = {
  transcript?: string | undefined;
  audioUrl?: string | undefined;
  peaks?: number[] | undefined;
  guardScore?: number | undefined;
  confidence?: number | undefined;
  snrDb?: number | undefined;
};

export type ProcessingResult = {
  requestId?: string | undefined;
  selectedCandidate?: TrackId | undefined;
  transcription?: string | undefined;
  candidates: Partial<Record<TrackId, CandidateResult>>;
  analysis: {
    snrDb?: number | undefined;
    noiseFloorDb?: number | undefined;
    speechEnergyDb?: number | undefined;
    vadSegments: [number, number][];
    guardScore?: number | undefined;
    guardStatus?: string | undefined;
    spectrum?: number[] | undefined;
  };
  evaluation?: { wer?: number | undefined; cer?: number | undefined; latencyMs?: number | undefined; calls?: number | undefined; note?: string | undefined } | undefined;
  pipeline: string[];
  latencyMs?: number | undefined;
};

export type HealthInfo = { status: string; gnaniConfigured?: boolean | undefined; version?: string | undefined; latencyMs: number };

export type EvaluationRow = { id: string; pipeline: string; wer?: number | undefined; cer?: number | undefined; latencyMs?: number | undefined; candidate?: string | undefined };
export type EvaluationSection = "comparison" | "ablation" | "snr" | "noise";
export type EvaluationReport = {
  dataset?: string | undefined;
  clipCount?: number | undefined;
  measuredAt?: string | undefined;
  sections: Partial<Record<EvaluationSection, EvaluationRow[]>>;
};

export type ProcessOptions = {
  mode: AccuracyMode;
  candidates: TrackId[];
  asrModel: string;
  compareModels: boolean;
  signal?: AbortSignal | undefined;
  timeoutMs?: number | undefined;
};

const STORAGE_KEY = "bharatnoise.api-base-url";

export function getApiBaseUrl(): string {
  if (typeof window === "undefined") return "";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored !== null) return stored;
  return (import.meta.env["VITE_BHARATNOISE_API_URL"] as string | undefined)?.replace(/\/+$/, "") ?? "";
}

/** Validates and normalizes a proxy base address. Empty input means demo mode. */
export function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "");
  if (!trimmed) return "";
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    throw new Error("Enter a full address, for example https://your-service.example.com");
  }
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(local && url.protocol === "http:")) {
    throw new Error("Use HTTPS for a hosted proxy, or http://localhost for development.");
  }
  return `${url.origin}${url.pathname}`.replace(/\/+$/, "");
}

export function saveApiBaseUrl(value: string): string {
  const normalized = normalizeBaseUrl(value);
  if (normalized) window.localStorage.setItem(STORAGE_KEY, normalized);
  else window.localStorage.setItem(STORAGE_KEY, "");
  return normalized;
}

async function readError(response: Response): Promise<string> {
  const payload = (await response.json().catch(() => null)) as { detail?: unknown | undefined; message?: unknown | undefined; error?: unknown } | null;
  for (const value of [payload?.detail, payload?.message, payload?.error]) {
    if (typeof value === "string") return value;
  }
  if (Array.isArray(payload?.detail)) return "The audio service rejected the request fields (422).";
  return `The audio service returned ${response.status}.`;
}

function withTimeout(signal: AbortSignal | undefined, timeoutMs: number) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), timeoutMs);
  const onAbort = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", onAbort, { once: true });
  return {
    signal: controller.signal,
    done: () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    },
  };
}

function describeFetchError(error: unknown, timeoutMs: number): Error {
  if (error instanceof DOMException && error.name === "AbortError") return new Error("Processing was cancelled.");
  if ((error instanceof DOMException && error.name === "TimeoutError") || (error as { name?: string | undefined })?.name === "TimeoutError") {
    return new Error(`The audio service did not answer within ${Math.round(timeoutMs / 1000)} s.`);
  }
  if (error instanceof TypeError) {
    return new Error("Could not reach the audio service. Check the address, that it is running, and that it allows this site (CORS).");
  }
  return error instanceof Error ? error : new Error("The audio service request failed.");
}

const num = z.coerce.number().finite().optional().catch(undefined);
const str = z.string().optional().catch(undefined);

const candidateSchema = z.union([
  z.string().transform((transcript) => ({ transcript })),
  z
    .object({
      transcript: str, text: str, audio_url: str, peaks: z.array(z.number()).optional().catch(undefined),
      guard_score: num, confidence: num, snr_db: num,
    })
    .passthrough()
    .transform((value) => ({
      transcript: value.transcript ?? value.text,
      audioUrl: value.audio_url,
      peaks: value.peaks,
      guardScore: value.guard_score,
      confidence: value.confidence,
      snrDb: value.snr_db,
    })),
]);

const resultSchema = z
  .object({
    request_id: str,
    selected_candidate: str,
    transcription: str,
    candidates: z.record(z.string(), candidateSchema).optional().catch(undefined),
    analysis: z
      .object({
        snr_db: num, noise_floor_db: num, speech_energy_db: num, guard_score: num, guard_status: str,
        vad_segments: z.array(z.tuple([z.number(), z.number()])).optional().catch(undefined),
        spectrum: z.array(z.number()).optional().catch(undefined),
      })
      .passthrough()
      .optional()
      .catch(undefined),
    evaluation: z
      .object({ wer: num, cer: num, latency_ms: num, calls: num, note: str })
      .passthrough()
      .optional()
      .catch(undefined),
    pipeline: z.array(z.string()).optional().catch(undefined),
    latency_ms: num,
  })
  .passthrough();

export function asTrackId(value: unknown): TrackId | undefined {
  const upper = typeof value === "string" ? value.trim().toUpperCase() : "";
  return (TRACK_IDS as readonly string[]).includes(upper) ? (upper as TrackId) : undefined;
}

export function resolveServiceUrl(baseUrl: string, value?: string): string | undefined {
  if (!value) return undefined;
  if (/^(data:|blob:)/.test(value)) return value;
  try {
    return new URL(value, `${baseUrl}/`).toString();
  } catch {
    return undefined;
  }
}

/** Normalizes a POST /process JSON body. Throws when the body is not a usable result. */
export function normalizeProcessingResult(raw: unknown, baseUrl = ""): ProcessingResult {
  const parsed = resultSchema.safeParse(raw);
  if (!parsed.success) throw new Error("The audio service returned a response this workstation cannot read.");
  const body = parsed.data;
  const candidates: ProcessingResult["candidates"] = {};
  for (const [key, value] of Object.entries(body.candidates ?? {})) {
    const id = asTrackId(key);
    if (!id) continue;
    candidates[id] = { ...value, audioUrl: resolveServiceUrl(baseUrl, "audioUrl" in value ? value.audioUrl : undefined) };
  }
  const selectedCandidate = asTrackId(body.selected_candidate);
  if (!selectedCandidate && !body.transcription && Object.keys(candidates).length === 0) {
    throw new Error("The audio service response had no transcript or candidates.");
  }
  return {
    requestId: body.request_id,
    selectedCandidate,
    transcription: body.transcription ?? (selectedCandidate ? candidates[selectedCandidate]?.transcript : undefined),
    candidates,
    analysis: {
      snrDb: body.analysis?.snr_db,
      noiseFloorDb: body.analysis?.noise_floor_db,
      speechEnergyDb: body.analysis?.speech_energy_db,
      guardScore: body.analysis?.guard_score,
      guardStatus: body.analysis?.guard_status,
      vadSegments: body.analysis?.vad_segments ?? [],
      spectrum: body.analysis?.spectrum,
    },
    evaluation: body.evaluation
      ? { wer: body.evaluation.wer, cer: body.evaluation.cer, latencyMs: body.evaluation.latency_ms, calls: body.evaluation.calls, note: body.evaluation.note }
      : undefined,
    pipeline: body.pipeline ?? [],
    latencyMs: body.latency_ms,
  };
}

/** Builds the multipart body for POST /process. */
export function buildProcessForm(file: File | Blob, fileName: string, options: Omit<ProcessOptions, "signal" | "timeoutMs">): FormData {
  const candidates = options.candidates.length ? options.candidates : ["C0"];
  const form = new FormData();
  form.append("file", file, fileName);
  form.append("mode", options.mode);
  form.append("sample_rate", "8000");
  form.append("channels", "1");
  form.append("sample_format", "pcm_s16le");
  form.append("asr_provider", "gnani_prisma");
  form.append("asr_model", options.asrModel);
  form.append("candidates", candidates.join(","));
  form.append("selection", options.mode === "fast" ? "snr_router" : "consensus");
  form.append("compare_models", options.mode === "max" && options.compareModels ? "true" : "false");
  return form;
}

export async function checkApiConnection(baseUrl: string): Promise<HealthInfo> {
  if (!baseUrl) throw new Error("Add your FastAPI or Flask proxy address first.");
  const started = performance.now();
  const timeout = withTimeout(undefined, 8000);
  try {
    const response = await fetch(`${baseUrl}/health`, { headers: { Accept: "application/json" }, signal: timeout.signal });
    if (!response.ok) throw new Error(await readError(response));
    const body = (await response.json().catch(() => ({}))) as { status?: unknown | undefined; gnani_configured?: unknown | undefined; version?: unknown };
    return {
      status: typeof body.status === "string" ? body.status : "ok",
      gnaniConfigured: typeof body.gnani_configured === "boolean" ? body.gnani_configured : undefined,
      version: typeof body.version === "string" ? body.version : undefined,
      latencyMs: Math.round(performance.now() - started),
    };
  } catch (error) {
    throw describeFetchError(error, 8000);
  } finally {
    timeout.done();
  }
}

export async function processAudioFile(baseUrl: string, file: File, options: ProcessOptions): Promise<ProcessingResult> {
  const timeoutMs = options.timeoutMs ?? 180_000;
  const timeout = withTimeout(options.signal, timeoutMs);
  const started = performance.now();
  try {
    const response = await fetch(`${baseUrl}/process`, {
      method: "POST",
      body: buildProcessForm(file, file.name, options),
      headers: { Accept: "application/json" },
      signal: timeout.signal,
    });
    if (!response.ok) throw new Error(await readError(response));
    const result = normalizeProcessingResult(await response.json(), baseUrl);
    return { ...result, latencyMs: result.latencyMs ?? Math.round(performance.now() - started) };
  } catch (error) {
    throw describeFetchError(error, timeoutMs);
  } finally {
    timeout.done();
  }
}

const rowSchema = z
  .object({
    id: z.union([z.string(), z.number()]).transform(String),
    pipeline: z.string(),
    wer: num, cer: num, latency_ms: num, candidate: str,
  })
  .transform((row) => ({ id: row.id, pipeline: row.pipeline, wer: row.wer, cer: row.cer, latencyMs: row.latency_ms, candidate: row.candidate }));

const reportSchema = z.object({
  dataset: str,
  clip_count: num,
  measured_at: str,
  sections: z.object({
    comparison: z.array(rowSchema).optional(),
    ablation: z.array(rowSchema).optional(),
    snr: z.array(rowSchema).optional(),
    noise: z.array(rowSchema).optional(),
  }),
});

/** Parses an evaluation report (GET /evaluation or an imported JSON file). WER/CER are fractions 0–1. */
export function parseEvaluationReport(raw: unknown): EvaluationReport {
  const parsed = reportSchema.safeParse(raw);
  if (!parsed.success) throw new Error("This evaluation file does not match the expected format (sections → rows with id, pipeline, wer, cer).");
  return { dataset: parsed.data.dataset, clipCount: parsed.data.clip_count, measuredAt: parsed.data.measured_at, sections: parsed.data.sections };
}

export async function fetchEvaluation(baseUrl: string): Promise<EvaluationReport> {
  const timeout = withTimeout(undefined, 15_000);
  try {
    const response = await fetch(`${baseUrl}/evaluation`, { headers: { Accept: "application/json" }, signal: timeout.signal });
    if (!response.ok) throw new Error(await readError(response));
    return parseEvaluationReport(await response.json());
  } catch (error) {
    throw describeFetchError(error, 15_000);
  } finally {
    timeout.done();
  }
}

export function makeLiveSocketUrl(baseUrl: string): string {
  const url = new URL(`${baseUrl}/live`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("sample_rate", "8000");
  url.searchParams.set("vad_preset", "telephony");
  return url.toString();
}

export type LiveMessage =
  | { type: "transcript"; text: string; final: boolean; candidate?: TrackId | undefined }
  | { type: "metrics"; snrDb?: number | undefined; latencyMs?: number | undefined; candidate?: TrackId | undefined }
  | { type: "error"; message: string };

/** Parses one JSON text message from WS /live. Unknown messages return null. */
export function parseLiveMessage(data: unknown): LiveMessage | null {
  if (typeof data !== "string") return null;
  let body: Record<string, unknown>;
  try {
    body = JSON.parse(data) as Record<string, unknown>;
  } catch {
    return null;
  }
  const candidate = asTrackId(body.candidate);
  if ((body.type === "partial" || body.type === "final" || body.type === "transcript") && typeof body.text === "string") {
    return { type: "transcript", text: body.text, final: body.type === "final" || body.final === true, candidate };
  }
  if (body.type === "metrics") {
    const n = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : undefined);
    return { type: "metrics", snrDb: n(body.snr_db), latencyMs: n(body.latency_ms), candidate };
  }
  if (body.type === "error") return { type: "error", message: typeof body.message === "string" ? body.message : "The live proxy reported an error." };
  return null;
}
