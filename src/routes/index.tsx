import { useEffect, useMemo, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  Activity, AudioLines, BarChart3, Check, ChevronDown, CircleHelp, Clock3, CloudUpload,
  FileAudio, Gauge, Headphones, History, Home, Layers3, ListChecks, LoaderCircle,
  Moon, Pause, Play, Radio, RotateCcw, Settings2, ShieldCheck, SkipBack, SkipForward,
  SlidersHorizontal, Sun, Trophy, Upload, Volume2, Waves, X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  checkApiConnection, getApiBaseUrl, makeLiveSocketUrl, processAudioFile, saveApiBaseUrl,
  type AccuracyMode, type ProcessingResult,
} from "@/lib/bharatnoise-api";
import { getExecutionProfile } from "@/lib/processing-contract";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "BharatNoise | AI Audio Workstation" },
      { name: "description", content: "Audio workspace for investigating noisy telephony speech, candidate enhancements, and Gnani Prisma transcription." },
      { property: "og:title", content: "BharatNoise | AI Audio Workstation" },
      { property: "og:description", content: "Inspect audio candidates, speech analysis, transcripts, and evaluation in one workspace." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: BharatNoise,
});

type TrackId = "C0" | "C1" | "C2" | "C3";
type WorkspaceTab = "studio" | "pipeline" | "analysis" | "transcript" | "evaluation" | "live" | "history";
type AnalysisTab = "vad" | "spectrum" | "floor";

const trackDefs: { id: TrackId; name: string; detail: string; color: "cyan" | "violet" }[] = [
  { id: "C0", name: "Original", detail: "Reference input", color: "cyan" },
  { id: "C1", name: "Hygiene", detail: "Band-limit · hum notch · gain", color: "cyan" },
  { id: "C2", name: "Enhanced", detail: "Wiener / spectral gate · blend", color: "violet" },
  { id: "C3", name: "DeepFilterNet", detail: "8 → 48 → 8 kHz · optional", color: "cyan" },
];

const stageNames = ["Ingest & prepare", "Analyse VAD / SNR", "Generate C0–C3", "Speech-preservation guard", "Route & transcribe"];
const sampleTexts = {
  C0: "haan mujhe voh plan kal tak bhej dena varna problem ho jayegi",
  C1: "haan mujhe voh plan kal tak bhej dena varna problem ho jayegi",
  C2: "haan mujhe woh plan kal tak bhej dena warna problem ho jayegi",
  C3: "haan mujhe woh plan kal tak bhej dena varna problem ho jayegi",
};

function BharatNoise() {
  const [dark, setDark] = useState(true);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("studio");
  const [analysisTab, setAnalysisTab] = useState<AnalysisTab>("vad");
  const [mode, setMode] = useState<AccuracyMode>("fast");
  const [selected, setSelected] = useState<TrackId>("C2");
  const [checkedTracks, setCheckedTracks] = useState<TrackId[]>(["C2"]);
  const [file, setFile] = useState<File | null>(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [duration, setDuration] = useState(14.7);
  const [currentTime, setCurrentTime] = useState(4.2);
  const [playing, setPlaying] = useState(false);
  const [volume, setVolume] = useState(0.66);
  const [playbackRate, setPlaybackRate] = useState("1×");
  const [result, setResult] = useState<ProcessingResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState(-1);
  const [apiUrl, setApiUrl] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [checkingConnection, setCheckingConnection] = useState(false);
  const [liveState, setLiveState] = useState<"ready" | "connecting" | "live">("ready");
  const [liveSeconds, setLiveSeconds] = useState(0);
  const [recording, setRecording] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const [transcriptTab, setTranscriptTab] = useState<"compare" | "single">("compare");
  const [evalTab, setEvalTab] = useState<"comparison" | "ablation" | "snr" | "noise">("comparison");
  const [filter, setFilter] = useState("");
  const [includeC3, setIncludeC3] = useState(true);
  const [comparePrisma, setComparePrisma] = useState(true);
  const [historyItems, setHistoryItems] = useState<string[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const recordRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const liveSocketRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const frameTimerRef = useRef<number | null>(null);
  const timeTimerRef = useRef<number | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analysisProcessorRef = useRef<ScriptProcessorNode | null>(null);

  useEffect(() => {
    setApiUrl(getApiBaseUrl());
    const stored = window.localStorage.getItem("bharatnoise.theme");
    if (stored) setDark(stored === "dark");
  }, []);

  useEffect(() => {
    window.localStorage.setItem("bharatnoise.theme", dark ? "dark" : "light");
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  useEffect(() => {
    return () => {
      if (audioUrl.startsWith("blob:")) URL.revokeObjectURL(audioUrl);
      if (timeTimerRef.current !== null) window.clearInterval(timeTimerRef.current);
      if (frameTimerRef.current !== null) window.clearInterval(frameTimerRef.current);
      liveSocketRef.current?.close();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      analysisProcessorRef.current?.disconnect();
      void audioContextRef.current?.close();
    };
  }, [audioUrl]);

  useEffect(() => {
    if (!playing || !audioRef.current) return;
    const element = audioRef.current;
    void element.play().catch(() => setPlaying(false));
    const update = () => setCurrentTime(element.currentTime);
    element.addEventListener("timeupdate", update);
    element.addEventListener("ended", () => setPlaying(false), { once: true });
    return () => element.removeEventListener("timeupdate", update);
  }, [playing, audioUrl]);

  useEffect(() => {
    if (!recording && !busy && liveState !== "live") return;
    timeTimerRef.current = window.setInterval(() => {
      if (recording) setRecordSeconds((value) => value + 1);
      if (liveState === "live") setLiveSeconds((value) => value + 1);
    }, 1000);
    return () => {
      if (timeTimerRef.current !== null) window.clearInterval(timeTimerRef.current);
    };
  }, [recording, busy, liveState]);

  const profile = useMemo(() => getExecutionProfile(mode), [mode]);
  const samples = useMemo(() => Array.from({ length: 320 }, (_, index) => {
    const base = Math.sin(index * 0.74) * 11 + Math.sin(index * 1.93) * 7 + Math.sin(index * 0.19) * 6;
    const speech = Math.sin(index * 0.071) * 9 + Math.sin(index * 0.23) * 5;
    const amplitude = [0.82, 0.62, 1.04, 0.75][trackDefs.findIndex((track) => track.id === selected)] ?? 1;
    return Math.max(8, Math.min(45, 21 + (base + speech) * amplitude));
  }), [selected]);
  const selectedText = result?.candidates?.[selected] ?? result?.transcription ?? sampleTexts[selected];
  const snr = result?.analysis?.snr_db;
  const noiseFloor = result?.analysis?.noise_floor_db;
  const speechEnergy = result?.analysis?.speech_energy_db;
  const isLiveDemo = !apiUrl;

  function setActive(tab: WorkspaceTab) {
    setActiveTab(tab);
    const section = document.getElementById(`section-${tab}`);
    section?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function chooseFile(nextFile?: File) {
    if (!nextFile) return;
    if (!nextFile.type.startsWith("audio/")) {
      toast.error("Choose an audio file to continue.");
      return;
    }
    if (nextFile.size > 80 * 1024 * 1024) {
      toast.error("Audio files must be 80 MB or smaller.");
      return;
    }
    const nextUrl = URL.createObjectURL(nextFile);
    setAudioUrl((current) => {
      if (current.startsWith("blob:")) URL.revokeObjectURL(current);
      return nextUrl;
    });
    setFile(nextFile);
    setResult(null);
    setCurrentTime(0);
    setSelected("C0");
    setCheckedTracks(["C0"]);
    setDuration(14.7);
    if (audioRef.current) audioRef.current.src = nextUrl;
    toast.success(`${nextFile.name} added to the studio.`);
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    chooseFile(event.currentTarget.files?.[0]);
    event.currentTarget.value = "";
  }

  async function startRecording() {
    if (recording) {
      recordRef.current?.stop();
      setRecording(false);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      toast.error("This browser does not support audio recording.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = stream;
      chunksRef.current = [];
      const recorder = new MediaRecorder(stream);
      recordRef.current = recorder;
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size) chunksRef.current.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        chooseFile(new File([blob], `recording-${new Date().toISOString().slice(11, 19)}.webm`, { type: blob.type }));
        stream.getTracks().forEach((track) => track.stop());
        if (streamRef.current === stream) streamRef.current = null;
      });
      recorder.start(250);
      setRecordSeconds(0);
      setRecording(true);
    } catch {
      toast.error("Microphone access was not granted.");
    }
  }

  async function saveProxy() {
    try {
      const normalized = saveApiBaseUrl(apiUrl);
      setApiUrl(normalized);
      toast.success(normalized ? "Audio service address saved." : "Demo mode enabled.");
      setSettingsOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "That service address is not valid.");
    }
  }

  async function testConnection() {
    try {
      const normalized = saveApiBaseUrl(apiUrl);
      setApiUrl(normalized);
      setCheckingConnection(true);
      await checkApiConnection(normalized);
      toast.success("Audio service is reachable.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not reach the audio service.");
    } finally {
      setCheckingConnection(false);
    }
  }

  async function runPipeline() {
    if (busy) return;
    if (!file) {
      uploadRef.current?.click();
      toast("Add an audio file first.");
      return;
    }
    if (!apiUrl) {
      setBusy(true);
      setStage(0);
      await new Promise((resolve) => window.setTimeout(resolve, 450));
      setStage(1);
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      setStage(2);
      await new Promise((resolve) => window.setTimeout(resolve, 650));
      setStage(3);
      await new Promise((resolve) => window.setTimeout(resolve, 550));
      setStage(4);
      await new Promise((resolve) => window.setTimeout(resolve, 350));
      setBusy(false);
      toast("Demo preview complete. Connect the Gnani-ready service to process real audio.");
      return;
    }
    setBusy(true);
    setStage(0);
    try {
      const stageTimer = window.setInterval(() => setStage((value) => Math.min(value + 1, 3)), 800);
      try {
        const response = await processAudioFile(apiUrl, file, mode);
        setResult(response);
        const selectedCandidate = response.selected_candidate?.toUpperCase();
        if (["C0", "C1", "C2", "C3"].includes(selectedCandidate ?? "")) {
          setSelected(selectedCandidate as TrackId);
          setCheckedTracks([selectedCandidate as TrackId]);
        }
        setStage(4);
        setHistoryItems((items) => [`${file.name} · ${mode === "fast" ? "Fast" : "Max accuracy"} · ${new Date().toLocaleTimeString()}`, ...items]);
        toast.success("Audio processing is complete.");
      } finally {
        window.clearInterval(stageTimer);
      }
    } catch (error) {
      setStage(-1);
      toast.error(error instanceof Error ? error.message : "Audio processing could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleLive() {
    if (liveState === "live" || liveState === "connecting") {
      frameTimerRef.current !== null && window.clearInterval(frameTimerRef.current);
      frameTimerRef.current = null;
      analysisProcessorRef.current?.disconnect();
      analysisProcessorRef.current = null;
      liveSocketRef.current?.close();
      liveSocketRef.current = null;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      await audioContextRef.current?.close().catch(() => undefined);
      audioContextRef.current = null;
      setLiveState("ready");
      toast("Live monitor stopped.");
      return;
    }
    if (!apiUrl) {
      setLiveState("live");
      setLiveSeconds(0);
      toast("Demo monitor started. Add a proxy address to stream audio.");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      toast.error("Microphone access is not available in this browser.");
      return;
    }
    setLiveState("connecting");
    let liveStream: MediaStream | undefined;
    try {
      liveStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = liveStream;
      const socket = new WebSocket(makeLiveSocketUrl(apiUrl));
      socket.binaryType = "arraybuffer";
      liveSocketRef.current = socket;
      socket.addEventListener("open", () => {
        if (socket.readyState !== WebSocket.OPEN) return;
        socket.send(JSON.stringify({ sample_rate: 8000, format: "pcm_s16le", vad_preset: "telephony" }));
        const context = new AudioContext();
        audioContextRef.current = context;
        const source = context.createMediaStreamSource(liveStream as MediaStream);
        const processor = context.createScriptProcessor(1024, 1, 1);
        analysisProcessorRef.current = processor;
        processor.onaudioprocess = (event) => {
          if (socket.readyState !== WebSocket.OPEN) return;
          const input = event.inputBuffer.getChannelData(0);
          const targetLength = 512;
          const pcm = new Int16Array(targetLength);
          for (let i = 0; i < targetLength; i += 1) {
            const sourceIndex = Math.min(input.length - 1, Math.floor(i * input.length / targetLength));
            const value = input[sourceIndex] ?? 0;
            pcm[i] = Math.max(-32768, Math.min(32767, Math.round(value * 32767)));
          }
          if (frameTimerRef.current === null) {
            let pending = new Uint8Array(0);
            frameTimerRef.current = window.setInterval(() => {
              if (pending.length < 1024 || socket.readyState !== WebSocket.OPEN) return;
              const frame = pending.slice(0, 1024);
              pending = pending.slice(1024);
              socket.send(frame.buffer);
            }, 64);
            Object.defineProperty(processor, "appendPcm", {
              value: (bytes: Uint8Array) => {
                const combined = new Uint8Array(pending.length + bytes.length);
                combined.set(pending);
                combined.set(bytes, pending.length);
                pending = combined;
              },
            });
          }
          const append = (processor as ScriptProcessorNode & { appendPcm?: (bytes: Uint8Array) => void }).appendPcm;
          append?.(new Uint8Array(pcm.buffer));
        };
        source.connect(processor);
        processor.connect(context.destination);
        setLiveState("live");
        setLiveSeconds(0);
        toast.success("Live audio connected to the proxy.");
      });
      socket.addEventListener("error", () => {
        toast.error("The live audio proxy could not be reached.");
        setLiveState("ready");
      });
      socket.addEventListener("close", () => {
        if (liveSocketRef.current === socket) setLiveState("ready");
      });
    } catch (error) {
      liveStream?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      liveSocketRef.current?.close();
      setLiveState("ready");
      toast.error(error instanceof Error ? error.message : "Microphone connection failed.");
    }
  }

  function changeTrack(id: TrackId, checked: boolean) {
    setCheckedTracks((current) => checked ? [...new Set([...current, id])] : current.filter((item) => item !== id));
    if (checked) setSelected(id);
  }

  function seek(event: ChangeEvent<HTMLInputElement>) {
    const next = Number(event.currentTarget.value);
    setCurrentTime(next);
    if (audioRef.current) audioRef.current.currentTime = next;
  }

  function setRate(next: string) {
    setPlaybackRate(next);
    if (audioRef.current) audioRef.current.playbackRate = Number.parseFloat(next);
  }

  const formatTime = (seconds: number) => {
    const whole = Math.max(0, Math.floor(seconds));
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
  };

  return (
    <main className="workbench-shell min-h-screen bg-background text-foreground" data-theme={dark ? "dark" : "light"}>
      <div className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark"><AudioLines size={25} strokeWidth={2.3} /></div>
          <span className="brand-name">Bharat<span>Noise</span></span>
        </div>
        <div className="top-title"><strong>Adaptive Speech Enhancement for Gnani AI Prisma</strong><span>Indian telephony ASR · Enhance · Transcribe · Evaluate</span></div>
        <div className="top-controls">
          <label className="model-picker"><span>ASR model</span><select aria-label="ASR model"><option>Prisma v2.5 · primary</option><option>Prisma v3.0</option></select><ChevronDown size={13} /></label>
          <Button variant="ghost" size="icon" className="theme-toggle" onClick={() => setDark((value) => !value)} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"} title={dark ? "Switch to light theme" : "Switch to dark theme"}>{dark ? <Sun /> : <Moon />}</Button>
          <Button variant="ghost" size="icon" className="theme-toggle" onClick={() => setSettingsOpen(true)} aria-label="Open audio service settings" title="Audio service settings"><Settings2 /></Button>
        </div>
      </div>

      <div className="workbench-grid">
        <aside className="sidebar">
          <div className="sidebar-nav" aria-label="Workstation sections">
            <NavItem icon={<Home />} label="Audio Studio" active={activeTab === "studio"} onClick={() => setActive("studio")} />
            <NavItem icon={<Layers3 />} label="Enhancement Pipeline" active={activeTab === "pipeline"} onClick={() => setActive("pipeline")} />
            <NavItem icon={<BarChart3 />} label="Speech Analysis" active={activeTab === "analysis"} onClick={() => setActive("analysis")} />
            <NavItem icon={<FileAudio />} label="Transcript Studio" active={activeTab === "transcript"} onClick={() => setActive("transcript")} />
            <NavItem icon={<Trophy />} label="Evaluation Lab" active={activeTab === "evaluation"} onClick={() => setActive("evaluation")} />
            <NavItem icon={<Radio />} label="Live Monitor" active={activeTab === "live"} onClick={() => setActive("live")} />
            <NavItem icon={<History />} label="Experiment History" active={activeTab === "history"} onClick={() => setActive("history")} />
          </div>
          <div className="sidebar-spacer" />
          <button className="service-card" onClick={() => setSettingsOpen(true)} aria-label="Configure audio service connection">
            <span className="service-brand"><AudioLines size={17} /> BharatNoise</span>
            <span>Telephony · 8 kHz</span><span>Multi-pipeline · Gnani Prisma</span>
            <span className={`service-status ${apiUrl ? "connected" : "demo"}`}><span />{apiUrl ? "Proxy configured" : "Demo workspace"}</span>
          </button>
        </aside>

        <div className="workspace-content">
          <div className="status-strip"><span className="demo-dot" /><span>{apiUrl ? "Audio service address saved" : "Demo preview · no live processing connected"}</span><button onClick={() => setSettingsOpen(true)}>{apiUrl ? "Service settings" : "Connect a service"}<ChevronDown size={12} /></button></div>
          <section className="studio-layout" id="section-studio">
            <section className="panel studio-panel" aria-labelledby="studio-heading">
              <div className="panel-heading studio-heading">
                <div className="panel-title"><AudioLines /><div><h1 id="studio-heading">Audio Studio</h1><span>Inspect and compare candidate waveforms</span></div></div>
                <div className="studio-actions">
                  <input ref={uploadRef} className="visually-hidden" type="file" accept="audio/*,.wav,.mp3,.ogg,.m4a,.flac,.webm" onChange={onFileChange} aria-label="Choose audio file" />
                  <Button className="upload-button" onClick={() => uploadRef.current?.click()}><CloudUpload /> Add audio</Button>
                  <Button variant="outline" className={`record-button ${recording ? "is-recording" : ""}`} onClick={() => void startRecording()}><span className="record-dot" />{recording ? `Stop · ${formatTime(recordSeconds)}` : "Record"}</Button>
                  <select className="sample-select" aria-label="Load sample audio" onChange={(event) => {
                    if (!event.currentTarget.value) return;
                    setFile(null);
                    setAudioUrl("");
                    setResult(null);
                    setDuration(14.7);
                    setCurrentTime(0);
                    toast("Sample view loaded. Add a real file to play audio.");
                  }}><option value="">Samples</option><option value="call">Noisy Hindi call</option><option value="street">Street interview</option><option value="quiet">Clean reference</option></select>
                </div>
              </div>
              <div className="audio-meta"><span className="file-chip"><PhoneGlyph />{file?.name ?? "call_traffic_hindi.wav"}<span className="sample-tag">{file ? "UPLOADED" : "SAMPLE"}</span></span><span className="meta-chip"><Activity />8 kHz</span><span className="meta-chip">Mono</span><span className="meta-chip"><Clock3 />{formatTime(duration)}{duration % 60 ? `.${String(Math.round((duration % 1) * 10))}` : ""}</span></div>
              <div className="timeline-scale"><span /><span>0:02</span><span>0:04</span><span>0:06</span><span>0:08</span><span>0:10</span><span>0:12</span><span>0:14</span></div>
              <div className="waveform-list">
                {trackDefs.filter((track) => includeC3 || track.id !== "C3").map((track, row) => (
                  <div key={track.id} className={`wave-row ${selected === track.id ? "selected" : ""}`}>
                    <label className="track-toggle" title={`Show ${track.id} in comparison`}><input type="checkbox" checked={checkedTracks.includes(track.id)} onChange={(event) => changeTrack(track.id, event.currentTarget.checked)} aria-label={`Show ${track.id} in comparison`} /></label>
                    <button className="track-label" onClick={() => setSelected(track.id)} aria-label={`Listen to ${track.id} ${track.name}`}><AudioLines size={15} /><strong>{track.id}</strong><span><b>{track.name}</b><small>{track.detail}</small></span></button>
                    <button className={`waveform wave-${track.color}`} onClick={() => setSelected(track.id)} aria-label={`Select ${track.id} waveform`}>
                      <svg viewBox="0 0 900 48" preserveAspectRatio="none" aria-hidden="true"><path d={samples.map((height, index) => {
                        const varying = Math.max(5, Math.min(45, height + Math.sin((index + row * 21) * 0.51) * 8));
                        const x = (index / (samples.length - 1)) * 900;
                        return `M ${x.toFixed(1)} ${(24 - varying / 2).toFixed(1)} v ${varying.toFixed(1)}`;
                      }).join(" ")} /></svg>
                    </button>
                    {row === 0 && <div className="playhead" style={{ left: `${Math.max(0, Math.min(100, duration ? currentTime / duration * 100 : 0))}%` }} />}
                  </div>
                ))}
              </div>
              <div className="transport">
                <div className="transport-left">
                  <Button variant="secondary" size="icon" aria-label="Skip to beginning" onClick={() => { setCurrentTime(0); if (audioRef.current) audioRef.current.currentTime = 0; }}><SkipBack /></Button>
                  <Button size="icon" className="play-button" aria-label={playing ? "Pause audio" : "Play audio"} onClick={() => { if (!audioUrl) { toast("Add an audio clip to listen to it."); return; } setPlaying((value) => !value); }}><span className="play-symbol">{playing ? <Pause /> : <Play />}</span></Button>
                  <Button variant="secondary" size="icon" aria-label="Skip to end" onClick={() => { setCurrentTime(duration); if (audioRef.current) audioRef.current.currentTime = duration; }}><SkipForward /></Button>
                  <span className="time-display">{formatTime(currentTime)} <i>/</i> {formatTime(duration)}</span>
                </div>
                <input className="seek-slider" aria-label="Audio playback position" type="range" min="0" max={duration} step="0.01" value={Math.min(currentTime, duration)} onChange={seek} />
                <div className="transport-right"><Volume2 size={16} /><input aria-label="Audio volume" className="volume-slider" type="range" min="0" max="1" step="0.01" value={volume} onChange={(event) => { const next = Number(event.currentTarget.value); setVolume(next); if (audioRef.current) audioRef.current.volume = next; }} /><select aria-label="Playback speed" value={playbackRate} onChange={(event) => setRate(event.currentTarget.value)}><option>0.75×</option><option>1×</option><option>1.25×</option><option>1.5×</option><option>2×</option></select></div>
              </div>
              <audio ref={audioRef} className="visually-hidden" src={audioUrl || undefined} onLoadedMetadata={(event) => { const nextDuration = event.currentTarget.duration; if (Number.isFinite(nextDuration)) setDuration(nextDuration); }} onVolumeChange={(event) => setVolume(event.currentTarget.volume)} />
            </section>

            <section className="panel pipeline-panel" id="section-pipeline" aria-labelledby="pipeline-heading">
              <div className="panel-heading"><div className="panel-title"><Layers3 /><h2 id="pipeline-heading">Enhancement pipeline</h2></div><span className={`run-indicator ${busy ? "running" : ""}`}><span />{busy ? "Working" : result ? "Complete" : "Ready"}</span></div>
              <div className="stage-list">{stageNames.map((name, index) => <div className="stage-item" key={name}><span className={`stage-check ${stage >= index ? "done" : ""}`}>{stage >= index ? <Check size={13} /> : String(index + 1)}</span><span>{name}</span><small>{index === 0 ? "8 kHz · PCM16" : index === 1 ? snr !== undefined ? `SNR ${snr.toFixed(1)} dB` : "VAD + SNR" : index === 2 ? includeC3 ? "C0, C1, C2, C3" : "C0, C1, C2" : index === 3 ? result?.analysis?.guard_status ?? (result ? "checked" : "pending") : mode === "fast" ? "Fast · 1 call" : "Max · 3–4 calls"}</small></div>)}</div>
              <div className="selected-route"><span>ASR candidate</span><span className="candidate-badge">{selected} · {trackDefs.find((track) => track.id === selected)?.name}</span></div>
              <div className="mode-control"><span>Accuracy</span><div className="segmented"><button className={mode === "fast" ? "active" : ""} onClick={() => setMode("fast")}>Fast</button><button className={mode === "max" ? "active" : ""} onClick={() => setMode("max")}>Max</button></div></div>
              <span className="mode-caption">{mode === "fast" ? "1 API call · SNR picks candidate" : "3–4 calls · consensus across candidates · about 3× cost"}</span>
              <Button className="run-button" onClick={() => void runPipeline()} disabled={busy}>{busy ? <><LoaderCircle className="spin" />Processing</> : <><Waves />Run pipeline</>}</Button>
            </section>
          </section>

          <section className="insight-layout">
            <section className="panel analysis-panel" id="section-analysis" aria-labelledby="analysis-heading">
              <div className="panel-heading"><div className="panel-title"><BarChart3 /><h2 id="analysis-heading">Speech analysis</h2></div><span className="subtle-note">{result ? "SERVICE RESULT" : "ILLUSTRATIVE UNTIL PROCESSED"}</span></div>
              <div className="analysis-body"><div className="analysis-visual"><div className="segmented analysis-tabs"><button className={analysisTab === "vad" ? "active" : ""} onClick={() => setAnalysisTab("vad")}>VAD</button><button className={analysisTab === "spectrum" ? "active" : ""} onClick={() => setAnalysisTab("spectrum")}>Spectrogram</button><button className={analysisTab === "floor" ? "active" : ""} onClick={() => setAnalysisTab("floor")}>Noise floor</button></div>
                {analysisTab === "vad" ? <div className="vad-chart"><div className="vad-track">{Array.from({ length: 18 }, (_, index) => <span key={index} className={[1, 2, 4, 5, 8, 9, 10, 13, 14, 16].includes(index) ? "speech" : ""} />)}</div><div className="chart-time"><span>0:00</span><span>0:04</span><span>0:08</span><span>0:12</span><span>0:14</span></div><div className="legend"><span><i className="speech-key" />Speech (VAD)</span><span><i className="silence-key" />Non-speech</span></div></div> : analysisTab === "spectrum" ? <div className="spectrum-chart" aria-label="Illustrative speech-band spectrum">{Array.from({ length: 36 }, (_, index) => <span key={index} style={{ height: `${20 + Math.abs(Math.sin(index * 1.73)) * 58 + Math.abs(Math.sin(index * 0.31)) * 20}%` }} />)}</div> : <div className="noise-floor-chart"><div><span>Speech frames</span><i className="floor-line speech-line" /></div><div><span>Non-speech floor</span><i className="floor-line noise-line" /></div><small>Estimates from silence frames after voice detection.</small></div>}
              </div><div className="metric-list"><Metric label="Estimated SNR" value={snr !== undefined ? `${snr.toFixed(1)} dB` : "—"} note={snr !== undefined ? "Measured by service" : "Run analysis"} /><Metric label="Noise floor" value={noiseFloor !== undefined ? `${noiseFloor.toFixed(1)} dB` : "—"} note="Non-speech frames" /><Metric label="Speech energy" value={speechEnergy !== undefined ? `${speechEnergy.toFixed(1)} dB` : "—"} note="Speech-band energy" /><div className="guard-metric"><ShieldCheck size={16} /><span>Speech guard</span><strong>{result?.analysis?.guard_status ?? "Unverified"}</strong></div></div></div>
            </section>

            <section className="panel transcript-panel" id="section-transcript" aria-labelledby="transcript-heading">
              <div className="panel-heading"><div className="panel-title"><FileAudio /><div><h2 id="transcript-heading">Transcript Studio</h2><span>Gnani Prisma · verbatim</span></div></div><div className="segmented transcript-tabs"><button className={transcriptTab === "compare" ? "active" : ""} onClick={() => setTranscriptTab("compare")}>Compare</button><button className={transcriptTab === "single" ? "active" : ""} onClick={() => setTranscriptTab("single")}>Selected</button></div></div>
              <div className={`transcript-cards ${transcriptTab === "single" ? "single" : ""}`}>
                {(transcriptTab === "single" ? [selected] : ["C0", selected].filter((value, index, array) => array.indexOf(value) === index)).map((id) => <article className="transcript-card" key={id}><div className="transcript-card-head"><strong>{id === "C0" ? "Original (C0)" : `${id} · ${trackDefs.find((track) => track.id === id)?.name}`}</strong><span>{result ? "Gnani Prisma" : "Sample text"}</span></div><p>{result?.candidates?.[id] ?? (id === "C0" ? result?.candidates?.["C0"] : undefined) ?? sampleTexts[id as TrackId]}</p>{!result && <small>Illustrative transcript · not generated from your clip</small>}</article>)}
              </div>
            </section>
          </section>

          <section className="bottom-layout">
            <section className="panel evaluation-panel" id="section-evaluation" aria-labelledby="evaluation-heading">
              <div className="panel-heading"><div className="panel-title"><Trophy /><div><h2 id="evaluation-heading">Evaluation lab</h2><span>Sample benchmark rows · replace with a labeled dev-set result</span></div></div><Button variant="ghost" size="icon" aria-label="About the evaluation data" title="Illustrative comparison only; no dev-set claim is made"><CircleHelp /></Button></div>
              <div className="eval-tabs segmented"><button className={evalTab === "comparison" ? "active" : ""} onClick={() => setEvalTab("comparison")}>Pipeline comparison</button><button className={evalTab === "ablation" ? "active" : ""} onClick={() => setEvalTab("ablation")}>Ablation</button><button className={evalTab === "snr" ? "active" : ""} onClick={() => setEvalTab("snr")}>SNR curve</button><button className={evalTab === "noise" ? "active" : ""} onClick={() => setEvalTab("noise")}>Noise type</button></div>
              <div className="evaluation-content"><div className="eval-table-wrap"><table className="eval-table"><thead><tr><th>ID</th><th>Pipeline → Prisma</th><th>WER ↓</th><th>CER ↓</th><th>Latency</th></tr></thead><tbody>{(evalTab === "comparison" ? [["A", "C0 · Original", "18.92%", "9.61%", "412 ms"], ["B", "C0 · Tuned VAD", "17.83%", "9.02%", "418 ms"], ["C", "C1 · Hygiene", "16.71%", "8.49%", "427 ms"], ["D", "C2 · Enhanced blend", "15.34%", "7.81%", "436 ms"], ["E", "C3 · DeepFilterNet", "16.27%", "8.36%", "455 ms"], ["F", "Consensus C0 + C1 + C2", "14.96%", "7.64%", "1,298 ms"]] : evalTab === "ablation" ? [["A", "Original input", "18.92%", "9.61%", "412 ms"], ["B", "+ VAD", "17.83%", "9.02%", "418 ms"], ["C", "+ Speech guard", "Pending", "Pending", "—"], ["D", "+ Candidate consensus", "Pending", "Pending", "—"]] : evalTab === "snr" ? [["1", "SNR < 0 dB", "Pending", "Pending", "—"], ["2", "SNR 0–5 dB", "Pending", "Pending", "—"], ["3", "SNR 5–10 dB", "Pending", "Pending", "—"], ["4", "SNR > 10 dB", "Pending", "Pending", "—"]] : [["1", "Traffic / babble", "Pending", "Pending", "—"], ["2", "Vehicle / road", "Pending", "Pending", "—"], ["3", "Fan / hum", "Pending", "Pending", "—"], ["4", "Channel / codec", "Pending", "Pending", "—"]]).map((row) => <tr key={row[0]} className={row[0] === "D" && evalTab === "comparison" ? "highlight-row" : ""}><td>{row[0]}</td>{row.slice(1).map((value) => <td key={value}>{value}</td>)}</tr>)}</tbody></table></div>
                <div className="chart-box"><div className="chart-box-heading">WER by pipeline <span>Illustrative · lower is better</span></div>{evalTab === "comparison" ? <div className="bar-chart">{[["A", 74], ["B", 69], ["C", 64], ["D", 59], ["E", 62], ["F", 56]].map(([label, height]) => <div className="bar-column" key={label}><span>{label === "D" ? "15.34" : label === "F" ? "14.96" : "18.9"}%</span><i className={label === "D" || label === "F" ? "best" : ""} style={{ height: `${height}%` }} /><small>{label}</small></div>)}</div> : <div className="chart-empty"><BarChart3 /><span>Awaiting labeled dev-set measurements</span></div>}</div></div>
              {result?.evaluation && <div className="service-evaluation">Service result: WER {result.evaluation.wer ?? "not reported"} · CER {result.evaluation.cer ?? "not reported"} · {result.evaluation.calls ?? profile.providerCalls.max} API call(s). {result.evaluation.note ?? "Metrics supplied by the configured service."}</div>}
            </section>

            <section className="panel live-panel" id="section-live" aria-labelledby="live-heading">
              <div className="panel-heading"><div className="panel-title"><Radio /><h2 id="live-heading">Live monitor <span className="demo-pill">DEMO</span></h2></div><span className={`run-indicator ${liveState === "live" ? "running" : ""}`}><span />{liveState === "live" ? `Live · ${formatTime(liveSeconds)}` : liveState === "connecting" ? "Connecting" : "Ready"}</span></div>
              <p className="live-description">{apiUrl ? "Microphone stream via your audio proxy." : "Live preview only · no audio is processed here."}</p>
              <Button className="live-button" onClick={() => void toggleLive()} disabled={liveState === "connecting"}>{liveState === "live" ? <><Pause />Stop live monitor</> : liveState === "connecting" ? <><LoaderCircle className="spin" />Connecting</> : <><Play />Start live demo</>}</Button>
              <div className="live-metrics"><div><Clock3 /><span>Latency</span><strong>{liveState === "live" && apiUrl ? "Streaming" : "—"}</strong></div><div><Activity /><span>SNR</span><strong>{liveState === "live" && snr !== undefined ? `${snr.toFixed(1)} dB` : "—"}</strong></div><div><FileAudio /><span>Transcript</span><strong>{liveState === "live" && result ? "Receiving" : "—"}</strong></div></div>
              <small className="live-note">8 kHz mono · 1,024-byte PCM16 frames · 64 ms cadence · telephony VAD · cheap candidates only</small>
            </section>
          </section>

          <section className="panel history-panel" id="section-history"><div className="panel-heading"><div className="panel-title"><History /><h2>Experiment history</h2></div><Button variant="ghost" size="sm" onClick={() => setHistoryItems([])} disabled={historyItems.length === 0}><RotateCcw /> Clear</Button></div>{historyItems.length === 0 ? <p>No completed service runs in this browser session.</p> : <div className="history-list">{historyItems.map((item) => <div key={item}><Check />{item}</div>)}</div>}</section>
          <footer className="workspace-footer"><span>BharatNoise · Audio workstation</span><span>{result ? "Service response available" : "No verified performance claims"}</span><Button variant="ghost" size="sm" onClick={() => setSettingsOpen(true)}><SlidersHorizontal /> Service setup</Button></footer>
        </div>
      </div>

      {settingsOpen && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setSettingsOpen(false); }}><section className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title"><div className="settings-heading"><div><span className="eyebrow">AUDIO SERVICE</span><h2 id="settings-title">Connect your proxy</h2><p>Use a FastAPI or Flask proxy that forwards telephony audio to Gnani AI Prisma.</p></div><Button variant="ghost" size="icon" onClick={() => setSettingsOpen(false)} aria-label="Close service settings"><X /></Button></div><label className="settings-label" htmlFor="proxy-url">Proxy base address</label><input id="proxy-url" className="settings-input" placeholder="https://your-service.example.com" value={apiUrl} onChange={(event) => setApiUrl(event.currentTarget.value)} autoFocus /><div className="settings-hint"><span>Expected routes</span><code>GET /health</code><code>POST /process</code><code>WS /live</code></div><div className="settings-hint"><span>Processing contract</span><small>POST /process accepts multipart fields file, mode, sample_rate=8000, channels=1, sample_format=pcm_s16le, asr_provider=gnani_prisma, candidates, compare_models. Return JSON with selected_candidate, candidates, analysis, evaluation, and pipeline. Max mode uses candidate/model consensus; calculate and return the measured WER/guard correlation from your labeled dev set.</small></div><div className="settings-hint"><span>Live socket</span><small>WS /live receives JSON configuration followed by exactly 1,024-byte PCM16 frames every 64 ms. Set x-sample-rate: 8000 and the telephony VAD preset in the proxy connection.</small></div><div className="settings-actions"><Button variant="outline" onClick={() => void testConnection()} disabled={checkingConnection || !apiUrl}>{checkingConnection ? <LoaderCircle className="spin" /> : <Activity />}Test connection</Button><Button onClick={() => void saveProxy()}>Save service address</Button></div><p className="privacy-note">Your service address stays in this browser. Audio is sent only after you connect a service and run processing.</p></section></div>}
    </main>
  );
}

function NavItem({ icon, label, active, onClick }: { icon: ReactNode; label: string; active: boolean; onClick: () => void }) {
  return <button className={`nav-item ${active ? "active" : ""}`} onClick={onClick}>{icon}<span>{label}</span></button>;
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong><small>{note}</small></div>;
}

function PhoneGlyph() {
  return <Headphones size={15} aria-hidden="true" />;
}