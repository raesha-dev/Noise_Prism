import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  AudioLines,
  BarChart3,
  FileAudio,
  History,
  Home,
  Layers3,
  Moon,
  Radio,
  Settings2,
  SlidersHorizontal,
  Sun,
  Trophy,
  ChevronRight,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  TRACK_IDS,
  checkApiConnection,
  fetchCandidateConfig,
  getApiBaseUrl,
  processAudioFile,
  saveApiBaseUrl,
  type AccuracyMode,
  type EvaluationReport,
  type HealthInfo,
  type LanguageCode,
  type OutputFormat,
  type ProcessingResult,
  type TrackId,
} from "@/lib/bharatnoise-api";
import {
  computePeaks,
  decodeToMono,
  encodeWav,
  makeWavFile,
  makeSampleClip,
  SAMPLE_CLIPS,
  type SampleKind,
} from "@/lib/audio-utils";
import { loadHistory, saveHistory, type HistoryEntry } from "@/lib/history";
import { startLiveStream, type LiveSession } from "@/lib/live-stream";
import { AudioStudio, type TrackView } from "./AudioStudio";
import { PipelinePanel } from "./PipelinePanel";
import { AnalysisPanel, type AnalysisView } from "./AnalysisPanel";
import { TranscriptPanel, type TranscriptView } from "./TranscriptPanel";
import { EvaluationPanel } from "./EvaluationPanel";
import { LivePanel, type LiveView } from "./LivePanel";
import { HistoryPanel } from "./HistoryPanel";
import { SettingsDialog } from "./SettingsDialog";
import { ASR_MODELS, downloadBlob, trackName } from "./workstation-utils";

type Clip = {
  file: File;
  url: string;
  name: string;
  source: "upload" | "recording" | "sample";
  peaks: number[] | null;
  duration: number;
  sampleRate?: number | undefined;
  channels?: number | undefined;
};

type Section =
  "studio" | "pipeline" | "analysis" | "transcript" | "evaluation" | "live" | "history";
const SECTIONS: { id: Section; label: string; icon: ReactNode }[] = [
  { id: "studio", label: "Audio Studio", icon: <Home /> },
  { id: "pipeline", label: "Enhancement Pipeline", icon: <Layers3 /> },
  { id: "analysis", label: "Speech Analysis", icon: <BarChart3 /> },
  { id: "transcript", label: "Transcript Studio", icon: <FileAudio /> },
  { id: "evaluation", label: "Evaluation Lab", icon: <Trophy /> },
  { id: "live", label: "Live Monitor", icon: <Radio /> },
  { id: "history", label: "Experiment History", icon: <History /> },
];

const MAX_BYTES = 80 * 1024 * 1024;
const AUDIO_EXT = /\.(wav|mp3|ogg|oga|m4a|aac|flac|webm|opus)$/i;

function segmentsToBins(segments: [number, number][], duration: number, bins = 48): boolean[] {
  return Array.from({ length: bins }, (_, i) => {
    const mid = ((i + 0.5) / bins) * duration;
    return segments.some(([start, end]) => mid >= start && mid <= end);
  });
}

export function Workstation() {
  const [dark, setDark] = useState(true);
  const [apiUrl, setApiUrl] = useState(getApiBaseUrl);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [healthError, setHealthError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [asrModel, setAsrModel] = useState(ASR_MODELS[0]!.value);
  const [languageCode, setLanguageCode] = useState<LanguageCode>("hi-IN");
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("auto");
  const [mode, setMode] = useState<AccuracyMode>("fast");
  const [includeC3, setIncludeC3] = useState(true);
  const [includeResearchCandidates, setIncludeResearchCandidates] = useState(false);
  const [oaLambda, setOaLambda] = useState(0.1);
  const [oaSweep, setOaSweep] = useState<number[]>([]);
  const [imcraFitted, setImcraFitted] = useState<boolean | null>(null);
  const [imcraConfigError, setImcraConfigError] = useState("");
  const [compareModels, setCompareModels] = useState(false);
  const [clip, setClip] = useState<Clip | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [selected, setSelected] = useState<TrackId>("C2");
  const [checked, setChecked] = useState<TrackId[]>(["C0", "C2"]);
  const [result, setResult] = useState<ProcessingResult | null>(null);
  const [resultSource, setResultSource] = useState<"service" | null>(null);
  const [routed, setRouted] = useState<TrackId | null>(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState(-1);
  const [candidatePeaks, setCandidatePeaks] = useState<Partial<Record<TrackId, number[]>>>({});
  const [reference, setReference] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [reportSource, setReportSource] = useState<"service" | "imported" | null>(null);
  const [analysisTab, setAnalysisTab] = useState<"vad" | "spectrum" | "floor">("vad");
  const [activeSection, setActiveSection] = useState<Section>("studio");
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [mediaDuration, setMediaDuration] = useState(0);
  const [volume, setVolume] = useState(0.8);
  const [rate, setRate] = useState(1);
  const [recording, setRecording] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const [live, setLive] = useState<LiveView>({
    state: "ready",
    levelDb: -120,
    finals: [],
    partial: "",
  });

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const clipUrlRef = useRef("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const liveSessionRef = useRef<LiveSession | null>(null);

  // ---------- Theme, saved settings, history ----------
  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    const saved = getApiBaseUrl();
    setApiUrl(saved);
    setHistory(loadHistory());
    if (saved) {
      checkApiConnection(saved)
        .then(setHealth)
        .catch((error: unknown) =>
          setHealthError(error instanceof Error ? error.message : "Service not reachable."),
        );
      fetchCandidateConfig(saved)
        .then((config) => {
          setOaSweep(config.oaSweep);
          setOaLambda(config.oaLambda);
          setImcraFitted(config.fitted);
          setImcraConfigError("");
        })
        .catch((error: unknown) => {
          setOaSweep([]);
          setImcraFitted(null);
          setImcraConfigError(
            error instanceof Error
              ? `Experimental candidates unavailable: ${error.message}`
              : "Could not load candidate settings.",
          );
        });
    }
  }, []);

  function toggleTheme() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      window.localStorage.setItem("bharatnoise.theme", next ? "dark" : "light");
    } catch {
      /* ignore */
    }
  }

  // ---------- Cleanup on unmount ----------
  useEffect(
    () => () => {
      if (clipUrlRef.current) URL.revokeObjectURL(clipUrlRef.current);
      abortRef.current?.abort();
      liveSessionRef.current?.stop();
      if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    },
    [],
  );

  // ---------- Scroll spy ----------
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (visible) setActiveSection(visible.target.id.replace("section-", "") as Section);
      },
      { rootMargin: "-80px 0px -45% 0px", threshold: [0.1, 0.4, 0.8] },
    );
    SECTIONS.forEach((section) => {
      const el = document.getElementById(`section-${section.id}`);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  function goTo(section: Section) {
    setActiveSection(section);
    document
      .getElementById(`section-${section}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------- Clip loading ----------
  const loadClip = useCallback(async (file: File, source: Clip["source"]) => {
    if (!file.type.startsWith("audio/") && !AUDIO_EXT.test(file.name)) {
      toast.error("Choose an audio file (WAV, MP3, OGG, M4A, FLAC or WebM).");
      return;
    }
    if (file.size > MAX_BYTES) {
      toast.error("Audio files must be 80 MB or smaller.");
      return;
    }
    abortRef.current?.abort();
    audioRef.current?.pause();
    if (clipUrlRef.current) URL.revokeObjectURL(clipUrlRef.current);
    const url = URL.createObjectURL(file);
    clipUrlRef.current = url;
    setClip({ file, url, name: file.name, source, peaks: null, duration: 0 });
    setResult(null);
    setResultSource(null);
    setRouted(null);
    setStage(-1);
    setCandidatePeaks({});
    setSelected("C0");
    setChecked(["C0"]);
    setCurrentTime(0);
    setAnalyzing(true);
    try {
      const decoded = await decodeToMono(file);
      if (clipUrlRef.current !== url) return;
      const serviceFile = makeWavFile(decoded.samples, decoded.sampleRate, file.name);
      setClip((current) =>
        current && current.url === url
          ? {
              ...current,
              file: serviceFile,
              peaks: computePeaks(decoded.samples),
              duration: decoded.duration,
              sampleRate: decoded.sourceRate,
              channels: decoded.channels,
            }
          : current,
      );
      toast.success(
        file.type === "audio/wav" || /\.wav$/i.test(file.name)
          ? `${file.name} is ready.`
          : `Converted ${file.name} to WAV and it is ready.`,
      );
    } catch {
      if (clipUrlRef.current === url) {
        URL.revokeObjectURL(url);
        clipUrlRef.current = "";
        setClip(null);
      }
      toast.error(
        "This browser could not decode the audio for WAV conversion. Try WAV, FLAC, OGG, MP3 or another browser-supported audio format.",
      );
    } finally {
      if (clipUrlRef.current === url) setAnalyzing(false);
    }
  }, []);

  function loadSample(kind: SampleKind) {
    void loadClip(makeSampleClip(kind), "sample");
    toast(`${SAMPLE_CLIPS[kind].label}: synthetic tones and noise, not real speech.`);
  }

  async function toggleRecording() {
    if (recording) {
      recorderRef.current?.stop();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      toast.error("This browser does not support audio recording.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
      const chunks: BlobPart[] = [];
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("stop", async () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        const recording = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        if (!recording.size) {
          toast.error("The recording was empty. Try recording again.");
          return;
        }
        try {
          const decoded = await decodeToMono(recording);
          const wav = encodeWav(decoded.samples, decoded.sampleRate);
          const name = `recording-${new Date().toISOString().slice(11, 19).replace(/:/g, "")}.wav`;
          await loadClip(new File([wav], name, { type: "audio/wav" }), "recording");
        } catch {
          toast.error(
            "Could not convert the recording to WAV. Try another browser or upload a WAV, FLAC or OGG file.",
          );
        }
      });
      recorder.start(250);
      setRecordSeconds(0);
      setRecording(true);
      toast("Recording. Browser noise suppression is off so the raw signal is kept.");
    } catch {
      toast.error("Microphone access was not granted.");
    }
  }

  useEffect(() => {
    if (!recording) return;
    const timer = setInterval(() => setRecordSeconds((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [recording]);

  // ---------- Playback ----------
  const selectedAudio = result?.candidates[selected]?.audioUrl;
  const activeUrl =
    (selected !== "C0" && selectedAudio) || result?.candidates.C0?.audioUrl || clip?.url || "";
  const duration = clip ? clip.duration || mediaDuration : 0;

  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    if (!activeUrl) {
      el.pause();
      el.removeAttribute("src");
      setPlaying(false);
      return;
    }
    if (el.src === activeUrl) return;
    const resumeAt = el.currentTime;
    const wasPlaying = !el.paused;
    el.src = activeUrl;
    el.playbackRate = rate;
    el.addEventListener(
      "loadedmetadata",
      () => {
        if (resumeAt && Number.isFinite(el.duration))
          el.currentTime = Math.min(resumeAt, el.duration);
        if (wasPlaying) void el.play().catch(() => setPlaying(false));
      },
      { once: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeUrl]);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const loop = () => {
      if (audioRef.current) setCurrentTime(audioRef.current.currentTime);
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [playing]);

  const togglePlay = useCallback(() => {
    const el = audioRef.current;
    if (!el || !activeUrl) {
      toast("Add, record, or pick a sample clip to listen.");
      return;
    }
    if (el.paused)
      void el.play().catch(() => toast.error("This browser cannot play this audio format."));
    else el.pause();
  }, [activeUrl]);

  function seek(seconds: number) {
    const next = Math.max(0, Math.min(seconds, duration || 0));
    setCurrentTime(next);
    if (audioRef.current && activeUrl) audioRef.current.currentTime = next;
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.code !== "Space" || event.repeat) return;
      const target = event.target as HTMLElement | null;
      if (
        target &&
        target.closest("input, textarea, select, button, [role='dialog'], [contenteditable='true']")
      )
        return;
      event.preventDefault();
      togglePlay();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePlay]);

  // ---------- Pipeline ----------
  const requestedCandidates: TrackId[] = [
    "C0",
    "C1",
    "C2",
    ...(includeResearchCandidates ? (["C2B", "C2C"] as const) : []),
    ...(includeC3 ? (["C3"] as const) : []),
  ];
  const canTranscribe = health?.gnaniMode === "live" || health?.gnaniMode === "mock";
  const changeAsrModel = (model: string) => {
    setAsrModel(model);
    if (model !== "prisma-v3.0") {
      if (languageCode === "indic-auto") setLanguageCode("hi-IN");
      if (outputFormat === "codemix" || outputFormat === "transliterate") setOutputFormat("auto");
    }
  };

  function addHistory(entry: HistoryEntry) {
    setHistory((current) => saveHistory([entry, ...current]));
  }

  async function runPipeline() {
    if (busy) return;
    if (analyzing) {
      toast("Wait for the audio conversion to finish before starting the pipeline.");
      return;
    }
    if (!clip) {
      toast("Add an audio clip or pick a sample first.");
      goTo("studio");
      return;
    }
    if (!canTranscribe) {
      toast.error(
        health?.gnaniMode === "unconfigured"
          ? "GNANI_API_KEY is not configured on the backend."
          : "The backend is not reachable.",
      );
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setStage(-1);
    setResult(null);
    setResultSource(null);
    setRouted(null);
    setCandidatePeaks({});

    try {
      const response = await processAudioFile(apiUrl, clip.file, {
        mode,
        candidates: requestedCandidates,
        asrModel,
        outputFormat,
        languageCode,
        compareModels,
        oaLambda,
        signal: controller.signal,
      });
      setResult(response);
      setResultSource("service");
      setStage(5);
      const pick = response.selectedCandidate ?? selected;
      setRouted(response.selectedCandidate ?? null);
      setSelected(pick);
      setChecked([
        ...new Set<TrackId>([
          "C0",
          pick,
          ...(Object.keys(response.candidates) as TrackId[]).filter(
            (id) => response.candidates[id]?.transcript,
          ),
        ]),
      ]);
      addHistory({
        id: response.requestId ?? crypto.randomUUID(),
        fileName: clip.name,
        mode,
        source: "service",
        selectedCandidate: response.selectedCandidate,
        snrDb: response.analysis.snrDb,
        latencyMs: response.latencyMs,
        createdAt: new Date().toISOString(),
        result: response,
      });
      toast.success(
        `Processed in ${response.latencyMs ? `${(response.latencyMs / 1000).toFixed(1)} s` : "—"}.`,
      );
      void loadCandidatePeaks(response);
    } catch (error) {
      setStage(-1);
      toast.error(
        error instanceof Error ? error.message : "Audio processing could not be completed.",
      );
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  async function loadCandidatePeaks(response: ProcessingResult) {
    for (const id of TRACK_IDS) {
      const candidate = response.candidates[id];
      if (!candidate) continue;
      if (candidate.peaks?.length) {
        setCandidatePeaks((current) => ({ ...current, [id]: candidate.peaks! }));
        continue;
      }
      if (!candidate.audioUrl) continue;
      try {
        const blob = await (await fetch(candidate.audioUrl)).blob();
        const decoded = await decodeToMono(blob);
        setCandidatePeaks((current) => ({ ...current, [id]: computePeaks(decoded.samples) }));
      } catch {
        // Waveform stays as a placeholder; playback may still work.
      }
    }
  }

  function downloadResult() {
    if (!result) return;
    const payload = {
      file: clip?.name,
      mode,
      asr_model: asrModel,
      source: resultSource,
      exported_at: new Date().toISOString(),
      result,
    };
    downloadBlob(
      new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
      `bharatnoise-${(clip?.name ?? "result").replace(/\.[^.]+$/, "")}.json`,
    );
  }

  function restoreHistory(entry: HistoryEntry) {
    if (!entry.result) return;
    setResult(entry.result);
    setResultSource("service");
    setRouted(entry.selectedCandidate ?? null);
    if (entry.selectedCandidate) setSelected(entry.selectedCandidate);
    setStage(5);
    void loadCandidatePeaks(entry.result);
    toast(`Showing the saved result for ${entry.fileName}. The original audio is not stored.`);
    goTo("studio");
  }

  // ---------- Live monitor ----------
  function stopLive(message?: string) {
    liveSessionRef.current?.stop();
    liveSessionRef.current = null;
    setLive((value) => ({ ...value, state: "ready", levelDb: -120 }));
    if (message) toast(message);
  }

  async function toggleLive() {
    if (live.state !== "ready") {
      stopLive("Live monitor stopped.");
      return;
    }
    if (!canTranscribe) {
      toast.error(
        health?.gnaniMode === "unconfigured"
          ? "GNANI_API_KEY is not configured on the backend."
          : "The backend is not reachable.",
      );
      return;
    }
    const reset = {
      levelDb: -120,
      latencyMs: undefined,
      snrDb: undefined,
      finals: [],
      partial: "",
    };
    setLive({ ...reset, state: "connecting" });
    try {
      liveSessionRef.current = await startLiveStream(apiUrl, {
        onOpen: () => {
          setLive((value) => ({ ...value, state: "live" }));
          toast.success("Streaming to your proxy.");
        },
        onMessage: (message) => {
          if (message.type === "transcript") {
            setLive((value) =>
              message.final
                ? { ...value, finals: [...value.finals, message.text].slice(-20), partial: "" }
                : { ...value, partial: message.text },
            );
          } else if (message.type === "metrics") {
            setLive((value) => ({
              ...value,
              latencyMs: message.latencyMs ?? value.latencyMs,
              snrDb: message.snrDb ?? value.snrDb,
            }));
          } else if (message.type === "error") {
            toast.error(message.message);
          }
        },
        onStats: (stats) => setLive((value) => ({ ...value, levelDb: stats.levelDb })),
        onClose: (reason) => {
          liveSessionRef.current = null;
          setLive((value) => ({ ...value, state: "ready", levelDb: -120 }));
          if (reason) toast.error(reason);
        },
      });
    } catch (error) {
      setLive((value) => ({ ...value, state: "ready" }));
      toast.error(
        error instanceof Error && error.name === "NotAllowedError"
          ? "Microphone access was not granted."
          : error instanceof Error
            ? error.message
            : "Could not start the live monitor.",
      );
    }
  }

  // ---------- Derived views ----------
  const tracks: TrackView[] = useMemo(() => {
    const ids: TrackId[] = ["C0", "C1", "C2"];
    if (includeResearchCandidates) ids.push("C2B", "C2C");
    if (includeC3) ids.push("C3");
    return ids.map((id) => {
      const candidate = result?.candidates[id];
      if (!clip) return { id, peaks: null, status: "pending", hasAudio: false };
      const unavailableReason = result?.unavailableCandidates?.[id];
      if (unavailableReason) {
        return {
          id,
          peaks: null,
          status: "unavailable",
          hasAudio: false,
          reason: unavailableReason,
        };
      }
      if (id === "C0")
        return {
          id,
          peaks: clip.peaks ?? candidatePeaks.C0 ?? null,
          status: clip.peaks ? "real" : "pending",
          hasAudio: true,
        };
      const peaks = candidatePeaks[id] ?? candidate?.peaks ?? null;
      if (peaks) return { id, peaks, status: "service", hasAudio: Boolean(candidate?.audioUrl) };
      return {
        id,
        peaks: null,
        status: resultSource === "service" && !candidate ? "skipped" : "pending",
        hasAudio: Boolean(candidate?.audioUrl),
      };
    });
  }, [clip, includeC3, includeResearchCandidates, result, candidatePeaks, resultSource]);

  const playbackNote =
    clip && selected !== "C0" && !selectedAudio
      ? `Playing C0 original · ${selected} ${trackName(selected)} audio comes from your service${resultSource === "service" ? " and was not returned for this run" : ""}.`
      : clip && selected !== "C0" && selectedAudio
        ? `Playing ${selected} ${trackName(selected)} from your service.`
        : undefined;

  const analysis: AnalysisView = useMemo(() => {
    if (result && resultSource === "service") {
      const a = result.analysis;
      return {
        source: "service",
        snrDb: a.snrDb,
        noiseFloorDb: a.noiseFloorDb,
        speechEnergyDb: a.speechEnergyDb,
        vadBins: a.vadSegments.length ? segmentsToBins(a.vadSegments, duration || 1) : [],
        spectrum: a.spectrum ?? [],
        guardStatus: a.guardStatus,
        guardScore: a.guardScore,
        duration,
      };
    }
    return { source: "empty", vadBins: [], spectrum: [], duration };
  }, [result, resultSource, duration]);

  const transcripts: TranscriptView[] = useMemo(() => {
    const ids = TRACK_IDS.filter(
      (id) =>
        (checked.includes(id) || id === selected) &&
        (includeC3 || id !== "C3") &&
        (includeResearchCandidates || (id !== "C2B" && id !== "C2C")),
    );
    return ids.map((id) => {
      if (result && resultSource === "service") {
        const candidate = result.candidates[id];
        const text =
          candidate?.transcript ??
          (id === result.selectedCandidate ? result.transcription : undefined);
        const unavailable = result.unavailableCandidates?.[id];
        const hasServiceResult = candidate !== undefined || id === result.selectedCandidate;
        return {
          id,
          text,
          source: hasServiceResult ? "service" : "missing",
          reason: unavailable
            ? `${id} unavailable: ${unavailable}`
            : candidate?.error
              ? `Prisma request failed: ${candidate.error}`
              : hasServiceResult && !text?.trim()
                ? "Gnani returned an empty transcript. Check the language setting and compare the original audio."
                : undefined,
          truncated: candidate?.truncated,
        };
      }
      return { id, source: "missing" };
    });
  }, [checked, selected, result, resultSource, includeC3, includeResearchCandidates]);
  const baseline =
    transcripts.find((item) => item.id === "C0")?.text ??
    (resultSource === "service" ? result?.candidates.C0?.transcript : undefined);

  const routeNote =
    resultSource === "service" && result?.pipeline.length ? result.pipeline.join(" → ") : "";

  const modelLabel =
    ASR_MODELS.find((model) => model.value === asrModel)?.label.replace(" · primary", "") ??
    "Gnani Prisma";
  const mockMode = health?.gnaniMode === "mock" || result?.gnaniMode === "mock";
  const unavailableMessage =
    health?.gnaniMode === "unconfigured"
      ? "Transcription is disabled because GNANI_API_KEY is not configured on the backend."
      : healthError
        ? `Backend unreachable: ${healthError}`
        : "Checking backend availability.";
  const statusText = !health
    ? healthError
      ? `Backend unreachable: ${healthError}`
      : "Checking backend availability."
    : health.gnaniMode === "unconfigured"
      ? "Backend reachable · GNANI_API_KEY is not configured · transcription disabled"
      : health.gnaniMode === "mock"
        ? "Mock GNANI configured · transcript output is not Prisma speech recognition"
        : "Live Gnani service configured";
  return (
    <main className="workbench-shell min-h-screen bg-background text-foreground">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <AudioLines size={22} strokeWidth={2.3} />
          </div>
          <span className="brand-name">
            Bharat<span>Noise</span>
          </span>
        </div>
        <div className="top-title">
          <strong>Adaptive Speech Enhancement for Gnani AI Prisma</strong>
          <span>Indian telephony ASR · Enhance · Transcribe · Evaluate</span>
        </div>
        <div className="top-controls">
          <label className="model-picker">
            <span>ASR model</span>
            <select
              aria-label="ASR model"
              value={asrModel}
              onChange={(event) => changeAsrModel(event.currentTarget.value)}
              disabled={!canTranscribe}
            >
              {ASR_MODELS.map((model) => (
                <option key={model.value} value={model.value}>
                  {mockMode ? `mock-${model.label}` : model.label}
                </option>
              ))}
            </select>
          </label>
          <Button
            variant="ghost"
            size="icon"
            className="theme-toggle"
            onClick={toggleTheme}
            aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
            title={dark ? "Light theme" : "Dark theme"}
          >
            {dark ? <Sun /> : <Moon />}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="theme-toggle"
            onClick={() => setSettingsOpen(true)}
            aria-label="Open audio service settings"
            title="Audio service settings"
          >
            <Settings2 />
          </Button>
        </div>
        {mockMode && (
          <div className="mock-mode-banner" role="status">
            MOCK GNANI: transcripts are placeholders, not Prisma output
          </div>
        )}
      </header>

      <div className="workbench-grid">
        <aside className="sidebar">
          <nav className="sidebar-nav" aria-label="Workstation sections">
            {SECTIONS.map((section) => (
              <button
                key={section.id}
                className={`nav-item ${activeSection === section.id ? "active" : ""}`}
                aria-current={activeSection === section.id ? "true" : undefined}
                onClick={() => goTo(section.id)}
              >
                {section.icon}
                <span>{section.label}</span>
              </button>
            ))}
          </nav>
          <div className="sidebar-spacer" />
          <button
            className="service-card"
            onClick={() => setSettingsOpen(true)}
            aria-label="Configure audio service connection"
          >
            <span className="service-brand">
              <AudioLines size={17} /> BharatNoise
            </span>
            <span>Telephony · 8 kHz · PCM16</span>
            <span>Multi-pipeline · Gnani Prisma</span>
            <span
              className={`service-status ${canTranscribe ? "connected" : health ? "demo" : "pending"}`}
            >
              <span />
              {health?.gnaniMode === "live"
                ? "Live service"
                : health?.gnaniMode === "mock"
                  ? "Mock GNANI"
                  : health?.gnaniMode === "unconfigured"
                    ? "GNANI key missing"
                    : healthError
                      ? "Backend unreachable"
                      : "Checking backend"}
            </span>
          </button>
        </aside>

        <div className="workspace-content">
          <div className={`status-strip ${canTranscribe ? "is-connected" : ""}`} role="status">
            <span className="demo-dot" />
            <span>{statusText}</span>
            <button onClick={() => setSettingsOpen(true)}>
              Service settings
              <ChevronRight size={12} />
            </button>
          </div>

          <section className="studio-layout" id="section-studio">
            <AudioStudio
              clip={
                clip
                  ? {
                      name: clip.name,
                      source: clip.source,
                      sampleRate: clip.sampleRate,
                      channels: clip.channels,
                    }
                  : null
              }
              analyzing={analyzing}
              pipelineBusy={busy}
              tracks={tracks}
              selected={selected}
              onSelect={setSelected}
              checked={checked}
              onToggleCheck={(id, value) =>
                setChecked((current) =>
                  value ? [...new Set([...current, id])] : current.filter((item) => item !== id),
                )
              }
              onFile={(file) => void loadClip(file, "upload")}
              recording={recording}
              recordSeconds={recordSeconds}
              onRecord={() => void toggleRecording()}
              onSample={loadSample}
              playing={playing}
              currentTime={currentTime}
              duration={duration}
              onPlayToggle={togglePlay}
              onSeek={seek}
              volume={volume}
              onVolume={(value) => {
                setVolume(value);
                if (audioRef.current) audioRef.current.volume = value;
              }}
              rate={rate}
              onRate={(value) => {
                setRate(value);
                if (audioRef.current) audioRef.current.playbackRate = value;
              }}
              playbackNote={playbackNote}
            />
            <PipelinePanel
              busy={busy}
              stage={stage}
              result={result}
              resultSource={resultSource}
              routedCandidate={routed}
              routeNote={routeNote}
              mode={mode}
              onMode={setMode}
              model={asrModel}
              languageCode={languageCode}
              onLanguageCode={(language) => {
                setLanguageCode(language);
                if (language === "indic-auto") setCompareModels(false);
              }}
              outputFormat={outputFormat}
              onOutputFormat={setOutputFormat}
              includeC3={includeC3}
              onIncludeC3={(value) => {
                setIncludeC3(value);
                if (!value && selected === "C3") setSelected("C2");
              }}
              includeResearchCandidates={includeResearchCandidates}
              onIncludeResearchCandidates={(value) => {
                setIncludeResearchCandidates(value);
                if (!value && (selected === "C2B" || selected === "C2C")) setSelected("C2");
              }}
              oaLambda={oaLambda}
              oaSweep={oaSweep}
              imcraFitted={imcraFitted}
              imcraConfigError={imcraConfigError}
              onOaLambda={setOaLambda}
              compareModels={compareModels}
              onCompareModels={setCompareModels}
              canRun={canTranscribe && !analyzing}
              unavailableMessage={unavailableMessage}
              onRun={() => void runPipeline()}
              onCancel={() => abortRef.current?.abort()}
              onDownload={downloadResult}
              snrDb={analysis.snrDb}
            />
          </section>

          <section className="insight-layout">
            <AnalysisPanel view={analysis} tab={analysisTab} onTab={setAnalysisTab} />
            <TranscriptPanel
              transcripts={transcripts}
              selected={selected}
              baseline={baseline ?? undefined}
              reference={reference}
              onReference={setReference}
              mode={mode}
              modelLabel={mockMode ? `mock-${modelLabel}` : modelLabel}
              requestedLanguageCode={result?.languageCode ?? languageCode}
              outputFormat={result?.outputFormat ?? outputFormat}
              detectedLanguageCode={result?.detectedLanguageCode ?? undefined}
              mockMode={mockMode}
              onSelect={setSelected}
              apiUrl={apiUrl}
            />
          </section>

          <section className="bottom-layout">
            <EvaluationPanel
              report={report}
              reportSource={reportSource}
              onReport={(next, source) => {
                setReport(next);
                setReportSource(source);
              }}
              apiUrl={apiUrl}
              lastResult={resultSource === "service" ? result : null}
              mockMode={mockMode}
            />
            <LivePanel
              view={live}
              onToggle={() => void toggleLive()}
              canRun={canTranscribe}
              unavailableMessage={unavailableMessage}
              mockMode={mockMode}
            />
          </section>

          <HistoryPanel
            entries={history}
            onClear={() => setHistory(saveHistory([]))}
            onRestore={restoreHistory}
          />

          <footer className="workspace-footer">
            <span>BharatNoise · Audio workstation</span>
            <span>
              {resultSource === "service"
                ? "Showing a service response"
                : "No verified performance claims on this screen"}
            </span>
            <Button variant="ghost" size="sm" onClick={() => setSettingsOpen(true)}>
              <SlidersHorizontal /> Service setup
            </Button>
          </footer>
        </div>
      </div>

      <audio
        ref={audioRef}
        preload="metadata"
        className="visually-hidden"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        onLoadedMetadata={(event) => {
          const d = event.currentTarget.duration;
          if (Number.isFinite(d)) setMediaDuration(d);
          event.currentTarget.volume = volume;
        }}
      />

      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        apiUrl={apiUrl}
        health={health}
        onHealth={(info) => {
          setHealth(info);
          setHealthError("");
        }}
        onSave={(url) => {
          const saved = saveApiBaseUrl(url);
          setApiUrl(saved);
          setHealth(null);
          setHealthError("");
          setOaSweep([]);
          setImcraFitted(null);
          setImcraConfigError("");
          if (!saved) {
            setHealth(null);
            setHealthError("");
            return;
          }
          checkApiConnection(saved)
            .then((info) => {
              setHealth(info);
              setHealthError("");
            })
            .catch((error: unknown) => {
              setHealth(null);
              setHealthError(error instanceof Error ? error.message : "Service not reachable.");
            });
          fetchCandidateConfig(saved)
            .then((config) => {
              setOaSweep(config.oaSweep);
              setOaLambda(config.oaLambda);
              setImcraFitted(config.fitted);
              setImcraConfigError("");
            })
            .catch((error: unknown) => {
              setOaSweep([]);
              setImcraFitted(null);
              setImcraConfigError(
                error instanceof Error
                  ? `Experimental candidates unavailable: ${error.message}`
                  : "Could not load candidate settings.",
              );
            });
        }}
      />
    </main>
  );
}
