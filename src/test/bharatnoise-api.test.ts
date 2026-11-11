import { describe, expect, it } from "vitest";
import {
  buildProcessForm, makeLiveSocketUrl, normalizeBaseUrl, normalizeProcessingResult, parseEvaluationReport, parseLiveMessage,
} from "@/lib/bharatnoise-api";

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

describe("POST /process response", () => {
  it("normalizes string and object candidates and resolves relative audio URLs", () => {
    const result = normalizeProcessingResult({
      selected_candidate: "c2",
      candidates: { C0: "original text", C2: { transcript: "enhanced", audio_url: "/audio/c2.wav", guard_score: 0.91 }, X9: "ignored" },
      analysis: { snr_db: 6.4, vad_segments: [[0.2, 1.4]] },
    }, "https://api.example.com");
    expect(result.selectedCandidate).toBe("C2");
    expect(result.candidates.C0?.transcript).toBe("original text");
    expect(result.candidates.C2?.audioUrl).toBe("https://api.example.com/audio/c2.wav");
    expect(result.candidates.C2?.guardScore).toBe(0.91);
    expect(result.transcription).toBe("enhanced");
    expect(result.analysis.snrDb).toBe(6.4);
    expect(Object.keys(result.candidates)).not.toContain("X9");
  });
  it("rejects a response without transcripts or candidates", () => {
    expect(() => normalizeProcessingResult({ status: "ok" })).toThrow();
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
    expect(parseLiveMessage("not json")).toBeNull();
  });
});

describe("evaluation report", () => {
  it("reads sections with fractional WER/CER", () => {
    const report = parseEvaluationReport({ dataset: "dev", clip_count: 120, sections: { comparison: [{ id: "A", pipeline: "C0", wer: 0.2, cer: 0.1, latency_ms: 400, candidate: "C0" }] } });
    expect(report.clipCount).toBe(120);
    expect(report.sections.comparison?.[0]).toMatchObject({ id: "A", wer: 0.2, latencyMs: 400 });
  });
  it("rejects files without sections", () => {
    expect(() => parseEvaluationReport({ rows: [] })).toThrow();
  });
});
