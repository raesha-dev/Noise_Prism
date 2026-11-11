"""BharatNoise prototype API (FastAPI).

Run:   uvicorn server:app --workers 1
Docs:  http://localhost:8000/docs   (interactive, generated from the routes below)

Why one worker: clips, jobs and the single serial job queue live in this process's memory.
Gnani limits an account to one active REST request, so every Prisma call goes through one
worker. Several uvicorn workers would break both the state and that rule.

Environment:
  GNANI_API_KEY       required for transcription and live mode (never sent to the browser)
  BN_CORS_ORIGINS     comma-separated allowed origins (default: local dev servers)
  BN_DEMO_TOKEN       if set, POST /api/clips, POST /api/jobs and /ws/live require it
  BN_DAILY_CALL_CAP   max Prisma calls per day for the whole demo (default 300)
  BN_MAX_UPLOAD_MB    default 5
  BN_TTL_MINUTES      uploaded clips are deleted after this long (default 30)
"""
import asyncio
import csv
import json
import math
import os
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import numpy as np
from fastapi import (Depends, FastAPI, File, Form, Header, Query, Request, UploadFile, WebSocket)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from websockets.asyncio.client import connect as ws_connect

try:
    from . import compare as cmp
    from . import pipeline as pl
    from .gnani_client import FatalAPIError, GnaniClient, Transcript, endpoint_mode
    from .metrics import normalize, score_utterance
except ImportError:
    import compare as cmp
    import pipeline as pl
    from gnani_client import FatalAPIError, GnaniClient, Transcript, endpoint_mode
    from metrics import normalize, score_utterance

ROOT = Path(__file__).resolve().parent
LANGUAGES = {
    "hi-IN": "Hindi", "kn-IN": "Kannada", "en-IN": "English (Indian)", "ta-IN": "Tamil",
    "te-IN": "Telugu", "bn-IN": "Bengali", "gu-IN": "Gujarati", "ml-IN": "Malayalam",
    "mr-IN": "Marathi", "pa-IN": "Punjabi",
}
MODELS = ("v2.5", "v3.0")
MODES = {
    "raw": "Send the original audio to Prisma (the baseline)",
    "fast": "Route to one version by estimated SNR, guard it, one API call",
    "accuracy": "Transcribe several versions on both models, keep the consensus",
    "compare_all": "Transcribe every available version side by side",
}
TAG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
FRAME_BYTES, FRAME_SECONDS = 1024, 0.064  # 512 samples at 8 kHz


# ------------------------------------------------------------------- plumbing
class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message


@dataclass
class Settings:
    workdir: Path = ROOT / "workdir"
    results_dir: Path = ROOT / "results"
    samples_dir: Path = ROOT / "samples"
    config_dir: Path = ROOT / "config"
    web_dir: Path = ROOT / "web"
    cors_origins: list = field(default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"])
    max_upload_mb: float = 5.0
    ttl_minutes: float = 30.0
    daily_call_cap: int = 300
    demo_token: str = ""
    ws_url: str = "wss://api.vachana.ai/stt/v3/stream"
    live_stop_grace_s: float = 3.0
    live_max_s: float = 300.0
    live_frame_ms: int = 64

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        s = cls()
        data_dir = Path(e("BN_DATA_DIR", str(ROOT))).expanduser().resolve()
        s.workdir = data_dir / "workdir"
        s.results_dir = data_dir / "results"
        if e("BN_CORS_ORIGINS"):
            s.cors_origins = [o.strip() for o in e("BN_CORS_ORIGINS").split(",") if o.strip()]
        s.max_upload_mb = float(e("BN_MAX_UPLOAD_MB", s.max_upload_mb))
        s.ttl_minutes = float(e("BN_TTL_MINUTES", s.ttl_minutes))
        s.daily_call_cap = int(e("BN_DAILY_CALL_CAP", s.daily_call_cap))
        s.demo_token = e("BN_DEMO_TOKEN", "")
        s.ws_url = e("GNANI_WS_URL", s.ws_url)
        s.live_stop_grace_s = float(e("BN_LIVE_STOP_GRACE_S", s.live_stop_grace_s))
        s.live_max_s = float(e("BN_LIVE_MAX_SECONDS", s.live_max_s))
        return s


@dataclass
class Clip:
    id: str
    created: float
    audio: np.ndarray
    analysis: dict
    dir: Path
    source: str
    language: Optional[str] = None
    reference: Optional[str] = None
    variants: dict = field(default_factory=dict)
    enhance_ms: dict = field(default_factory=dict)
    candidate_meta: dict = field(default_factory=dict)


@dataclass
class Job:
    id: str
    params: "JobRequest"
    created: float = field(default_factory=time.time)
    status: str = "queued"
    steps: list = field(default_factory=list)
    result: Optional[dict] = None
    error: Optional[dict] = None
    done_event: Optional[asyncio.Event] = None


class JobRequest(BaseModel):
    clip_id: str
    mode: Literal["raw", "fast", "accuracy", "compare_all"] = "fast"
    language: str = "hi-IN"
    model: Literal["v2.5", "v3.0"] = "v2.5"
    reference: Optional[str] = None
    candidate_ids: Optional[list[str]] = None


class ScoreRequest(BaseModel):
    reference: str
    hypothesis: str


class LegacyHealthResponse(BaseModel):
    status: str
    gnani_configured: bool
    version: str
    gnani_mode: Literal["unconfigured", "live", "mock"]


class ApiHealthResponse(LegacyHealthResponse):
    ok: bool
    gnani_key_configured: bool
    clips_in_memory: int
    jobs_in_memory: int


class LegacyCandidateResponse(BaseModel):
    transcript: str | None
    audio_url: str | None
    peaks: list[float]
    guard_score: float | None = None
    confidence: float | None = None
    snr_db: float | None = None


class LegacyAnalysisResponse(BaseModel):
    snr_db: float | None = None
    noise_floor_db: float | None = None
    speech_energy_db: float | None = None
    vad_segments: list[tuple[float, float]]
    guard_status: str | None = None
    guard_result: dict[str, Any] | None = None
    spectrum: list[float]


class LegacyEvaluationResponse(BaseModel):
    wer: float | None = None
    cer: float | None = None
    latency_ms: float | None = None
    calls: int | None = None
    note: str | None = None


class LegacyProcessResponse(BaseModel):
    request_id: str
    gnani_mode: Literal["unconfigured", "live", "mock"]
    selected_candidate: Literal["C0", "C1", "C2", "C3"]
    transcription: str
    candidates: dict[str, LegacyCandidateResponse]
    analysis: LegacyAnalysisResponse
    evaluation: LegacyEvaluationResponse
    pipeline: list[str]
    latency_ms: float
    router_fitted: bool
    suppression_fitted: bool | None
    score_suppressed_reason: str | None = None


class LegacyEvaluationRow(BaseModel):
    id: str
    pipeline: str
    wer: float | None = None
    cer: float | None = None
    latency_ms: float | None = None
    candidate: str | None = None


class LegacyEvaluationResponseModel(BaseModel):
    available: bool
    reason: str | None = None
    dataset: str | None = None
    clip_count: int | None = None
    measured_at: str | None = None
    sections: dict[str, list[LegacyEvaluationRow]]


def clean(obj: Any) -> Any:
    """JSON cannot carry NaN; turn it into null."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, np.generic):
        return clean(obj.item())
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def _csv_number(value: Any) -> Optional[float]:
    """Parse a measured CSV value, preserving blank/invalid data as null."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalise_model(value: str) -> str:
    model = value.strip().lower()
    if model.startswith("prisma-"):
        model = model.removeprefix("prisma-")
    if model not in MODELS:
        raise ApiError(422, "BAD_MODEL", "model must be prisma-v2.5 or prisma-v3.0.")
    return model


def validate_language(language: str, model: str) -> None:
    if language == "indic-auto":
        if model != "v3.0":
            raise ApiError(422, "BAD_LANGUAGE", "indic-auto (automatic language detection) needs model v3.0.")
    elif language not in LANGUAGES:
        raise ApiError(422, "BAD_LANGUAGE", f"Unsupported language_code. Use one of {sorted(LANGUAGES)}.")


def score_text(reference: Optional[str], text: str) -> Optional[dict]:
    if not reference or not normalize(reference):
        return None
    s = score_utterance(reference, text)
    return {
        "wer": s.word.errors / s.word.n if s.word.n else None,
        "cer": s.char_errors / s.char_n if s.char_n else None,
        "sub": s.word.sub, "del": s.word.dele, "ins": s.word.ins, "ref_words": s.word.n,
    }


# ---------------------------------------------------------------- application
def create_app(settings: Optional[Settings] = None,
               client_factory: Optional[Callable[[], Any]] = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    router_path = cfg.config_dir / "router.json"
    suppress_path = cfg.config_dir / "suppress.json"
    router_cfg = pl.load_router_config(router_path) if router_path.exists() else {
        "fitted": False, "rules": [], "fallback_chain": ["C2", "C1", "C0"],
        "guard": {"max_lsd_db": 6.0, "max_speech_energy_change_db": 12.0}}
    suppress_cfg = json.loads(suppress_path.read_text(encoding="utf-8")) if suppress_path.exists() else pl.SUPPRESS_CONFIG
    suppress_cfg.setdefault("fitted", False)

    clips: dict[str, Clip] = {}
    jobs: dict[str, Job] = {}
    budget = {"day": "", "used": 0}
    holder: dict[str, Any] = {"client": None, "queue": None}
    live_lock = asyncio.Lock()

    def get_gnani_mode() -> str:
        return "mock" if client_factory is not None else endpoint_mode()

    def get_client():
        if holder["client"] is None:
            try:
                holder["client"] = client_factory() if client_factory else GnaniClient(cache_dir=cfg.workdir / "cache")
            except FatalAPIError as exc:
                raise ApiError(503, "GNANI_KEY_MISSING", str(exc))
        return holder["client"]

    def reserve_calls(n: int) -> None:
        today = date.today().isoformat()
        if budget["day"] != today:
            budget["day"], budget["used"] = today, 0
        if budget["used"] + n > cfg.daily_call_cap:
            raise ApiError(429, "DEMO_BUDGET_EXHAUSTED",
                           "The demo has used its daily Prisma budget. Try again tomorrow or use the saved results.")
        budget["used"] += n

    def require_token(x_demo_token: Optional[str] = Header(None)) -> None:
        if cfg.demo_token and x_demo_token != cfg.demo_token:
            raise ApiError(401, "DEMO_TOKEN_REQUIRED", "Send the demo token in the X-Demo-Token header.")

    def get_clip(clip_id: str) -> Clip:
        clip = clips.get(clip_id)
        if clip is None:
            raise ApiError(404, "CLIP_NOT_FOUND", "Unknown or expired clip. Upload it again.")
        return clip

    def ensure_variant(clip: Clip, vid: str) -> Path:
        if vid not in pl.CANDIDATES:
            reason = pl.UNAVAILABLE_REASONS.get(vid, "The requested candidate is not available.")
            raise ApiError(404, "UNKNOWN_VARIANT", f"Variant {vid} is not available. {reason}")
        path = clip.dir / f"{vid}.wav"
        if vid not in clip.variants:
            t0 = time.perf_counter()
            arr, meta = pl.apply_candidate(vid, clip.audio, suppress_cfg)
            clip.enhance_ms[vid] = round(1000 * (time.perf_counter() - t0), 1)
            clip.candidate_meta[vid] = meta
            clip.variants[vid] = arr
            path.write_bytes(pl.to_wav_bytes(arr))
        return path

    def drop_clip(clip_id: str) -> None:
        clip = clips.pop(clip_id, None)
        if clip:
            shutil.rmtree(clip.dir, ignore_errors=True)

    # -------------------------------------------------------------- job runner
    def decide_fast(clip: Clip, available: list) -> tuple:
        variant, reason = pl.route(clip.analysis, available, router_cfg)
        ensure_variant(clip, variant)
        thresholds = router_cfg.get("guard", {})
        g = pl.guard(clip.audio, clip.variants[variant], clip.candidate_meta.get(variant), thresholds)
        decision = {
            "routed_to": variant,
            "reason": reason,
            "router_fitted": bool(router_cfg["fitted"]),
            "guard": g,
        }
        if variant == "C2":
            decision["suppression_fitted"] = bool(suppress_cfg.get("fitted", False))
        if g.get("passed") is not True:
            if variant != "C0" and "C0" in available:
                decision["guard"]["action"] = "guard failed or could not be measured; fell back to the original audio"
                variant = "C0"
                ensure_variant(clip, variant)
            elif g.get("passed") is False:
                decision["guard"]["action"] = "guard failed for the only available variant"
        decision["routed_to"] = variant
        return variant, decision

    async def transcribe_one(job: Job, clip: Clip, variant: str, model: str) -> dict:
        path = ensure_variant(clip, variant)
        job.steps.append({"t": round(time.time() - job.created, 2), "msg": f"transcribing {variant} on Prisma {model}"})
        t: Transcript = await asyncio.to_thread(
            get_client().transcribe, path, job.params.language, model=model, fmt="verbatim", run_id=job.id)
        note = t.note
        if variant == "C2" and not suppress_cfg.get("fitted", False):
            note = "; ".join(part for part in (note, "C2 suppression parameters are placeholders, not fitted.") if part)
        mode = get_gnani_mode()
        model_name = f"mock-prisma-{model}" if mode == "mock" else model
        if mode == "mock":
            note = "; ".join(part for part in (note, "MOCK GNANI: this transcript is a placeholder, not Prisma output.") if part)
        return {"key": f"{variant}@{model_name}", "variant": variant, "model": model_name, "text": t.text, "ok": t.ok,
                "error": t.error, "note": note, "cached": t.cached, "api_latency_s": t.end_to_end_latency,
                "wall_s": t.wall_seconds, "detected_language_code": t.detected_language_code,
                "score": score_text(job.params.reference or clip.reference, t.text) if t.ok and mode == "live" else None,
                "score_suppressed_reason": "mock" if mode == "mock" else None,
                "parameters_fitted": suppress_cfg.get("fitted", False) if variant == "C2" else None}

    async def run_job(job: Job) -> None:
        p = job.params
        job.status = "running"
        clip = get_clip(p.clip_id)
        t_start = time.perf_counter()
        available = [v for v in (p.candidate_ids or list(pl.CANDIDATES)) if v in pl.CANDIDATES]
        if "C0" not in available:
            available.insert(0, "C0")
        other = "v3.0" if p.model == "v2.5" else "v2.5"
        decision: dict = {}
        if p.mode == "raw":
            plan, routed = [("C0", p.model)], "C0"
        elif p.mode == "fast":
            routed, decision = decide_fast(clip, available)
            plan = [(routed, p.model)]
        elif p.mode == "compare_all":
            routed, decision = decide_fast(clip, available)
            plan = [(v, p.model) for v in available]
        else:  # accuracy
            routed = "C0"
            plan = [(v, m) for m in (p.model, other) for v in available]

        hyps = [await transcribe_one(job, clip, v, m) for v, m in plan]
        ok = [h for h in hyps if h["ok"]]
        if not ok:
            raise ApiError(502, "ASR_FAILED", "; ".join(h["error"] for h in hyps if h["error"]) or "No transcript returned.")

        if p.mode == "accuracy":
            if len(ok) < 3:
                raise ApiError(502, "NOT_ENOUGH_HYPOTHESES", "Accuracy mode needs at least three successful transcripts.")
            picked = pl.consensus_pick({h["key"]: h["text"] for h in ok}, [h["key"] for h in ok])
            chosen = next(h for h in ok if h["key"] == picked["chosen"])
            decision = {"method": "minimum-Bayes-risk consensus over word edit distance",
                        "chosen": picked["chosen"], "total_distance": picked["total_distance"],
                        "pairwise_distance": picked["pairwise_distance"],
                        "tie_break": "earliest candidate, i.e. the original audio on the requested model"}
        else:
            chosen = next((h for h in ok if h["variant"] == routed and h["model"] == p.model), ok[0])

        job.result = clean({
            "mode": p.mode, "language": p.language,
            "model": f"mock-prisma-{p.model}" if get_gnani_mode() == "mock" else p.model,
            "gnani_mode": get_gnani_mode(),
            "chosen": {"key": chosen["key"], "variant": chosen["variant"], "model": chosen["model"],
                       "text": chosen["text"], "score": chosen["score"]},
            "hypotheses": hyps, "decision": decision,
            "score_suppressed_reason": "mock" if get_gnani_mode() == "mock" else None,
            "suppression_fitted": bool(suppress_cfg.get("fitted", False)) if any(h["variant"] == "C2" for h in hyps) else None,
            "api_calls": sum(1 for h in hyps if not h["cached"]),
            "timing_ms": {
                "analysis_at_upload": clip.analysis.get("analysis_ms"),
                "enhance": dict(clip.enhance_ms),
                "api_wall": round(1000 * sum(h["wall_s"] or 0 for h in hyps), 1),
                "job_total": round(1000 * (time.perf_counter() - t_start), 1),
            },
            "router_note": None if router_cfg["fitted"] else "Router thresholds are placeholders, not fitted on data.",
        })
        job.status = "done"

    async def worker() -> None:
        queue = holder["queue"]
        while True:
            job = await queue.get()
            try:
                await run_job(job)
            except ApiError as exc:
                job.status, job.error = "error", {"code": exc.code, "message": exc.message}
            except FatalAPIError as exc:
                key_missing = "not configured" in str(exc).lower()
                job.status, job.error = "error", {
                    "code": "GNANI_KEY_MISSING" if key_missing else "GNANI_FATAL",
                    "message": "GNANI_API_KEY is not configured." if key_missing else str(exc),
                }
            except Exception as exc:  # keep the worker alive whatever happens
                if type(exc).__name__ == "FatalAPIError":
                    missing_key = "not configured" in str(exc).lower()
                    job.status, job.error = "error", {
                        "code": "GNANI_KEY_MISSING" if missing_key else "GNANI_FATAL",
                        "message": "GNANI_API_KEY is not configured." if missing_key else str(exc),
                    }
                else:
                    job.status, job.error = "error", {
                        "code": "INTERNAL",
                        "message": f"The transcription worker encountered an internal {type(exc).__name__} error.",
                    }
            finally:
                if job.done_event is not None:
                    job.done_event.set()
                queue.task_done()

    async def sweeper() -> None:
        while True:
            await asyncio.sleep(60)
            cutoff = time.time() - cfg.ttl_minutes * 60
            for cid in [c for c, v in clips.items() if v.created < cutoff]:
                drop_clip(cid)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        holder["queue"] = asyncio.Queue()
        tasks = [asyncio.create_task(worker()), asyncio.create_task(sweeper())]
        yield
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="BharatNoise API", version="0.1", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        issues = exc.errors()
        message = "Request fields are invalid."
        if issues:
            location = ".".join(str(part) for part in issues[0].get("loc", ()) if part != "body")
            message = f"Invalid request field: {location or 'body'}."
        return JSONResponse(status_code=422, content={"error": {"code": "BAD_REQUEST", "message": message}})

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException):
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        message = str(exc.detail) if isinstance(exc.detail, str) else "The request could not be completed."
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": message}})

    # ------------------------------------------------------------------ meta
    @app.get("/health", response_model=LegacyHealthResponse)
    @app.get("/api/health", response_model=ApiHealthResponse)
    def health():
        """Liveness plus whether the server can reach Gnani. Never returns secrets."""
        mode = get_gnani_mode()
        configured = mode != "unconfigured"
        return {"ok": True, "status": "ok", "gnani_configured": configured,
                "gnani_key_configured": configured, "gnani_mode": mode, "version": "0.3.1",
                "clips_in_memory": len(clips), "jobs_in_memory": len(jobs)}

    @app.get("/api/config")
    def config():
        """Everything the front end needs to build its controls."""
        neural_available, neural_reason = pl.initialize_neural()
        silero_available, silero_reason = pl.vad.silero_availability()
        today = date.today().isoformat()
        used = budget["used"] if budget["day"] == today else 0
        candidates = [
            {"id": candidate.id, "label": candidate.label, "available": True, "reason": None}
            for candidate in pl.CANDIDATES.values()
        ]
        if not neural_available:
            candidates.append({"id": "C3", "label": pl.PLANNED["C3"], "available": False,
                              "reason": neural_reason or pl.UNAVAILABLE_REASONS.get("C3")})
        return {
            "gnani_mode": get_gnani_mode(),
            "languages": [{"code": c, "name": n} for c, n in LANGUAGES.items()],
            "models": list(MODELS),
            "modes": [{"id": k, "label": v} for k, v in MODES.items()],
            "candidates": candidates,
            "limits": {"max_seconds": pl.MAX_SECONDS, "max_upload_mb": cfg.max_upload_mb, "sample_rate": pl.SR},
            "router": {"fitted": bool(router_cfg["fitted"])},
            "suppression": {"fitted": bool(suppress_cfg.get("fitted", False))},
            "optional_features": {
                "silero_vad": {"available": silero_available, "reason": silero_reason,
                               "install_hint": "python -m pip install silero-vad"},
                "deepfilternet": {"available": neural_available, "reason": neural_reason,
                                  "install_hint": "python -m pip install deepfilternet"},
            },
            "live": {"variants": ["C0", "C1"], "sample_rate": pl.SR, "frame_bytes": FRAME_BYTES,
                     "frame_ms": int(FRAME_SECONDS * 1000), "max_seconds": cfg.live_max_s},
            "demo_token_required": bool(cfg.demo_token),
            "budget": {"daily_cap": cfg.daily_call_cap, "remaining": max(0, cfg.daily_call_cap - used)},
            "remaining_daily_budget": max(0, cfg.daily_call_cap - used),
        }

    @app.get("/api/samples")
    def samples():
        """Bundled demo clips (samples/index.json). Use their id with POST /api/clips."""
        index = cfg.samples_dir / "index.json"
        if not index.exists():
            return []
        entries = json.loads(index.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ApiError(500, "SAMPLE_INDEX_INVALID", "samples/index.json must contain a list.")
        return [{k: v for k, v in entry.items() if k != "file" and k != "path"}
                for entry in entries if isinstance(entry, dict)]

    # ----------------------------------------------------------------- clips
    @app.post("/api/clips", status_code=201, dependencies=[Depends(require_token)])
    async def create_clip(file: Optional[UploadFile] = File(None), sample_id: Optional[str] = Form(None),
                          language: Optional[str] = Form(None), reference: Optional[str] = Form(None)):
        """Upload audio (or pick a bundled sample). Decodes, converts to 8 kHz mono, analyses, stores briefly."""
        pl.initialize_neural()
        if (file is None) == (sample_id is None):
            raise ApiError(422, "BAD_REQUEST", "Send exactly one of: file, sample_id.")
        if file is not None:
            limit = int(cfg.max_upload_mb * 1024 * 1024)
            data = await file.read(limit + 1)
            if len(data) > limit:
                raise ApiError(413, "UPLOAD_TOO_LARGE", f"Upload exceeds {cfg.max_upload_mb:g} MB.")
            source = Path((file.filename or "upload").replace("\\", "/")).name or "upload"
        else:
            index = cfg.samples_dir / "index.json"
            entries = json.loads(index.read_text(encoding="utf-8")) if index.exists() else []
            entry = next((s for s in entries if s.get("id") == sample_id), None)
            if entry is None:
                raise ApiError(404, "SAMPLE_NOT_FOUND", "Unknown sample_id.")
            sample_path = (cfg.samples_dir / str(entry.get("file", ""))).resolve()
            if cfg.samples_dir.resolve() not in sample_path.parents:
                raise ApiError(404, "SAMPLE_NOT_FOUND", "Unknown sample_id.")
            try:
                data = sample_path.read_bytes()
            except OSError as exc:
                raise ApiError(404, "SAMPLE_NOT_FOUND", "The bundled sample audio is unavailable.") from exc
            source, language, reference = f"sample:{sample_id}", language or entry.get("language"), reference or entry.get("reference")
        if language is not None and language != "indic-auto" and language not in LANGUAGES:
            raise ApiError(422, "BAD_LANGUAGE", f"Unsupported language_code. Use one of {sorted(LANGUAGES)}.")
        try:
            audio = await asyncio.to_thread(pl.load_audio, data)
        except pl.AudioError as exc:
            raise ApiError(422, "BAD_AUDIO", str(exc))
        t0 = time.perf_counter()
        analysis = pl.analyze(audio)
        analysis["analysis_ms"] = round(1000 * (time.perf_counter() - t0), 1)
        clip_id = uuid.uuid4().hex[:12]
        cdir = cfg.workdir / clip_id
        cdir.mkdir(parents=True, exist_ok=True)
        clip = Clip(clip_id, time.time(), audio, analysis, cdir, source, language, reference)
        ensure_variant(clip, "C0")
        clips[clip_id] = clip
        return {"clip_id": clip_id, "source": source, "language": language, "analysis": analysis,
                "variants": list(pl.CANDIDATES), "expires_in_minutes": cfg.ttl_minutes}

    @app.get("/api/clips/{clip_id}")
    def clip_info(clip_id: str):
        """Metadata and analysis (estimated SNR, speech segments, clipping) for an uploaded clip."""
        c = get_clip(clip_id)
        return {"clip_id": c.id, "source": c.source, "language": c.language, "reference": c.reference,
                "analysis": c.analysis, "variants": list(pl.CANDIDATES), "enhance_ms": c.enhance_ms,
                "candidate_parameters": c.candidate_meta}

    @app.get("/api/clips/{clip_id}/audio")
    def clip_audio(clip_id: str, variant: str = Query("C0")):
        """WAV for playback: the original (C0) or a processed version."""
        c = get_clip(clip_id)
        path = ensure_variant(c, variant)
        headers = {"X-BN-Suppression-Fitted": str(bool(suppress_cfg.get("fitted", False))).lower()} if variant == "C2" else {}
        return FileResponse(path, media_type="audio/wav", headers=headers)

    @app.get("/api/clips/{clip_id}/spectrogram.png")
    async def clip_spectrogram(clip_id: str, variant: str = Query("C0")):
        """Server-rendered spectrogram (0-4 kHz) so the page can show before and after."""
        c = get_clip(clip_id)
        ensure_variant(c, variant)
        png = await asyncio.to_thread(pl.spectrogram_png, c.variants[variant])
        headers = {"X-BN-Suppression-Fitted": str(bool(suppress_cfg.get("fitted", False))).lower()} if variant == "C2" else {}
        return Response(png, media_type="image/png", headers=headers)

    @app.delete("/api/clips/{clip_id}", status_code=204)
    def delete_clip(clip_id: str):
        """Remove a clip and its derived audio immediately."""
        get_clip(clip_id)
        drop_clip(clip_id)
        return Response(status_code=204)

    # ------------------------------------------------------------------ jobs
    @app.post("/api/jobs", status_code=202, dependencies=[Depends(require_token)])
    async def create_job(req: JobRequest):
        """Queue a transcription run. Poll GET /api/jobs/{id}. Runs one at a time because Gnani allows one active request."""
        pl.initialize_neural()
        get_clip(req.clip_id)
        validate_language(req.language, req.model)
        if req.mode == "accuracy" and req.language == "indic-auto":
            raise ApiError(422, "BAD_LANGUAGE", "Accuracy mode needs a specific language (it also calls Prisma v2.5).")
        requested = req.candidate_ids or list(pl.CANDIDATES)
        unknown = set(requested) - set(pl.CANDIDATES)
        if unknown:
            raise ApiError(422, "UNKNOWN_VARIANT", f"Unavailable candidate(s): {', '.join(sorted(unknown))}.")
        n = len(set(requested) | {"C0"})
        reserve_calls({"raw": 1, "fast": 1, "compare_all": n, "accuracy": 2 * n}[req.mode])
        job = Job(uuid.uuid4().hex[:12], req, done_event=asyncio.Event())
        jobs[job.id] = job
        for old in list(jobs)[:-200]:
            jobs.pop(old, None)
        await holder["queue"].put(job)
        return {"job_id": job.id, "status": job.status}

    @app.post("/process", response_model=LegacyProcessResponse, dependencies=[Depends(require_token)])
    async def frontend_process(
        file: UploadFile = File(...),
        mode: str = Form("fast"),
        sample_rate: int = Form(8000),
        channels: int = Form(1),
        sample_format: str = Form("pcm_s16le"),
        asr_provider: str = Form("gnani_prisma"),
        asr_model: str = Form("prisma-v2.5"),
        candidates: str = Form("C0,C1,C2"),
        selection: str = Form("snr_router"),
        compare_models: bool = Form(False),
        reference: Optional[str] = Form(None),
    ):
        """Process one upload using the browser workstation's multipart wire contract."""
        if sample_rate != 8000 or channels != 1 or sample_format != "pcm_s16le":
            raise ApiError(422, "BAD_AUDIO_FORMAT", "Expected 8 kHz mono PCM16 little-endian audio.")
        if asr_provider != "gnani_prisma":
            raise ApiError(422, "BAD_PROVIDER", "Only gnani_prisma is supported.")
        if mode not in {"fast", "max"}:
            raise ApiError(422, "BAD_MODE", "mode must be fast or max.")
        if selection not in {"snr_router", "consensus"}:
            raise ApiError(422, "BAD_SELECTION", "selection must be snr_router or consensus.")
        model = _normalise_model(asr_model)
        requested = [part.strip().upper() for part in candidates.split(",") if part.strip()]
        invalid = set(requested) - {"C0", "C1", "C2", "C3"}
        if invalid:
            raise ApiError(422, "UNKNOWN_VARIANT", f"Unknown candidate(s): {', '.join(sorted(invalid))}.")
        requested = list(dict.fromkeys(requested))
        pl.initialize_neural()
        usable = [candidate for candidate in requested if candidate in pl.CANDIDATES]
        if "C0" not in usable:
            usable.insert(0, "C0")
        if mode == "fast" and selection != "snr_router":
            raise ApiError(422, "BAD_SELECTION", "fast mode requires snr_router selection.")
        if mode == "max" and selection != "consensus":
            raise ApiError(422, "BAD_SELECTION", "max mode requires consensus selection.")

        created = await create_clip(file=file, sample_id=None, language=None, reference=reference)
        job_mode = "fast" if mode == "fast" else ("accuracy" if compare_models else "compare_all")
        request = JobRequest(
            clip_id=created["clip_id"], mode=job_mode, language="hi-IN", model=model,
            reference=reference, candidate_ids=usable,
        )
        queued = await create_job(request)
        job = jobs[queued["job_id"]]
        if job.done_event is not None:
            await job.done_event.wait()
        if job.status != "done" or job.result is None:
            error = job.error or {"code": "ASR_FAILED", "message": "The transcription job failed."}
            status = 401 if error["code"] == "GNANI_KEY_MISSING" else 502
            raise ApiError(status, error["code"], error["message"])

        result = job.result
        requested_model_name = f"mock-prisma-{model}" if result["gnani_mode"] == "mock" else model
        requested_hypotheses = [
            hyp for hyp in result["hypotheses"] if hyp["model"] == requested_model_name and hyp["ok"]
        ]
        if mode == "max" and not compare_models:
            if len(requested_hypotheses) < 3:
                raise ApiError(502, "NOT_ENOUGH_HYPOTHESES", "Consensus needs at least three successful candidate transcripts.")
            consensus = pl.consensus_pick(
                {hyp["key"]: hyp["text"] for hyp in requested_hypotheses},
                [hyp["key"] for hyp in requested_hypotheses],
            )
            chosen = next(hyp for hyp in requested_hypotheses if hyp["key"] == consensus["chosen"])
        else:
            chosen = next(
                (hyp for hyp in requested_hypotheses if hyp["variant"] == result["chosen"]["variant"]),
                requested_hypotheses[0] if requested_hypotheses else result["hypotheses"][0],
            )

        clip = get_clip(created["clip_id"])
        candidate_results: dict[str, dict] = {}
        for hyp in requested_hypotheses:
            candidate = hyp["variant"]
            if candidate in candidate_results:
                continue
            audio = clip.variants.get(candidate)
            peaks = []
            if audio is not None:
                peaks = [float(np.max(np.abs(part))) for part in np.array_split(audio, min(320, max(1, len(audio)))) if len(part)]
            candidate_results[candidate] = {
                "transcript": hyp["text"],
                "audio_url": f"/api/clips/{clip.id}/audio?variant={candidate}",
                "peaks": peaks,
            }
        selected_guard = (
            pl.guard(clip.audio, clip.variants[chosen["variant"]],
                     clip.candidate_meta.get(chosen["variant"]), router_cfg.get("guard", {}))
        )
        guard_status = "passed" if selected_guard.get("passed") is True else (
            "failed" if selected_guard.get("passed") is False else "not measured"
        )
        analysis = {
            **({"snr_db": clip.analysis["snr_db_est"]} if clip.analysis["snr_db_est"] is not None else {}),
            "noise_floor_db": clip.analysis["noise_floor_db"],
            **({"speech_energy_db": clip.analysis["speech_energy_db"]} if clip.analysis["speech_energy_db"] is not None else {}),
            "vad_segments": clip.analysis["segments"],
            "guard_status": guard_status,
            "guard_result": selected_guard,
            "spectrum": clip.analysis["spectrum"],
        }
        score = chosen.get("score")
        response = {
            "request_id": job.id,
            "gnani_mode": result["gnani_mode"],
            "selected_candidate": chosen["variant"],
            "transcription": chosen["text"],
            "candidates": candidate_results,
            "analysis": analysis,
            "evaluation": {
                **({"wer": score["wer"], "cer": score["cer"]} if score else {}),
                "latency_ms": result["timing_ms"]["api_wall"],
                "calls": result["api_calls"],
                "note": "Mock scores are suppressed." if result["score_suppressed_reason"] else (
                    "WER/CER computed against the supplied reference." if score else "No reference supplied; WER/CER were not measured."
                ),
            },
            "pipeline": ["ingest", "vad_snr", "candidates", "guard", f"route:{chosen['variant']}", "transcribe"],
            "latency_ms": result["timing_ms"]["job_total"],
            "router_fitted": router_cfg["fitted"],
            "score_suppressed_reason": result["score_suppressed_reason"],
            "suppression_fitted": suppress_cfg.get("fitted", False) if any(
                hyp["variant"] == "C2" for hyp in result["hypotheses"]
            ) else None,
        }
        return clean(response)

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        """queued | running | done | error, with progress steps and the final result."""
        job = jobs.get(job_id)
        if job is None:
            raise ApiError(404, "JOB_NOT_FOUND", "Unknown job.")
        return {"job_id": job.id, "status": job.status, "steps": job.steps, "result": job.result,
                "error": job.error, "gnani_mode": get_gnani_mode()}

    # --------------------------------------------------------------- scoring
    @app.post("/api/score", dependencies=[Depends(require_token)])
    def score(req: ScoreRequest):
        """Score a hypothesis with the exact normalisation used in the benchmark, with word-level ops for highlighting."""
        ref, hyp = normalize(req.reference).split(), normalize(req.hypothesis).split()
        return clean({"reference_normalized": " ".join(ref), "hypothesis_normalized": " ".join(hyp),
                      "score": score_text(req.reference, req.hypothesis), "ops": pl.align_ops(ref, hyp)})

    # --------------------------------------------------------------- results
    def results_file(tag: str) -> Path:
        if not TAG_RE.match(tag):
            raise ApiError(422, "BAD_TAG", "Tags may contain letters, digits, underscore, dot and hyphen only.")
        path = cfg.results_dir / f"{tag}.csv"
        if not path.exists():
            raise ApiError(404, "RESULTS_NOT_FOUND", f"No results file for tag {tag}.")
        return path

    @app.get("/api/results")
    def results_list():
        """Results CSVs written by run_baseline.py. The dashboard reads these; nothing is hard-coded."""
        if not cfg.results_dir.exists():
            return []
        return [{"tag": p.stem, "rows": max(0, sum(1 for _ in p.open(encoding="utf-8")) - 1)}
                for p in sorted(cfg.results_dir.glob("*.csv"))]

    @app.get("/api/results/tables")
    def result_tables():
        """Return aggregate CSVs as rows; never synthesize tables when benchmarks are absent."""
        names = {
            "final": "final_results.csv", "by_noise": "by_noise.csv", "by_snr": "by_snr.csv",
            "by_language": "by_language.csv", "ablation": "ablation_results.csv", "latency": "latency.csv",
        }
        missing = [filename for filename in names.values() if not (cfg.results_dir / filename).is_file()]
        if missing:
            raise ApiError(404, "AGGREGATES_NOT_FOUND",
                           f"Aggregate results have not been generated yet: {', '.join(missing)}.")
        tables = {}
        for key, filename in names.items():
            with (cfg.results_dir / filename).open(newline="", encoding="utf-8") as handle:
                tables[key] = list(csv.DictReader(handle))
        return tables

    @app.get("/api/results/compare")
    def results_compare(a: str, b: str, by: Optional[Literal["group", "language"]] = None,
                        n_boot: int = Query(2000, ge=200, le=5000)):
        """Paired comparison of two results files: WER/CER, bootstrap 95% CI, win/tie/loss. Delta is B minus A."""
        pairs, info = cmp.paired_rows(cmp.load(results_file(a)), cmp.load(results_file(b)))
        if not pairs:
            raise ApiError(422, "NO_COMPARABLE_CLIPS", f"No clips in common without errors ({info}).")
        out: dict = {"a": a, "b": b, "skipped": info, "overall": cmp.summarise(pairs, n_boot, 0)}
        if by:
            groups: dict = {}
            for ra, rb in pairs:
                groups.setdefault(ra.get(by) or "(none)", []).append((ra, rb))
            out["by"] = {k: cmp.summarise(v, n_boot, 0) for k, v in sorted(groups.items())}
        return clean(out)

    @app.get("/evaluation", response_model=LegacyEvaluationResponseModel)
    def frontend_evaluation():
        """Expose measured aggregate CSV rows in the workstation's evaluation contract."""
        final_path = cfg.results_dir / "final_results.csv"
        if not final_path.is_file():
            return {
                "available": False,
                "reason": "No benchmark results have been generated yet.",
                "dataset": None,
                "clip_count": None,
                "measured_at": None,
                "sections": {"comparison": [], "ablation": [], "snr": [], "noise": []},
            }

        def ui_rows(path: Path, default_pipeline: str) -> list[dict]:
            if not path.is_file():
                return []
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            formatted = []
            for index, row in enumerate(rows):
                formatted.append({
                    "id": str(row.get("id") or row.get("group") or index + 1),
                    "pipeline": str(row.get("pipeline") or row.get("candidate") or default_pipeline),
                    "candidate": row.get("candidate"),
                    **({"wer": _csv_number(row["wer"])} if _csv_number(row.get("wer")) is not None else {}),
                    **({"cer": _csv_number(row["cer"])} if _csv_number(row.get("cer")) is not None else {}),
                    **({"latency_ms": _csv_number(row["latency_ms"])} if _csv_number(row.get("latency_ms")) is not None else {}),
                })
            return formatted

        final_rows = ui_rows(final_path, "Measured benchmark")
        measured_at = time.strftime("%Y-%m-%d", time.gmtime(final_path.stat().st_mtime))
        return {
            "available": True,
            "reason": None,
            "clip_count": len(final_rows),
            "dataset": None,
            "measured_at": measured_at,
            "sections": {
                "comparison": final_rows,
                "ablation": ui_rows(cfg.results_dir / "ablation_results.csv", "Measured ablation"),
                "snr": ui_rows(cfg.results_dir / "by_snr.csv", "Measured SNR group"),
                "noise": ui_rows(cfg.results_dir / "by_noise.csv", "Measured noise group"),
            },
        }

    @app.get("/api/results/{tag}/rows")
    def results_rows(tag: str, group: Optional[str] = None, limit: int = Query(200, ge=1, le=1000)):
        """Per-clip rows (reference, hypothesis, latency) for the experiment explorer."""
        with results_file(tag).open(newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if group is None or r.get("group") == group]
        return rows[:limit]

    # ------------------------------------------------------------------ live
    @app.websocket("/live")
    @app.websocket("/ws/live")
    async def live(ws: WebSocket, language: str = "hi-IN", model: str = "v2.5", variant: str = "C0",
                   vad_threshold: float = 0.7, min_speech_ms: int = 200, min_silence_ms: int = 500,
                   token: Optional[str] = None, sample_rate: int = 8000,
                   vad_preset: str = "telephony"):
        """Live microphone proxy. Browser sends 8 kHz mono PCM16 binary chunks (any size) and JSON text
        frames {"type":"stop"} or {"type":"commit"}. Server re-frames to exact 1,024-byte frames at 64 ms
        cadence, applies C1's band-limit if variant=C1, and relays Gnani's JSON messages back unchanged."""
        await ws.accept()

        async def refuse(message: str, code: int = 1008) -> None:
            await ws.send_json({"type": "error", "message": message})
            await ws.close(code=code)

        key = os.environ.get("GNANI_API_KEY", "")
        if cfg.demo_token and token != cfg.demo_token:
            return await refuse("Demo token required (?token=...).")
        if not key:
            return await refuse("Server has no GNANI_API_KEY configured.", 1011)
        if model not in MODELS or variant not in ("C0", "C1"):
            return await refuse("model must be v2.5 or v3.0 and variant C0 or C1.")
        if sample_rate != pl.SR or vad_preset != "telephony":
            return await refuse("sample_rate must be 8000 and vad_preset must be telephony.")
        if not (0 <= vad_threshold <= 1 and 0 <= min_speech_ms <= 5000 and 0 <= min_silence_ms <= 10000):
            return await refuse("VAD parameters are outside the supported range.")
        try:
            validate_language(language, model)
        except ApiError as exc:
            return await refuse(exc.message)
        if live_lock.locked():
            return await refuse("Live demo is busy with another session. Try again in a moment.", 1013)

        async with live_lock:
            headers = {"x-api-key-id": key, "language_code": language, "x-sample-rate": str(pl.SR),
                       "model": "prisma-v3.0" if model == "v3.0" else "prisma-v2.5",
                       "x-vad-threshold": str(vad_threshold), "x-min-speech-ms": str(min_speech_ms),
                       "x-min-silence-ms": str(min_silence_ms)}
            try:
                async with ws_connect(cfg.ws_url, additional_headers=headers, open_timeout=10) as up:
                    await relay(ws, up, variant)
            except Exception as exc:
                try:
                    await ws.send_json({"type": "error", "message": f"Could not reach Gnani streaming: {type(exc).__name__}"})
                except Exception:
                    pass
            try:
                await ws.close()
            except Exception:
                pass

    async def relay(client_ws: WebSocket, up, variant: str) -> None:
        buf, stopping = bytearray(), asyncio.Event()
        band = pl.StreamBandpass() if variant == "C1" else None
        silence = bytes(FRAME_BYTES)

        async def from_browser() -> None:
            try:
                while True:
                    msg = await client_ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    if msg.get("bytes") is not None:
                        buf.extend(band.process(msg["bytes"]) if band else msg["bytes"])
                    elif msg.get("text") is not None:
                        try:
                            kind = json.loads(msg["text"]).get("type")
                        except ValueError:
                            continue
                        if kind == "commit":
                            await up.send(json.dumps({"type": "commit"}))
                        elif kind == "stop":
                            stopping.set()
            finally:
                stopping.set()

        async def pacer() -> None:
            # Gnani requires exactly 1,024 bytes every 64 ms; when the browser is late we send silence.
            next_t, tail_left = time.monotonic(), int(1.0 / FRAME_SECONDS) + 1
            while True:
                if len(buf) >= FRAME_BYTES:
                    frame = bytes(buf[:FRAME_BYTES])
                    del buf[:FRAME_BYTES]
                elif stopping.is_set():
                    if buf:
                        frame = bytes(buf) + bytes(FRAME_BYTES - len(buf))
                        buf.clear()
                    elif tail_left > 0:  # about a second of silence so the last sentence is closed
                        frame, tail_left = silence, tail_left - 1
                    else:
                        await up.send(json.dumps({"type": "commit"}))
                        await asyncio.sleep(cfg.live_stop_grace_s)
                        return
                else:
                    frame = silence
                await up.send(frame)
                next_t += FRAME_SECONDS
                delay = next_t - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    next_t = time.monotonic()

        async def to_browser() -> None:
            async for raw in up:
                await client_ws.send_text(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))

        tasks = [asyncio.create_task(c()) for c in (from_browser, pacer, to_browser)]
        try:
            await asyncio.wait(tasks, timeout=cfg.live_max_s, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    if cfg.web_dir.exists():  # optional: serve the front end from the same process
        app.mount("/", StaticFiles(directory=str(cfg.web_dir), html=True), name="web")
    return app


app = create_app()
