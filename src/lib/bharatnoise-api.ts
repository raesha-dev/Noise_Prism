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
  transcript?: string | null | undefined;
  audioUrl?: string | null | undefined;
  peaks?: number[] | null | undefined;
  guardScore?: number | null | undefined;
  confidence?: number | null | undefined;
  snrDb?: number | null | undefined;
};

export type ProcessingResult = {
  requestId?: string | undefined;
  gnaniMode: "unconfigured" | "live" | "mock";
  scoreSuppressedReason?: string | null | undefined;
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

export type GnaniMode = "unconfigured" | "live" | "mock";
export type HealthInfo = { status: string; gnaniConfigured: boolean; gnaniMode: GnaniMode; version: string; latencyMs: number };

export type EvaluationRow = { id: string; pipeline: string; wer?: number | null | undefined; cer?: number | null | undefined; latencyMs?: number | null | undefined; candidate?: string | null | undefined };
export type EvaluationSection = "comparison" | "ablation" | "snr" | "noise";
export type EvaluationReport = {
  available: boolean;
  reason?: string | null | undefined;
  dataset?: string | null | undefined;
  clipCount?: number | null | undefined;
  measuredAt?: string | null | undefined;
  sections: Partial<Record<EvaluationSection, EvaluationRow[] | undefined>>;
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
  const payload = (await response.json().catch(() => null)) as {
    detail?: unknown | undefined;
    message?: unknown | undefined;
    error?: unknown;
  } | null;
  for (const value of [payload?.detail, payload?.message, payload?.error]) {
    if (typeof value === "string") return value;
  }
  const error = payload?.error;
  if (typeof error === "object" && error !== null && "message" in error && typeof error.message === "string") {
    return error.message;
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

const num = z.number().finite();
const nullableNum = num.nullable();
const nullableStr = z.string().nullable();

const candidateSchema = z.object({
  transcript: nullableStr,
  audio_url: nullableStr,
  peaks: z.array(num),
  guard_score: nullableNum,
  confidence: nullableNum,
  snr_db: nullableNum,
}).strict().transform((value) => ({
  transcript: value.transcript,
  audioUrl: value.audio_url,
  peaks: value.peaks,
  guardScore: value.guard_score,
  confidence: value.confidence,
  snrDb: value.snr_db,
}));

const analysisSchema = z.object({
  snr_db: nullableNum,
  noise_floor_db: nullableNum,
  speech_energy_db: nullableNum,
  vad_segments: z.array(z.tuple([num, num])),
  guard_status: nullableStr,
  guard_result: z.record(z.string(), z.unknown()).nullable(),
  spectrum: z.array(num),
}).strict();

const serviceEvaluationSchema = z.object({
  wer: nullableNum,
  cer: nullableNum,
  latency_ms: nullableNum,
  calls: z.number().int().nullable(),
  note: nullableStr,
}).strict();

const resultSchema = z.object({
  request_id: z.string().min(1),
  gnani_mode: z.enum(["unconfigured", "live", "mock"]),
  score_suppressed_reason: z.string().nullable(),
  selected_candidate: z.enum(TRACK_IDS),
  transcription: z.string(),
  candidates: z.record(z.enum(TRACK_IDS), candidateSchema),
  analysis: analysisSchema,
  evaluation: serviceEvaluationSchema,
  pipeline: z.array(z.string()),
  latency_ms: num,
  router_fitted: z.boolean(),
  suppression_fitted: z.boolean().nullable(),
}).strict();

function responseShapeError(label: string, issues: { path: PropertyKey[] }[]): Error {
  const paths = issues.map((issue) => issue.path.map(String).join(".") || "(root)");
  return new Error(`${label} response does not match the workstation contract${paths.length ? `: ${paths.join(", ")}` : "."}`);
}

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
  if (!parsed.success) throw responseShapeError("POST /process", parsed.error.issues);
  const body = parsed.data;
  const candidates: ProcessingResult["candidates"] = {};
  for (const [key, value] of Object.entries(body.candidates)) {
    const id = asTrackId(key);
    if (!id) continue;
    candidates[id] = { ...value, audioUrl: resolveServiceUrl(baseUrl, value.audioUrl ?? undefined) };
  }
  const selectedCandidate = asTrackId(body.selected_candidate);
  if (!selectedCandidate || (!body.transcription && Object.keys(candidates).length === 0)) {
    throw new Error("The audio service response had no transcript or candidates.");
  }
  return {
    requestId: body.request_id,
    gnaniMode: body.gnani_mode,
    scoreSuppressedReason: body.score_suppressed_reason ?? undefined,
    selectedCandidate,
    transcription: body.transcription ?? (selectedCandidate ? candidates[selectedCandidate]?.transcript : undefined),
    candidates,
    analysis: {
      snrDb: body.analysis.snr_db ?? undefined,
      noiseFloorDb: body.analysis.noise_floor_db ?? undefined,
      speechEnergyDb: body.analysis.speech_energy_db ?? undefined,
      guardScore: undefined,
      guardStatus: body.analysis.guard_status ?? undefined,
      vadSegments: body.analysis.vad_segments,
      spectrum: body.analysis.spectrum,
    },
    evaluation: {
      wer: body.evaluation.wer ?? undefined,
      cer: body.evaluation.cer ?? undefined,
      latencyMs: body.evaluation.latency_ms ?? undefined,
      calls: body.evaluation.calls ?? undefined,
      note: body.evaluation.note ?? undefined,
    },
    pipeline: body.pipeline,
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
    const parsed = z.object({
      status: z.string(),
      gnani_configured: z.boolean(),
        gnani_mode: z.enum(["unconfigured", "live", "mock"]),
        version: z.string(),
    }).strict().safeParse(await response.json());
    if (!parsed.success) throw responseShapeError("GET /health", parsed.error.issues);
    return {
      status: parsed.data.status,
      gnaniConfigured: parsed.data.gnani_configured,
      gnaniMode: parsed.data.gnani_mode,
      version: parsed.data.version,
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

const rowSchema = z.object({
  id: z.union([z.string(), z.number()]).transform(String),
  pipeline: z.string(),
  wer: nullableNum.optional(),
  cer: nullableNum.optional(),
  latency_ms: nullableNum.optional(),
  candidate: z.string().nullable().optional(),
}).strict().transform((row) => ({
  id: row.id,
  pipeline: row.pipeline,
  wer: row.wer,
  cer: row.cer,
  latencyMs: row.latency_ms,
  candidate: row.candidate,
}));

const reportSectionsSchema = z.object({
  comparison: z.array(rowSchema).optional(),
  ablation: z.array(rowSchema).optional(),
  snr: z.array(rowSchema).optional(),
  noise: z.array(rowSchema).optional(),
}).strict();

const reportSchema = z.object({
  available: z.boolean().optional(),
  reason: z.string().nullable().optional(),
  dataset: z.string().nullable().optional(),
  clip_count: z.number().int().nullable().optional(),
  measured_at: z.string().nullable().optional(),
  sections: reportSectionsSchema,
}).strict();

const evaluationResponseSchema = z.object({
  available: z.boolean(),
  reason: z.string().nullable(),
  dataset: z.string().nullable(),
  clip_count: z.number().int().nullable(),
  measured_at: z.string().nullable(),
  sections: z.object({
    comparison: z.array(rowSchema),
    ablation: z.array(rowSchema),
    snr: z.array(rowSchema),
    noise: z.array(rowSchema),
  }).strict(),
}).strict();

/** Parses an evaluation report (GET /evaluation or an imported JSON file). WER/CER are fractions 0–1. */
export function parseEvaluationReport(raw: unknown): EvaluationReport {
  const parsed = reportSchema.safeParse(raw);
  if (!parsed.success) throw responseShapeError("Evaluation", parsed.error.issues);
  return {
    available: parsed.data.available ?? true,
    reason: parsed.data.reason,
    dataset: parsed.data.dataset,
    clipCount: parsed.data.clip_count,
    measuredAt: parsed.data.measured_at,
    sections: parsed.data.sections,
  };
}

export async function fetchEvaluation(baseUrl: string): Promise<EvaluationReport> {
  const timeout = withTimeout(undefined, 15_000);
  try {
    const response = await fetch(`${baseUrl}/evaluation`, { headers: { Accept: "application/json" }, signal: timeout.signal });
    if (!response.ok) throw new Error(await readError(response));
    const raw = await response.json();
    const parsed = evaluationResponseSchema.safeParse(raw);
    if (!parsed.success) throw responseShapeError("GET /evaluation", parsed.error.issues);
    return {
      available: parsed.data.available,
      reason: parsed.data.reason,
      dataset: parsed.data.dataset,
      clipCount: parsed.data.clip_count,
      measuredAt: parsed.data.measured_at,
      sections: parsed.data.sections,
    };
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
  | { type: "connected"; config?: Record<string, unknown> | undefined }
  | { type: "speech_start" }
  | { type: "processing" }
  | { type: "transcript"; text: string; final: boolean; audioDurationMs?: number | undefined; segmentIndex?: number | undefined; latencyMs?: number | undefined; detectedLanguageCode?: string | undefined; candidate?: TrackId | undefined }
  | { type: "metrics"; snrDb?: number | undefined; latencyMs?: number | undefined; candidate?: TrackId | undefined }
  | { type: "error"; message: string };

/** Validates one JSON text message relayed by the backend's Gnani WebSocket proxy. */
export function parseLiveMessage(data: unknown): LiveMessage {
  if (typeof data !== "string") throw new Error("The live proxy sent a non-text response.");
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch (error) {
    throw new Error("The live proxy sent invalid JSON.", { cause: error });
  }
  const envelope = z.object({ type: z.string() }).passthrough().safeParse(raw);
  if (!envelope.success) throw responseShapeError("WS /live", envelope.error.issues);
  const body = envelope.data;
  if (body.type === "connected") {
    const parsed = z.object({ type: z.literal("connected"), config: z.record(z.string(), z.unknown()).optional() }).passthrough().safeParse(body);
    if (!parsed.success) throw responseShapeError("WS /live connected", parsed.error.issues);
    return { type: "connected", config: parsed.data.config };
  }
  if (body.type === "speech_start" || body.type === "processing") return { type: body.type };
  if (body.type === "transcript" || body.type === "partial" || body.type === "final") {
    const parsed = z.object({
      type: z.enum(["transcript", "partial", "final"]),
      text: z.string(),
      audio_duration_ms: num.optional(),
      segment_index: z.number().int().optional(),
      latency: num.optional(),
      detected_language_code: z.string().optional(),
      final: z.boolean().optional(),
      candidate: z.string().optional(),
    }).passthrough().safeParse(body);
    if (!parsed.success) throw responseShapeError("WS /live transcript", parsed.error.issues);
    return {
      type: "transcript",
      text: parsed.data.text,
      final: parsed.data.type === "final" || parsed.data.final === true || parsed.data.type === "transcript",
      audioDurationMs: parsed.data.audio_duration_ms,
      segmentIndex: parsed.data.segment_index,
      latencyMs: parsed.data.latency,
      detectedLanguageCode: parsed.data.detected_language_code,
      candidate: asTrackId(parsed.data.candidate),
    };
  }
  if (body.type === "metrics") {
    const parsed = z.object({
      type: z.literal("metrics"),
      snr_db: num.optional(),
      latency_ms: num.optional(),
      candidate: z.string().optional(),
    }).passthrough().safeParse(body);
    if (!parsed.success) throw responseShapeError("WS /live metrics", parsed.error.issues);
    return { type: "metrics", snrDb: parsed.data.snr_db, latencyMs: parsed.data.latency_ms, candidate: asTrackId(parsed.data.candidate) };
  }
  if (body.type === "error") {
    const parsed = z.object({
      type: z.literal("error"),
      message: z.string().optional(),
      error: z.string().optional(),
    }).passthrough().safeParse(body);
    if (!parsed.success) throw responseShapeError("WS /live error", parsed.error.issues);
    return { type: "error", message: parsed.data.message ?? parsed.data.error ?? "The live proxy reported an error." };
  }
  throw new Error(`The live proxy sent unsupported message type "${body.type}".`);
}
