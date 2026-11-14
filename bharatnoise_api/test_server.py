"""Run with:  pytest -q test_server.py

Uses a fake Gnani REST client and a fake Gnani streaming server on localhost,
so nothing here touches the real API or spends credit.
"""
import asyncio
import csv
import io
import json
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

try:
    from .gnani_client import FatalAPIError, GnaniClient, Transcript
    from .server import Settings, create_app
    from . import pipeline as pl
except ImportError:
    from gnani_client import FatalAPIError, GnaniClient, Transcript
    from server import Settings, create_app
    import pipeline as pl

HERE = Path(__file__).parent


# ---------------------------------------------------------------- test helpers
def speechy_wav(seconds=2.0, sr=16000, seed=0) -> bytes:
    """Bursts of voiced-looking tone over low noise: enough for the VAD and SNR estimate."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sr)) / sr
    burst = (np.sin(2 * np.pi * 2.0 * t) > 0).astype(float)
    x = 0.3 * burst * np.sin(2 * np.pi * 400 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 5 * t)) + 0.01 * rng.standard_normal(len(t))
    buf = io.BytesIO()
    sf.write(buf, x.astype("float32"), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class FakeClient:
    def __init__(self, texts=None, ok=True, raises=None):
        self.texts, self.ok, self.raises, self.calls = texts or {}, ok, raises, []

    def transcribe(self, path, language, *, model="v2.5", fmt="verbatim", run_id=0, extra=None, use_cache=True):
        started = time.monotonic()
        if self.raises:
            raise self.raises
        variant = Path(path).stem
        self.calls.append((variant, model, language, fmt))
        if not self.ok:
            return Transcript(ok=False, error="HTTP 400 EMPTY_AUDIO: nothing")
        text = self.texts.get((variant, model), "[MOCK] test utterance")
        return Transcript(ok=True, text=text, model=f"mock-prisma-{model}",
                          wall_seconds=time.monotonic() - started)

def make_app(tmp_path, fake=None, **overrides):
    s = Settings(workdir=tmp_path / "work", results_dir=tmp_path / "results", samples_dir=tmp_path / "samples",
                 config_dir=HERE / "config", web_dir=tmp_path / "no-web", daily_call_cap=50,
                 live_stop_grace_s=0.2, live_max_s=20)
    for k, v in overrides.items():
        setattr(s, k, v)
    fake = fake or FakeClient()
    return create_app(s, client_factory=lambda: fake), fake


def upload(client, data=None, **form):
    files = {"file": ("call.wav", data or speechy_wav(), "audio/wav")}
    return client.post("/api/clips", files=files, data=form)


def run_job(client, clip_id, **body):
    r = client.post("/api/jobs", json={"clip_id": clip_id, **body})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    for _ in range(100):
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ------------------------------------------------------------------------ meta
def test_health_and_config(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"] is True
        legacy_health = c.get("/health")
        assert legacy_health.status_code == 200
        assert set(legacy_health.json()) == {"status", "gnani_configured", "version", "gnani_mode"}
        assert legacy_health.json()["gnani_mode"] == "mock"
        cfg = c.get("/api/config").json()
        assert cfg["gnani_mode"] == "mock"
        avail = {x["id"]: x["available"] for x in cfg["candidates"]}
        assert avail == {
            "C0": True, "C1": True, "C2": True,
            "C2B": True, "C2C": True, "C3": False,
        }
        assert {m["id"] for m in cfg["modes"]} == {"raw", "fast", "accuracy", "compare_all"}
        assert cfg["router"]["fitted"] is False
        assert cfg["suppression"]["fitted"] is False
        assert cfg["imcra_omlsa"]["fitted"] is False
        assert cfg["imcra_omlsa"]["oa_sweep"] == [0.0, 0.05, 0.1, 0.2, 0.3, 0.4]
        assert cfg["optional_features"]["deepfilternet"]["available"] is False
        assert cfg["optional_features"]["deepfilternet"]["reason"]
        assert cfg["live"]["frame_bytes"] == 1024 and cfg["live"]["frame_ms"] == 64
        assert cfg["limits"]["max_seconds"] == 30.0
        assert c.get("/health").json()["status"] == "ok"
        paths = c.get("/openapi.json").json()["paths"]
        for path in ("/api/clips", "/api/jobs", "/api/score", "/api/results/compare", "/api/results/tables",
                     "/api/results/{tag}/export",
                     "/process", "/evaluation"):
            assert path in paths
        for path, method, schema_name in (
            ("/health", "get", "LegacyHealthResponse"),
            ("/process", "post", "LegacyProcessResponse"),
            ("/evaluation", "get", "LegacyEvaluationResponseModel"),
        ):
            response_schema = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
            assert response_schema["$ref"].endswith(f"/{schema_name}")


def test_tts_returns_audio_and_uses_multilingual_auto_mode(tmp_path, monkeypatch):
    calls = []
    transcript_audio = speechy_wav(seconds=0.1)

    def fake_synthesize(self, text, *, language="auto"):
        calls.append((text, language))
        return transcript_audio, "audio/wav"

    monkeypatch.setenv("GNANI_API_KEY", "offline-test-key")
    monkeypatch.setenv("GNANI_BASE_URL", "https://api.vachana.ai")
    monkeypatch.setattr(GnaniClient, "synthesize", fake_synthesize)
    settings = Settings(
        workdir=tmp_path / "work",
        results_dir=tmp_path / "results",
        samples_dir=tmp_path / "samples",
        config_dir=HERE / "config",
        web_dir=tmp_path / "no-web",
        daily_call_cap=50,
    )
    app = create_app(settings)
    transcript = "नमस्ते, வணக்கம், hello"
    with TestClient(app) as c:
        before = c.get("/api/config").json()["budget"]["remaining"]
        response = c.post("/api/tts", json={"text": transcript})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/wav")
        assert response.content.startswith(b"RIFF")
        assert calls == [(transcript, "auto")]
        assert c.get("/api/config").json()["budget"]["remaining"] == before - 1


def test_tts_rejects_blank_text_and_respects_demo_auth(tmp_path):
    app, _ = make_app(tmp_path, demo_token="expected-token")
    with TestClient(app) as c:
        unauthorized = c.post("/api/tts", json={"text": "hello"})
        assert unauthorized.status_code == 401
        blank = c.post("/api/tts", json={"text": "   "}, headers={"X-Demo-Token": "expected-token"})
        assert blank.status_code == 422
        assert blank.json()["error"]["code"] == "EMPTY_TEXT"
        live_only = c.post("/api/tts", json={"text": "hello"}, headers={"X-Demo-Token": "expected-token"})
        assert live_only.status_code == 503
        assert live_only.json()["error"]["code"] == "TTS_UNAVAILABLE"


def test_results_export_supports_csv_and_json_with_safe_tags(tmp_path):
    app, _ = make_app(tmp_path)
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "C0.csv").write_text("id,candidate\nclip-1,C0\n", encoding="utf-8")
    with TestClient(app) as client:
        json_response = client.get("/api/results/C0/export?format=json")
        csv_response = client.get("/api/results/C0/export?format=csv")
        assert json_response.status_code == 200
        assert json_response.json() == [{"id": "clip-1", "candidate": "C0"}]
        assert csv_response.status_code == 200
        assert csv_response.headers["content-type"].startswith("text/csv")
        assert csv_response.text == "id,candidate\nclip-1,C0\n"
        assert client.get("/api/results/../outside/export").status_code in {404, 422}


def test_app_creation_does_not_create_data_directories(tmp_path, monkeypatch):
    data_dir = tmp_path / "configured-data"
    monkeypatch.setenv("BN_DATA_DIR", str(data_dir))
    settings = Settings.from_env()

    assert settings.workdir == data_dir / "workdir"
    assert settings.results_dir == data_dir / "results"
    create_app(settings)
    assert not data_dir.exists()


def test_root_paths_do_not_depend_on_current_working_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("BN_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env()

    assert settings.workdir == HERE / "workdir"
    assert settings.results_dir == HERE / "results"


# ----------------------------------------------------------------------- clips
def test_clip_lifecycle(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        r = upload(c, language="hi-IN")
        assert r.status_code == 201
        body = r.json()
        cid = body["clip_id"]
        assert body["variants"] == ["C0", "C1", "C2"]
        a = body["analysis"]
        assert a["duration_s"] == pytest.approx(2.0, abs=0.01)
        assert a["stationary_snr_db_est"] is not None and a["segments"]

        for v in ("C0", "C1", "C2"):
            audio = c.get(f"/api/clips/{cid}/audio", params={"variant": v})
            assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
        data, sr = sf.read(io.BytesIO(c.get(f"/api/clips/{cid}/audio", params={"variant": "C1"}).content))
        assert sr == 8000 and np.max(np.abs(data)) <= 1.0

        assert c.get(f"/api/clips/{cid}/audio", params={"variant": "C9"}).json()["error"]["code"] == "UNKNOWN_VARIANT"
        assert c.get(f"/api/clips/{cid}/spectrogram.png").content[:4] == b"\x89PNG"
        assert c.get(f"/api/clips/{cid}").json()["analysis"]["duration_s"] == a["duration_s"]

        assert c.delete(f"/api/clips/{cid}").status_code == 204
        assert c.get(f"/api/clips/{cid}").json()["error"]["code"] == "CLIP_NOT_FOUND"


def test_candidate_outputs_are_finite_and_limited():
    audio = pl.load_audio(speechy_wav())
    for candidate in ("C0", "C1", "C2"):
        output, meta = pl.apply_candidate(candidate, audio)
        assert output.dtype == np.float32
        assert np.isfinite(output).all()
        assert np.max(np.abs(output)) <= 1.0
        assert np.isfinite(meta["gain_applied_db"])


def test_frontend_process_accepts_audio_not_aligned_to_vad_frames(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        body = {
            "mode": "max", "sample_rate": "8000", "channels": "1", "sample_format": "pcm_s16le",
            "asr_provider": "gnani_prisma", "asr_model": "prisma-v2.5",
            "language_code": "hi-IN", "format": "auto",
            "candidates": "C0,C1,C2", "selection": "consensus", "compare_models": "false",
        }
        response = c.post(
            "/process",
            files={"file": ("unaligned.wav", speechy_wav(0.51, sr=8000), "audio/wav")},
            data=body,
        )
        assert response.status_code == 200, response.text


def test_upload_validation(tmp_path):
    app, _ = make_app(tmp_path, max_upload_mb=0.5)
    with TestClient(app) as c:
        assert upload(c, b"not audio at all").json()["error"]["code"] == "BAD_AUDIO"
        assert upload(c, speechy_wav(31.0, sr=8000)).status_code in (413, 422)       # too long or too large
        assert upload(c, speechy_wav(0.1)).json()["error"]["code"] == "BAD_AUDIO"      # too short
        assert upload(c, speechy_wav(20.0, sr=44100)).status_code == 413               # over the size limit
        assert upload(c, language="xx-YY").json()["error"]["code"] == "BAD_LANGUAGE"
        r = c.post("/api/clips", data={})                                              # neither file nor sample
        assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_REQUEST"


def test_too_long_clip_message(tmp_path):
    app, _ = make_app(tmp_path, max_upload_mb=5)
    with TestClient(app) as c:
        r = upload(c, speechy_wav(31.0, sr=8000))
        assert r.status_code == 422 and "30" in r.json()["error"]["message"]


def test_samples(tmp_path):
    (tmp_path / "samples").mkdir()
    (tmp_path / "samples" / "a.wav").write_bytes(speechy_wav())
    (tmp_path / "samples" / "index.json").write_text(json.dumps(
        [{"id": "s1", "file": "a.wav", "language": "kn-IN", "group": "traffic_snr0", "reference": "ref text"}]))
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        listed = c.get("/api/samples").json()
        assert listed[0]["id"] == "s1" and "file" not in listed[0]
        r = c.post("/api/clips", data={"sample_id": "s1"})
        assert r.status_code == 201 and r.json()["language"] == "kn-IN"
        assert c.get(f"/api/clips/{r.json()['clip_id']}").json()["reference"] == "ref text"
        assert c.post("/api/clips", data={"sample_id": "nope"}).json()["error"]["code"] == "SAMPLE_NOT_FOUND"


def test_demo_token(tmp_path):
    app, _ = make_app(tmp_path, demo_token="secret")
    with TestClient(app) as c:
        assert c.get("/api/config").json()["demo_token_required"] is True
        assert upload(c).status_code == 401
        r = c.post("/api/clips", files={"file": ("a.wav", speechy_wav(), "audio/wav")}, headers={"X-Demo-Token": "secret"})
        assert r.status_code == 201


# ------------------------------------------------------------------------ jobs
def test_raw_job_with_score(tmp_path):
    app, fake = make_app(tmp_path)
    with TestClient(app) as c:
        cid = upload(c).json()["clip_id"]
        j = run_job(c, cid, mode="raw", language="hi-IN", model="v2.5", reference="[MOCK] test utterance")
        assert j["status"] == "done", j
        res = j["result"]
        assert res["chosen"]["variant"] == "C0"
        assert res["chosen"]["score"] is None
        assert res["score_suppressed_reason"] == "mock"
        assert res["model"] == "mock-prisma-v2.5"
        assert res["chosen"]["text"].startswith("[MOCK]")
        assert res["api_calls"] == 1 and fake.calls == [("C0", "v2.5", "hi-IN", "verbatim")]
        assert j["steps"]


def test_fast_job_routes_guards_and_flags_unfitted_router(tmp_path):
    app, fake = make_app(tmp_path)
    with TestClient(app) as c:
        cid = upload(c).json()["clip_id"]
        j = run_job(c, cid, mode="fast", language="hi-IN", model="v3.0")
        assert j["status"] == "done", j
        d = j["result"]["decision"]
        assert d["routed_to"] == "C0" and d["router_fitted"] is False
        assert d["guard"]["checked"] is True and "lsd_db" in d["guard"]
        assert j["result"]["router_note"]
        assert len(fake.calls) == 1 and fake.calls[0][1] == "v3.0"


def test_router_ignores_placeholder_snr_rules_even_if_marked_fitted():
    variant, note = pl.route(
        {"stationary_snr_db_est": 30.0},
        ["C0", "C1", "C2"],
        {
            "fitted": True,
            "routing_estimator_validated": False,
            "rules": [{"min_snr_db": -100, "variant": "C2"}],
        },
    )

    assert variant == "C0"
    assert "SNR estimator is not validated" in note


def test_compare_all_job(tmp_path):
    app, fake = make_app(tmp_path)
    with TestClient(app) as c:
        cid = upload(c).json()["clip_id"]
        j = run_job(c, cid, mode="compare_all", language="hi-IN", model="v2.5")
        assert j["status"] == "done", j
        assert [h["variant"] for h in j["result"]["hypotheses"]] == ["C0", "C1", "C2"]
        assert j["result"]["chosen"]["variant"] in ("C0", "C1", "C2")
        assert j["result"]["suppression_fitted"] is False


def test_frontend_process_contract_and_measured_scores(tmp_path):
    app, fake = make_app(tmp_path)
    with TestClient(app) as c:
        body = {
            "mode": "fast", "sample_rate": "8000", "channels": "1", "sample_format": "pcm_s16le",
            "asr_provider": "gnani_prisma", "asr_model": "prisma-v2.5",
            "candidates": "C0,C1,C2,C3", "selection": "snr_router", "compare_models": "false",
        }
        response = c.post("/process", files={"file": ("sample.wav", speechy_wav(), "audio/wav")}, data=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["selected_candidate"] in {"C0", "C1", "C2"}
        assert set(result) == {
            "request_id", "gnani_mode", "output_format", "language_code", "detected_language_code",
            "unavailable_candidates", "selected_candidate", "transcription", "candidates",
            "analysis", "evaluation", "pipeline",
            "latency_ms", "router_fitted", "suppression_fitted",
            "score_suppressed_reason",
        }
        assert result["output_format"] == "verbatim"
        assert result["language_code"] == "hi-IN"
        assert isinstance(result["unavailable_candidates"], dict)
        assert set(result["candidates"][result["selected_candidate"]]) == {
            "transcript", "audio_url", "peaks", "guard_score", "confidence", "snr_db",
            "error", "truncated", "parameters",
        }
        assert set(result["analysis"]) == {
            "stationary_snr_db_est", "noise_floor_db", "speech_energy_db", "vad_segments", "guard_status",
            "guard_result", "spectrum",
        }
        assert result["gnani_mode"] == "mock"
        assert result["candidates"][result["selected_candidate"]]["transcript"].startswith("[MOCK]")
        assert result["candidates"][result["selected_candidate"]]["audio_url"].startswith("/api/clips/")
        assert result["evaluation"]["calls"] == 1
        assert result["evaluation"]["wer"] is None
        assert set(result["evaluation"]) == {"wer", "cer", "latency_ms", "calls", "note"}
        assert fake.calls and len(fake.calls) == 1
        assert fake.calls[0][3] is None

        body.update({"mode": "max", "selection": "consensus"})
        response = c.post(
            "/process", files={"file": ("sample.wav", speechy_wav(), "audio/wav")},
            data={**body, "reference": "[MOCK] test utterance"},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["selected_candidate"] in {"C0", "C1", "C2"}
        assert result["evaluation"]["wer"] is None
        assert result["score_suppressed_reason"] == "mock"
        assert result["evaluation"]["calls"] == 3


def test_max_keeps_empty_transcripts_out_of_consensus_and_reports_failures(tmp_path):
    class PartialClient(FakeClient):
        def transcribe(self, path, language, *, model="v2.5", fmt="verbatim", run_id=0, extra=None, use_cache=True):
            variant = Path(path).stem
            if variant == "C1":
                self.calls.append((variant, model, language, fmt))
                return Transcript(ok=False, error="HTTP 400 BAD_REQUEST")
            if variant == "C0":
                self.texts[("C0", model)] = ""
            if variant == "C2":
                self.texts[("C2", model)] = "[MOCK] recognized words"
            return super().transcribe(
                path, language, model=model, fmt=fmt, run_id=run_id, extra=extra, use_cache=use_cache
            )

    app, fake = make_app(tmp_path, PartialClient())
    with TestClient(app) as c:
        response = c.post(
            "/process",
            files={"file": ("sample.wav", speechy_wav(), "audio/wav")},
            data={
                "mode": "max", "sample_rate": "8000", "channels": "1",
                "sample_format": "pcm_s16le", "asr_provider": "gnani_prisma",
                "asr_model": "prisma-v2.5", "candidates": "C0,C1,C2",
                "selection": "consensus", "compare_models": "false",
            },
        )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["selected_candidate"] == "C2"
    assert result["transcription"] == "[MOCK] recognized words"
    assert result["candidates"]["C0"]["transcript"] == ""
    assert result["candidates"]["C1"]["error"] == "HTTP 400 BAD_REQUEST"
    assert result["candidates"]["C2"]["truncated"] is False
    assert len(fake.calls) == 3


def test_max_with_only_empty_transcripts_returns_diagnostic_result(tmp_path):
    fake = FakeClient(texts={
        ("C0", "v2.5"): "",
        ("C1", "v2.5"): "",
        ("C2", "v2.5"): "",
    })
    app, _ = make_app(tmp_path, fake)
    with TestClient(app) as c:
        response = c.post(
            "/process",
            files={"file": ("sample.wav", speechy_wav(), "audio/wav")},
            data={
                "mode": "max", "sample_rate": "8000", "channels": "1",
                "sample_format": "pcm_s16le", "asr_provider": "gnani_prisma",
                "asr_model": "prisma-v2.5", "candidates": "C0,C1,C2",
                "selection": "consensus", "compare_models": "false",
            },
        )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["selected_candidate"] == "C0"
    assert result["transcription"] == ""
    assert all(candidate["transcript"] == "" for candidate in result["candidates"].values())


def test_experimental_candidates_are_configurable_and_report_parameters(tmp_path):
    app, fake = make_app(tmp_path)
    with TestClient(app) as c:
        response = c.post(
            "/process",
            files={"file": ("sample.wav", speechy_wav(), "audio/wav")},
            data={
                "mode": "max", "sample_rate": "8000", "channels": "1",
                "sample_format": "pcm_s16le", "asr_provider": "gnani_prisma",
                "asr_model": "prisma-v3.0", "language_code": "hi-IN",
                "candidates": "C0,C2B,C2C", "selection": "consensus",
                "compare_models": "false", "oa_lambda": "0.3",
            },
        )

    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result["candidates"]) == {"C0", "C2B", "C2C"}
    assert result["candidates"]["C2B"]["parameters"]["algorithm"] == (
        "simplified adaptive spectral gain + VAD-gated speech leveling"
    )
    assert result["candidates"]["C2B"]["parameters"]["voice_focus"]["applied"] is True
    assert result["candidates"]["C2B"]["parameters"]["parameters_fitted"] is False
    assert result["candidates"]["C2C"]["parameters"]["observation_addition_lambda"] == 0.3
    assert result["candidates"]["C2C"]["parameters"]["observation_addition_mode"] == (
        "enhanced + lambda * original"
    )
    assert len(fake.calls) == 3


def test_experimental_candidate_rejects_invalid_oa_lambda(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        response = c.post(
            "/process",
            files={"file": ("sample.wav", speechy_wav(), "audio/wav")},
            data={
                "mode": "max", "sample_rate": "8000", "channels": "1",
                "sample_format": "pcm_s16le", "asr_provider": "gnani_prisma",
                "asr_model": "prisma-v3.0", "candidates": "C0,C2C",
                "selection": "consensus", "compare_models": "false", "oa_lambda": "0.7",
            },
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BAD_OA_LAMBDA"


def test_frontend_process_passes_prisma_format_and_auto_language(tmp_path):
    app, fake = make_app(tmp_path)
    with TestClient(app) as c:
        response = c.post(
            "/process",
            files={"file": ("sample.wav", speechy_wav(), "audio/wav")},
            data={
                "mode": "fast", "sample_rate": "8000", "channels": "1",
                "sample_format": "pcm_s16le", "asr_provider": "gnani_prisma",
                "asr_model": "prisma-v3.0", "language_code": "indic-auto",
                "format": "codemix", "candidates": "C0,C1,C2",
                "selection": "snr_router", "compare_models": "false",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["output_format"] == "codemix"
        assert response.json()["language_code"] == "indic-auto"
        assert fake.calls and fake.calls[0][2:] == ("indic-auto", "codemix")


def test_frontend_auto_format_uses_gnani_model_language_default(tmp_path):
    app, fake = make_app(tmp_path)
    with TestClient(app) as c:
        response = c.post(
            "/process",
            files={"file": ("sample.wav", speechy_wav(), "audio/wav")},
            data={
                "mode": "fast", "sample_rate": "8000", "channels": "1",
                "sample_format": "pcm_s16le", "asr_provider": "gnani_prisma",
                "asr_model": "prisma-v3.0", "language_code": "hi-IN",
                "candidates": "C0,C1,C2", "selection": "snr_router",
                "compare_models": "false",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["output_format"] == "transcribe"
        assert fake.calls and fake.calls[0][3] is None


def test_aggregate_routes_are_explicit_when_missing(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        empty = c.get("/evaluation")
        assert empty.status_code == 200
        assert empty.json() == {
            "available": False,
            "reason": "No benchmark results have been generated yet.",
            "dataset": None,
            "clip_count": None,
            "measured_at": None,
            "sections": {"comparison": [], "ablation": [], "snr": [], "noise": [], "source_type": []},
        }
        result = c.get("/api/results/tables")
        assert result.status_code == 404
        assert result.json()["error"]["code"] == "AGGREGATES_NOT_FOUND"


def test_build_eval_set_meets_snr_and_speaker_split(tmp_path):
    data = tmp_path / "source"
    audio_dir = data / "audio"
    noise_dir = tmp_path / "noise" / "street" / "dev"
    audio_dir.mkdir(parents=True)
    noise_dir.mkdir(parents=True)
    rng = np.random.default_rng(42)
    t = np.arange(8000) / 8000
    clean_rows = []
    for index in range(5):
        wave = (0.25 * np.sin(2 * np.pi * (220 + index * 10) * t)).astype(np.float32)
        sf.write(audio_dir / f"clean{index}.wav", wave, 8000)
        clean_rows.append({
            "id": f"clean{index}", "audio_path": f"audio/clean{index}.wav",
            "language": "hi-IN", "reference": "synthetic speech", "speaker_id": f"speaker{index}", "group": "clean",
        })
    with (data / "clean.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(clean_rows[0]))
        writer.writeheader()
        writer.writerows(clean_rows)
    sf.write(noise_dir / "noise.wav", (0.05 * rng.standard_normal(8000)).astype(np.float32), 8000)
    with (tmp_path / "noise" / "LICENSES.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file", "type", "source", "license", "note"])
        writer.writeheader()
        writer.writerow({
            "file": "street/dev/noise.wav", "type": "synthetic",
            "source": "synthetic test fixture", "license": "synthetic",
            "note": "Test-only generated noise.",
        })
    output = tmp_path / "eval"
    script = Path(__file__).parent / "scripts" / "build_eval_set.py"
    subprocess.run(
        [sys.executable, str(script), "--manifest", str(data / "clean.csv"), "--noise-dir", str(tmp_path / "noise"),
         "--output-dir", str(output), "--snrs", "0", "5", "10", "--seed", "17", "--legacy-plumbing-only"],
        check=True, capture_output=True, text=True,
    )
    log = json.loads((output / "build_log.json").read_text(encoding="utf-8"))
    assert len(log["sources"]) == 15
    for item in log["sources"]:
        assert abs(item["measured_linear_mix_snr_db"] - item["target_snr_db"]) < 0.5
    with (output / "dev.csv").open(newline="", encoding="utf-8") as handle:
        dev = list(csv.DictReader(handle))
    with (output / "test.csv").open(newline="", encoding="utf-8") as handle:
        test = list(csv.DictReader(handle))
    assert {row["speaker_id"] for row in dev}.isdisjoint({row["speaker_id"] for row in test})
    assert dev and test


def test_accuracy_job_picks_consensus(tmp_path):
    texts = {
        ("C0", "v2.5"): "[MOCK] outlier tokens", ("C1", "v2.5"): "[MOCK] consensus tokens", ("C2", "v2.5"): "[MOCK] consensus tokens",
        ("C0", "v3.0"): "[MOCK] consensus tokens", ("C1", "v3.0"): "[MOCK] consensus tokens", ("C2", "v3.0"): "[MOCK] consensus tokens",
    }
    app, fake = make_app(tmp_path, FakeClient(texts))
    with TestClient(app) as c:
        cid = upload(c).json()["clip_id"]
        j = run_job(c, cid, mode="accuracy", language="hi-IN", model="v2.5")
        assert j["status"] == "done", j
        res = j["result"]
        assert [h["key"] for h in res["hypotheses"]] == [
            "C0@mock-prisma-v2.5", "C1@mock-prisma-v2.5", "C2@mock-prisma-v2.5",
            "C0@mock-prisma-v3.0", "C1@mock-prisma-v3.0", "C2@mock-prisma-v3.0"
        ]
        assert res["chosen"]["key"] == "C1@mock-prisma-v2.5"
        assert res["chosen"]["text"] == "[MOCK] consensus tokens"
        assert res["decision"]["total_distance"]["C0@mock-prisma-v2.5"] > res["decision"]["total_distance"]["C1@mock-prisma-v3.0"]
        assert res["score_suppressed_reason"] == "mock"
        assert len(fake.calls) == 6


def test_accuracy_ties_go_to_original(tmp_path):
    app, _ = make_app(tmp_path)  # every hypothesis identical
    with TestClient(app) as c:
        cid = upload(c).json()["clip_id"]
        j = run_job(c, cid, mode="accuracy", language="hi-IN", model="v2.5")
        assert j["result"]["chosen"]["key"] == "C0@mock-prisma-v2.5"


def test_job_validation_and_budget(tmp_path):
    app, _ = make_app(tmp_path, daily_call_cap=2)
    with TestClient(app) as c:
        cid = upload(c).json()["clip_id"]
        post = lambda **b: c.post("/api/jobs", json={"clip_id": cid, **b})
        assert post(mode="raw", language="indic-auto", model="v2.5").json()["error"]["code"] == "BAD_LANGUAGE"
        assert post(mode="accuracy", language="indic-auto", model="v3.0").json()["error"]["code"] == "BAD_LANGUAGE"
        assert post(mode="raw", language="xx-YY").json()["error"]["code"] == "BAD_LANGUAGE"
        assert c.post("/api/jobs", json={"clip_id": "nope", "mode": "raw"}).json()["error"]["code"] == "CLIP_NOT_FOUND"
        assert post(mode="raw", language="hi-IN").status_code == 202                      # uses 1 of 2
        assert post(mode="accuracy", language="hi-IN").json()["error"]["code"] == "DEMO_BUDGET_EXHAUSTED"
        assert c.get("/api/config").json()["budget"]["remaining"] == 1


def test_job_failures_are_reported_not_raised(tmp_path):
    app, _ = make_app(tmp_path, FakeClient(ok=False))
    with TestClient(app) as c:
        cid = upload(c).json()["clip_id"]
        j = run_job(c, cid, mode="raw", language="hi-IN")
        assert j["status"] == "error" and j["error"]["code"] == "ASR_FAILED"
    app, _ = make_app(tmp_path, FakeClient(raises=FatalAPIError("HTTP 402 INSUFFICIENT_CREDIT")))
    with TestClient(app) as c:
        cid = upload(c).json()["clip_id"]
        j = run_job(c, cid, mode="raw", language="hi-IN")
        assert j["status"] == "error" and j["error"]["code"] == "GNANI_FATAL"
        j2 = run_job(c, cid, mode="raw", language="hi-IN")   # worker survived the failure
        assert j2["status"] == "error"


def test_missing_key_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    s = Settings(workdir=tmp_path / "w", config_dir=HERE / "config", web_dir=tmp_path / "x", results_dir=tmp_path / "r")
    with TestClient(create_app(s)) as c:   # no client_factory: real client, no key
        assert c.get("/api/health").json()["gnani_mode"] == "unconfigured"
        assert c.get("/api/config").json()["gnani_mode"] == "unconfigured"
        cid = upload(c).json()["clip_id"]
        j = run_job(c, cid, mode="raw", language="hi-IN")
        assert j["status"] == "error" and j["error"]["code"] == "GNANI_KEY_MISSING"
        assert c.get("/api/health").json()["gnani_key_configured"] is False


def test_gnani_key_is_redacted_from_responses_logs_and_errors(tmp_path, monkeypatch, caplog):
    secret = "distinctive-bharatnoise-test-key"
    monkeypatch.setenv("GNANI_API_KEY", secret)
    app, _ = make_app(tmp_path, FakeClient(raises=FatalAPIError(f"upstream failure {secret}")))
    with TestClient(app) as c:
        responses = [c.get("/api/health"), c.get("/api/config")]
        cid = upload(c).json()["clip_id"]
        responses.append(c.post("/api/jobs", json={
            "clip_id": cid, "mode": "raw", "language": "hi-IN", "model": "v2.5",
        }))
        job_id = responses[-1].json()["job_id"]
        for _ in range(100):
            job_response = c.get(f"/api/jobs/{job_id}")
            if job_response.json()["status"] == "error":
                break
            time.sleep(0.05)
        responses.append(job_response)

    exposed = "\n".join(response.text for response in responses) + caplog.text
    assert secret not in exposed
    assert responses[-1].json()["error"]["message"] == "upstream failure [REDACTED]"


# --------------------------------------------------------------------- scoring
def test_score_route(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/score", json={"reference": "the cat sat", "hypothesis": "The dog sat down."}).json()
        assert r["score"]["sub"] == 1 and r["score"]["ins"] == 1 and r["score"]["wer"] == pytest.approx(2 / 3)
        assert [o["op"] for o in r["ops"]] == ["ok", "sub", "ok", "ins"]
        empty = c.post("/api/score", json={"reference": "", "hypothesis": "x"}).json()
        assert empty["score"] is None


# --------------------------------------------------------------------- results
def write_results(path: Path, rows):
    fields = ["id", "group", "language", "model", "run", "reference", "hypothesis", "error"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({"model": "prisma-v2.5", "run": 0, "language": "hi-IN", "error": "", **r})


def test_results_routes(tmp_path):
    (tmp_path / "results").mkdir()
    ids = [f"c{i}" for i in range(8)]
    write_results(tmp_path / "results" / "A.csv",
                  [{"id": i, "group": "g%d" % (n % 2), "reference": "a b c d", "hypothesis": "a x c d"} for n, i in enumerate(ids)])
    write_results(tmp_path / "results" / "B.csv",
                  [{"id": i, "group": "g%d" % (n % 2), "reference": "a b c d", "hypothesis": "a b c d"} for n, i in enumerate(ids)])
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        assert {x["tag"]: x["rows"] for x in c.get("/api/results").json()} == {"A": 8, "B": 8}
        r = c.get("/api/results/compare", params={"a": "A", "b": "B", "by": "group", "n_boot": 300}).json()
        assert r["overall"]["n_source_clips"] == 8 and r["overall"]["underpowered"] is True
        assert r["overall"]["wer"]["delta"] is None
        assert r["overall"]["B_better"] is None and set(r["by"]) == {"g0", "g1"}
        assert len(c.get("/api/results/A/rows", params={"group": "g0"}).json()) == 4
        assert c.get("/api/results/compare", params={"a": "../x", "b": "B"}).json()["error"]["code"] == "BAD_TAG"
        assert c.get("/api/results/compare", params={"a": "A", "b": "missing"}).json()["error"]["code"] == "RESULTS_NOT_FOUND"


def test_results_empty_when_no_directory(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/results").json() == []


# ------------------------------------------------------------------- live WS
class FakeUpstream:
    """Stands in for wss://api.vachana.ai/stt/v3/stream."""

    def __init__(self):
        self.headers, self.frame_sizes, self.frame_times, self.texts, self.port = {}, [], [], [], None

    def start(self):
        ready = threading.Event()

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def handler(ws):
                self.headers = {k.lower(): v for k, v in ws.request.headers.items()}
                await ws.send(json.dumps({"type": "connected", "config": {"sample_rate": 8000, "chunk_size": 512}}))
                async for msg in ws:
                    if isinstance(msg, bytes):
                        self.frame_sizes.append(len(msg))
                        self.frame_times.append(time.monotonic())
                    else:
                        self.texts.append(msg)
                        if json.loads(msg).get("type") == "commit":
                            await ws.send(json.dumps({"type": "transcript", "text": "[MOCK] segment fixture",
                                                      "segment_index": 1}))

            async def main():
                from websockets.asyncio.server import serve
                async with serve(handler, "127.0.0.1", 0) as server:
                    self.port = server.sockets[0].getsockname()[1]
                    ready.set()
                    await asyncio.Future()

            loop.run_until_complete(main())

        threading.Thread(target=run, daemon=True).start()
        assert ready.wait(5)
        return self


def collect(ws):
    got = []
    try:
        while True:
            got.append(json.loads(ws.receive_text()))
    except WebSocketDisconnect:
        pass
    return got


def test_live_ws_reframes_paces_and_relays(tmp_path, monkeypatch):
    up = FakeUpstream().start()
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    app, _ = make_app(tmp_path, ws_url=f"ws://127.0.0.1:{up.port}")
    with TestClient(app) as c, c.websocket_connect("/ws/live?language=hi-IN&variant=C1&model=v3.0") as ws:
        for _ in range(6):
            ws.send_bytes(bytes(700))             # odd-sized chunks: 4,200 bytes, not a multiple of 1,024
        ws.send_text(json.dumps({"type": "stop"}))
        msgs = collect(ws)
    kinds = [m["type"] for m in msgs]
    assert "connected" in kinds and "transcript" in kinds
    assert up.frame_sizes and set(up.frame_sizes) == {1024}                       # every frame exactly 1,024 bytes
    assert len(up.frame_sizes) >= 4 + 1 + 15                                      # audio + padded tail + ~1 s silence
    gaps = [b - a for a, b in zip(up.frame_times, up.frame_times[1:])]
    assert 0.05 <= statistics.median(gaps) <= 0.09                                # real-time cadence, not a burst
    assert up.headers["x-api-key-id"] == "test-key" and up.headers["x-sample-rate"] == "8000"
    assert up.headers["language_code"] == "hi-IN" and up.headers["model"] == "prisma-v3.0"
    assert up.headers["x-vad-threshold"] == "0.7" and up.headers["x-min-speech-ms"] == "200"
    assert any(json.loads(t).get("type") == "commit" for t in up.texts)


def test_live_ws_refusals(tmp_path, monkeypatch):
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        with c.websocket_connect("/ws/live") as ws:
            assert "GNANI_API_KEY" in json.loads(ws.receive_text())["message"]
        monkeypatch.setenv("GNANI_API_KEY", "k")
        with c.websocket_connect("/ws/live?variant=C3") as ws:
            assert json.loads(ws.receive_text())["type"] == "error"
        with c.websocket_connect("/ws/live?language=indic-auto&model=v2.5") as ws:
            assert json.loads(ws.receive_text())["type"] == "error"
    app, _ = make_app(tmp_path, demo_token="t")
    with TestClient(app) as c, c.websocket_connect("/ws/live") as ws:
        assert "token" in json.loads(ws.receive_text())["message"].lower()


def test_live_ws_reports_unreachable_upstream(tmp_path, monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "k")
    app, _ = make_app(tmp_path, ws_url="ws://127.0.0.1:9")   # nothing listens on port 9
    with TestClient(app) as c, c.websocket_connect("/ws/live") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "error" and "Gnani streaming" in msg["message"]
