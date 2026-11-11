import { Activity, Clock3, LoaderCircle, Pause, Play, Radio, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LIVE_CANDIDATES, LIVE_FRAME_BYTES, LIVE_FRAME_INTERVAL_MS, LIVE_SAMPLE_RATE } from "@/lib/pcm-framer";
import { formatTime } from "./shared";

export type LiveView = {
  state: "ready" | "connecting" | "live";
  kind: "simulated" | "proxy";
  seconds: number;
  framesSent: number;
  droppedFrames: number;
  levelDb: number;
  latencyMs?: number | undefined;
  snrDb?: number | undefined;
  finals: string[];
  partial: string;
};

export function LivePanel({ view, onToggle }: { view: LiveView; onToggle: () => void }) {
  const levelPct = Math.max(0, Math.min(100, ((view.levelDb + 70) / 70) * 100));
  const live = view.state === "live";
  return (
    <section className="panel live-panel" id="section-live" aria-labelledby="live-heading">
      <div className="panel-heading">
        <div className="panel-title"><Radio /><h2 id="live-heading">Live monitor <span className="demo-pill">DEMO ONLY</span></h2></div>
        <span className={`run-indicator ${live ? "running" : ""}`} aria-live="polite"><span />{live ? `Live · ${formatTime(view.seconds)}` : view.state === "connecting" ? "Connecting" : "Ready"}</span>
      </div>
      <p className="live-description">
        {view.kind === "proxy" ? "Streams your microphone to the proxy's WS /live route. Not for production traffic." : "Simulated stream · no microphone, no audio leaves this page. Connect a proxy to stream for real."}
      </p>
      <Button className="live-button" onClick={onToggle} disabled={view.state === "connecting"}>
        {live ? <><Pause />Stop live monitor</> : view.state === "connecting" ? <><LoaderCircle className="spin" />Connecting</> : <><Play />{view.kind === "proxy" ? "Start live demo" : "Start simulated demo"}</>}
      </Button>
      <div className="level-meter" role="meter" aria-label="Input level" aria-valuemin={-70} aria-valuemax={0} aria-valuenow={Math.round(view.levelDb)}>
        <i style={{ width: `${live ? levelPct : 0}%` }} />
      </div>
      <div className="live-metrics">
        <div><Send /><span>Frames sent</span><strong>{view.framesSent.toLocaleString()}</strong></div>
        <div><Clock3 /><span>Latency</span><strong>{live && view.latencyMs !== undefined ? `${Math.round(view.latencyMs)} ms` : "—"}</strong></div>
        <div><Activity /><span>SNR</span><strong>{live && view.snrDb !== undefined ? `${view.snrDb.toFixed(1)} dB` : "—"}</strong></div>
      </div>
      <div className="live-transcript" aria-live="polite">
        {view.finals.length === 0 && !view.partial ? <span className="live-empty">Transcript appears here while streaming.</span> : null}
        {view.finals.slice(-3).map((line, i) => <p key={i}>{line}</p>)}
        {view.partial && <p className="live-partial">{view.partial}</p>}
      </div>
      <small className="live-note">
        {LIVE_SAMPLE_RATE / 1000} kHz mono · {LIVE_FRAME_BYTES.toLocaleString()}-byte PCM16 frames · {LIVE_FRAME_INTERVAL_MS} ms cadence · x-sample-rate: {LIVE_SAMPLE_RATE} · telephony VAD · candidates {LIVE_CANDIDATES.join(", ")} only
        {view.droppedFrames > 0 ? ` · ${view.droppedFrames} frames dropped (buffer full)` : ""}
        {view.kind === "simulated" ? " · latency/SNR are not simulated" : " · latency is reported by the proxy"}
      </small>
    </section>
  );
}
