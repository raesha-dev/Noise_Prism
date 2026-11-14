import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildProcessForm,
  checkApiConnection,
  DEFAULT_API_BASE_URL,
  fetchCandidateConfig,
  getApiBaseUrl,
  makeLiveSocketUrl,
  normalizeBaseUrl,
  normalizeProcessingResult,
  parseEvaluationReport,
  parseLiveMessage,
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
  it("uses the local backend when no address is configured", () => {
    expect(normalizeBaseUrl("  ")).toBe(DEFAULT_API_BASE_URL);
    window.localStorage.removeItem("bharatnoise.api-base-url");
    expect(getApiBaseUrl()).toBe(DEFAULT_API_BASE_URL);
  });
});

describe("POST /process request", () => {
  it("sends 8 kHz mono PCM16 for one selected provider call in Fast mode", () => {
    const form = buildProcessForm(new Blob(["x"]), "a.wav", {
      mode: "fast",
      candidates: ["C0", "C1", "C2"],
      asrModel: "prisma-v2.5",
      outputFormat: "auto",
      languageCode: "hi-IN",
      compareModels: true,
    });
    expect(form.get("sample_rate")).toBe("8000");
    expect(form.get("channels")).toBe("1");
    expect(form.get("sample_format")).toBe("pcm_s16le");
    expect(form.get("asr_provider")).toBe("gnani_prisma");
    expect(form.get("selection")).toBe("snr_router");
    expect(form.get("compare_models")).toBe("false");
    expect(form.get("candidates")).toBe("C0,C1,C2");
    expect(form.get("language_code")).toBe("hi-IN");
    expect(form.has("format")).toBe(false);
  });
  it("uses consensus and optional model comparison in Max mode", () => {
    const form = buildProcessForm(new Blob(["x"]), "a.wav", {
      mode: "max",
      candidates: ["C0", "C1", "C2", "C3"],
      asrModel: "prisma-v3.0",
      outputFormat: "codemix",
      languageCode: "indic-auto",
      compareModels: true,
    });
    expect(form.get("selection")).toBe("consensus");
    expect(form.get("compare_models")).toBe("true");
    expect(form.get("asr_model")).toBe("prisma-v3.0");
    expect(form.get("format")).toBe("codemix");
    expect(form.get("language_code")).toBe("indic-auto");
  });
  it("sends the selected Observation Addition coefficient", () => {
    const form = buildProcessForm(new Blob(["x"]), "a.wav", {
      mode: "max",
      candidates: ["C0", "C2B", "C2C"],
      asrModel: "prisma-v3.0",
      outputFormat: "codemix",
      languageCode: "indic-auto",
      compareModels: false,
      oaLambda: 0.3,
    });
    expect(form.get("oa_lambda")).toBe("0.3");
    expect(form.get("candidates")).toBe("C0,C2B,C2C");
  });
});

describe("GET /api/config response", () => {
  it("uses the backend-owned OA sweep and fitted flag", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            imcra_omlsa: { fitted: false, oa_lambda: 0.1, oa_sweep: [0, 0.1, 0.3] },
          }),
          { headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    await expect(fetchCandidateConfig("http://localhost:8000")).resolves.toEqual({
      fitted: false,
      oaLambda: 0.1,
      oaSweep: [0, 0.1, 0.3],
    });
  });
  it("rejects malformed candidate configuration", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ imcra_omlsa: { fitted: false } }), {
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(fetchCandidateConfig("http://localhost:8000")).rejects.toThrow(
      /GET \/api\/config response does not match/,
    );
  });
});

describe("GET /health response", () => {
  it("validates the documented fields without inventing health metrics", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: "ok",
            gnani_configured: false,
            gnani_mode: "unconfigured",
            version: "0.3.1",
          }),
          { headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    await expect(checkApiConnection("http://localhost:8000")).resolves.toMatchObject({
      status: "ok",
      gnaniConfigured: false,
      gnaniMode: "unconfigured",
      version: "0.3.1",
    });
  });
  it("surfaces a malformed health response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: "ok" }), {
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(checkApiConnection("http://localhost:8000")).rejects.toThrow(
      /GET \/health response does not match/,
    );
  });
});

describe("POST /process response", () => {
  it("validates the backend response and resolves candidate audio URLs", () => {
    const result = normalizeProcessingResult(
      {
        request_id: "mock-request",
        gnani_mode: "mock",
        output_format: "codemix",
        language_code: "indic-auto",
        detected_language_code: "hi-IN",
        unavailable_candidates: { C3: "DeepFilterNet dependency is unavailable." },
        score_suppressed_reason: "mock",
        selected_candidate: "C2",
        transcription: "[MOCK] enhanced fixture",
        candidates: {
          C0: {
            transcript: "[MOCK] original fixture",
            audio_url: "/api/clips/mock/C0",
            peaks: [],
            guard_score: null,
            confidence: null,
            snr_db: null,
            error: null,
            truncated: false,
            parameters: null,
          },
          C1: {
            transcript: "",
            audio_url: "/api/clips/mock/C1",
            peaks: [],
            guard_score: null,
            confidence: null,
            snr_db: null,
            error: "HTTP 400 BAD_REQUEST",
            truncated: false,
            parameters: null,
          },
          C2: {
            transcript: "[MOCK] enhanced fixture",
            audio_url: "/api/clips/mock/C2",
            peaks: [],
            guard_score: null,
            confidence: null,
            snr_db: null,
            error: null,
            truncated: true,
            parameters: { observation_addition_lambda: 0.3 },
          },
        },
        analysis: {
          stationary_snr_db_est: null,
          noise_floor_db: null,
          speech_energy_db: null,
          vad_segments: [],
          guard_status: null,
          guard_result: null,
          spectrum: [],
        },
        evaluation: {
          wer: null,
          cer: null,
          latency_ms: null,
          calls: null,
          note: "Mock scores are suppressed.",
        },
        pipeline: ["mock"],
        latency_ms: 12,
        router_fitted: false,
        suppression_fitted: false,
      },
      "https://api.example.com",
    );
    expect(result.selectedCandidate).toBe("C2");
    expect(result.analysis.snrDb).toBeUndefined();
    expect(result.outputFormat).toBe("codemix");
    expect(result.languageCode).toBe("indic-auto");
    expect(result.detectedLanguageCode).toBe("hi-IN");
    expect(result.candidates.C0?.transcript).toBe("[MOCK] original fixture");
    expect(result.candidates.C2?.audioUrl).toBe("https://api.example.com/api/clips/mock/C2");
    expect(result.candidates.C1?.transcript).toBe("");
    expect(result.candidates.C1?.error).toBe("HTTP 400 BAD_REQUEST");
    expect(result.candidates.C2?.truncated).toBe(true);
    expect(result.candidates.C2?.parameters?.["observation_addition_lambda"]).toBe(0.3);
    expect(result.transcription).toBe("[MOCK] enhanced fixture");
    expect(result.unavailableCandidates?.C3).toBe("DeepFilterNet dependency is unavailable.");
  });

  it("normalizes the legacy analysis.snr_db field", () => {
    const raw = {
      request_id: "legacy-request",
      gnani_mode: "mock",
      output_format: "verbatim",
      language_code: "hi-IN",
      detected_language_code: null,
      unavailable_candidates: {},
      score_suppressed_reason: null,
      selected_candidate: "C0",
      transcription: "[MOCK] legacy fixture",
      candidates: {
        C0: {
          transcript: "[MOCK] legacy fixture",
          audio_url: null,
          peaks: [],
          guard_score: null,
          confidence: null,
          snr_db: null,
          error: null,
          truncated: false,
          parameters: null,
        },
      },
      analysis: {
        snr_db: 8.5,
        noise_floor_db: null,
        speech_energy_db: null,
        vad_segments: [],
        guard_status: null,
        guard_result: null,
        spectrum: [],
      },
      evaluation: {
        wer: null,
        cer: null,
        latency_ms: null,
        calls: null,
        note: "Mock scores are suppressed.",
      },
      pipeline: ["mock"],
      latency_ms: 12,
      router_fitted: false,
      suppression_fitted: false,
    };

    expect(normalizeProcessingResult(raw).analysis.snrDb).toBe(8.5);
  });
  it("rejects missing and malformed response fields with a readable error", () => {
    expect(() => normalizeProcessingResult({ status: "ok" })).toThrow(
      /POST \/process response does not match/,
    );
    expect(() =>
      normalizeProcessingResult({
        request_id: "id",
        gnani_mode: "mock",
        output_format: "verbatim",
        language_code: "hi-IN",
        detected_language_code: null,
        unavailable_candidates: {},
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
      }),
    ).toThrow(/selected_candidate/);
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
    expect(parseLiveMessage('{"type":"partial","text":"haan"}')).toEqual({
      type: "transcript",
      text: "haan",
      final: false,
      candidate: undefined,
    });
    expect(parseLiveMessage('{"type":"final","text":"haan ji","candidate":"C1"}')).toEqual({
      type: "transcript",
      text: "haan ji",
      final: true,
      candidate: "C1",
    });
    expect(parseLiveMessage('{"type":"metrics","latency_ms":182,"snr_db":7.5}')).toMatchObject({
      latencyMs: 182,
      snrDb: 7.5,
    });
    expect(parseLiveMessage('{"type":"connected","config":{"sample_rate":8000}}')).toEqual({
      type: "connected",
      config: { sample_rate: 8000 },
    });
    expect(
      parseLiveMessage(
        '{"type":"transcript","text":"[MOCK] segment fixture","segment_index":1,"latency":12}',
      ),
    ).toMatchObject({
      type: "transcript",
      text: "[MOCK] segment fixture",
      final: true,
      segmentIndex: 1,
      latencyMs: 12,
    });
    expect(() => parseLiveMessage("not json")).toThrow(/invalid JSON/);
  });
});

describe("evaluation report", () => {
  it("reads sections with fractional WER/CER", () => {
    const report = parseEvaluationReport({
      dataset: "dev",
      clip_count: 120,
      sections: {
        comparison: [
          { id: "A", pipeline: "C0", wer: 0.2, cer: 0.1, latency_ms: 400, candidate: "C0" },
        ],
      },
    });
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
