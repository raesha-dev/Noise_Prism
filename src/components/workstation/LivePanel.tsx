import { Activity, Clock3, LoaderCircle, Pause, Play, Radio } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  LIVE_CANDIDATES,
  LIVE_FRAME_BYTES,
  LIVE_FRAME_INTERVAL_MS,
  LIVE_SAMPLE_RATE,
} from "@/lib/pcm-framer";

export type LiveView = {
  state: "ready" | "connecting" | "live";
  levelDb: number;
  latencyMs?: number | undefined;
  snrDb?: number | undefined;
  finals: string[];
  partial: string;
};

type Props = {
  view: LiveView;
  onToggle: () => void;
  canRun: boolean;
  unavailableMessage: string;
  mockMode: boolean;
};

export function LivePanel({ view, onToggle, canRun, unavailableMessage, mockMode }: Props) {
  const levelPct = Math.max(0, Math.min(100, ((view.levelDb + 70) / 70) * 100));
  const live = view.state === "live";
  return (
    <section className="panel live-panel" id="section-live" aria-labelledby="live-heading">
      <div className="panel-heading">
        <div className="panel-title">
          <Radio />
          <h2 id="live-heading">Live monitor</h2>
        </div>
        <span className={`run-indicator ${live ? "running" : ""}`} aria-live="polite">
          <span />
          {live ? "Live" : view.state === "connecting" ? "Connecting" : "Ready"}
        </span>
      </div>
      <p className="live-description">
        Streams microphone audio through the backend proxy to the configured speech service.
      </p>
      {!canRun && (
        <p className="chart-empty" role="status">
          {unavailableMessage}
        </p>
      )}
      {mockMode && (
        <p className="chart-empty" role="status">
          Mock streaming does not provide real Prisma speech recognition.
        </p>
      )}
      <Button
        className="live-button"
        onClick={onToggle}
        disabled={!canRun || view.state === "connecting"}
      >
        {live ? (
          <>
            <Pause />
            Stop live monitor
          </>
        ) : view.state === "connecting" ? (
          <>
            <LoaderCircle className="spin" />
            Connecting
          </>
        ) : (
          <>
            <Play />
            Start live monitor
          </>
        )}
      </Button>
      <div
        className="level-meter"
        role="meter"
        aria-label="Input level"
        aria-valuemin={-70}
        aria-valuemax={0}
        aria-valuenow={Math.round(view.levelDb)}
      >
        <i style={{ width: `${live ? levelPct : 0}%` }} />
      </div>
      <div className="live-metrics">
        <div>
          <Clock3 />
          <span>Latency</span>
          <strong>
            {live && view.latencyMs !== undefined ? `${Math.round(view.latencyMs)} ms` : "—"}
          </strong>
        </div>
        <div>
          <Activity />
          <span>SNR</span>
          <strong>{live && view.snrDb !== undefined ? `${view.snrDb.toFixed(1)} dB` : "—"}</strong>
        </div>
      </div>
      <div className="live-transcript" aria-live="polite">
        {view.finals.length === 0 && !view.partial ? (
          <span className="live-empty">Transcript appears here while streaming.</span>
        ) : null}
        {view.finals.slice(-3).map((line, i) => (
          <p key={i}>{line}</p>
        ))}
        {view.partial && <p className="live-partial">{view.partial}</p>}
      </div>
      <small className="live-note">
        {LIVE_SAMPLE_RATE / 1000} kHz mono · {LIVE_FRAME_BYTES.toLocaleString()}-byte PCM16 frames ·{" "}
        {LIVE_FRAME_INTERVAL_MS} ms cadence · x-sample-rate: {LIVE_SAMPLE_RATE} · telephony VAD ·
        candidates {LIVE_CANDIDATES.join(", ")} only
      </small>
    </section>
  );
}
