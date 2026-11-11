import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildProcessForm, checkApiConnection, makeLiveSocketUrl, normalizeBaseUrl, normalizeProcessingResult, parseEvaluationReport, parseLiveMessage,
} from "@/lib/bharatnoise-api";

afterEach(() => vi.unstubAllGlobals());

describe("proxy address", () => {
  it("accepts HTTPS and strips trailing slashes", () => {
    expect(normalizeBaseUrl("https://api.example.com/v1/")).toBe("https://api.example.com/v1");
  });
  it("allows plain HTTP only for localhost", () => {
    expect(normalizeBaseUrl("http://localhost:8000")).toBe("http://localhost:8000");
    expect(() => normalizeBaseUrl("http://api.example.com")).toThrow(/HTTPS/);
  });
  it("treats an empty address as demo mode", () => {
    expect(normalizeBaseUrl("  ")).toBe("");
  });
});

describe("POST /process request", () => {
  it("sends 8 kHz mono PCM16 to Gnani Prisma with one SNR-routed call in Fast mode", () => {
    const form = buildProcessForm(new Blob(["x"]), "a.wav", { mode: "fast", candidates: ["C0", "C1", "C2"], asrModel: "prisma-v2.5", compareModels: true });
    expect(form.get("sample_rate")).toBe("8000");
    expect(form.get("channels")).toBe("1");
    expect(form.get("sample_format")).toBe("pcm_s16le");
    expect(form.get("asr_provider")).toBe("gnani_prisma");
    expect(form.get("selection")).toBe("snr_router");
    expect(form.get("compare_models")).toBe("false");
    expect(form.get("candidates")).toBe("C0,C1,C2");
  });
  it("uses consensus and optional model comparison in Max mode", () => {
    const form = buildProcessForm(new Blob(["x"]), "a.wav", { mode: "max", candidates: ["C0", "C1", "C2", "C3"], asrModel: "prisma-v3.0", compareModels: true });
    expect(form.get("selection")).toBe("consensus");
    expect(form.get("compare_models")).toBe("true");
    expect(form.get("asr_model")).toBe("prisma-v3.0");
  });
});

describe("GET /health response", () => {
  it("validates the documented fields and measures request latency locally", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: "ok", gnani_configured: false, gnani_mode: "unconfigured", version: "0.3.1",
    }), { headers: { "Content-Type": "application/json" } })));
    await expect(checkApiConnection("http://localhost:8000")).resolves.toMatchObject({
      status: "ok", gnaniConfigured: false, gnaniMode: "unconfigured", version: "0.3.1",
    });
  });
  it("surfaces a malformed health response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok" }), {
      headers: { "Content-Type": "application/json" },
    })));
    await expect(checkApiConnection("http://localhost:8000")).rejects.toThrow(/GET \/health response does not match/);
  });
});

describe("POST /process response", () => {
  it("validates the backend response and resolves candidate audio URLs", () => {
    const result = normalizeProcessingResult({
      request_id: "mock-request",
      gnani_mode: "mock",
      score_suppressed_reason: "mock",
      selected_candidate: "C2",
      transcription: "[MOCK] enhanced fixture",
      candidates: {
        C0: { transcript: "[MOCK] original fixture", audio_url: "/api/clips/mock/C0", peaks: [], guard_score: null, confidence: null, snr_db: null },
        C2: { transcript: "[MOCK] enhanced fixture", audio_url: "/api/clips/mock/C2", peaks: [], guard_score: null, confidence: null, snr_db: null },
      },
      analysis: { snr_db: null, noise_floor_db: null, speech_energy_db: null, vad_segments: [], guard_status: null, guard_result: null, spectrum: [] },
      evaluation: { wer: null, cer: null, latency_ms: null, calls: null, note: "Mock scores are suppressed." },
      pipeline: ["mock"],
      latency_ms: 12,
      router_fitted: false,
      suppression_fitted: false,
    }, "https://api.example.com");
    expect(result.selectedCandidate).toBe("C2");
    expect(result.candidates.C0?.transcript).toBe("[MOCK] original fixture");
    expect(result.candidates.C2?.audioUrl).toBe("https://api.example.com/api/clips/mock/C2");
    expect(result.transcription).toBe("[MOCK] enhanced fixture");
  });
  it("rejects missing and malformed response fields with a readable error", () => {
    expect(() => normalizeProcessingResult({ status: "ok" })).toThrow(/POST \/process response does not match/);
    expect(() => normalizeProcessingResult({
      request_id: "id",
      gnani_mode: "mock",
      score_suppressed_reason: "mock",
      selected_candidate: "C9",
      transcription: "",
      candidates: {},
      analysis: {},
      evaluation: {},
      pipeline: [],
      latency_ms: 1,
      router_fitted: false,
      suppression_fitted: null,
    })).toThrow(/selected_candidate/);
  });
});

describe("live socket", () => {
  it("targets wss /live with 8 kHz and the telephony VAD preset", () => {
    const url = new URL(makeLiveSocketUrl("https://api.example.com"));
    expect(url.protocol).toBe("wss:");
    expect(url.pathname).toBe("/live");
    expect(url.searchParams.get("sample_rate")).toBe("8000");
    expect(url.searchParams.get("vad_preset")).toBe("telephony");
  });
  it("parses partial, final and metrics messages", () => {
    expect(parseLiveMessage('{"type":"partial","text":"haan"}')).toEqual({ type: "transcript", text: "haan", final: false, candidate: undefined });
    expect(parseLiveMessage('{"type":"final","text":"haan ji","candidate":"C1"}')).toEqual({ type: "transcript", text: "haan ji", final: true, candidate: "C1" });
    expect(parseLiveMessage('{"type":"metrics","latency_ms":182,"snr_db":7.5}')).toMatchObject({ latencyMs: 182, snrDb: 7.5 });
    expect(parseLiveMessage('{"type":"connected","config":{"sample_rate":8000}}')).toEqual({
      type: "connected", config: { sample_rate: 8000 },
    });
    expect(parseLiveMessage('{"type":"transcript","text":"[MOCK] segment fixture","segment_index":1,"latency":12}')).toMatchObject({
      type: "transcript", text: "[MOCK] segment fixture", final: true, segmentIndex: 1, latencyMs: 12,
    });
    expect(() => parseLiveMessage("not json")).toThrow(/invalid JSON/);
  });
});

describe("evaluation report", () => {
  it("reads sections with fractional WER/CER", () => {
    const report = parseEvaluationReport({ dataset: "dev", clip_count: 120, sections: { comparison: [{ id: "A", pipeline: "C0", wer: 0.2, cer: 0.1, latency_ms: 400, candidate: "C0" }] } });
    expect(report.available).toBe(true);
    expect(report.clipCount).toBe(120);
    expect(report.sections.comparison?.[0]).toMatchObject({ id: "A", wer: 0.2, latencyMs: 400 });
  });
  it("rejects files without sections", () => {
    expect(() => parseEvaluationReport({ rows: [] })).toThrow();
  });
  it("accepts the explicit no-results response", () => {
    const report = parseEvaluationReport({
      available: false,
      reason: "No benchmark results have been generated yet.",
      dataset: null,
      clip_count: null,
      measured_at: null,
      sections: { comparison: [], ablation: [], snr: [], noise: [] },
    });
    expect(report.available).toBe(false);
    expect(report.sections.comparison).toEqual([]);
  });
});
