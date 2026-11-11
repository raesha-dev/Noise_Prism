import { BarChart3, ShieldCheck } from "lucide-react";
import { Metric, Segmented, SourceTag, formatDb, formatTime } from "./shared";

export type AnalysisView = {
  source: "service" | "estimate" | "sample";
  snrDb?: number | undefined;
  noiseFloorDb?: number | undefined;
  speechEnergyDb?: number | undefined;
  vadBins: boolean[];
  spectrum: number[];
  guardStatus?: string | undefined;
  guardScore?: number | undefined;
  duration: number;
};

type Tab = "vad" | "spectrum" | "floor";

export function AnalysisPanel({ view, tab, onTab }: { view: AnalysisView; tab: Tab; onTab: (tab: Tab) => void }) {
  const note = view.source === "service" ? "Measured by service" : view.source === "estimate" ? "Browser energy estimate" : "Add a clip";
  const floorPct = view.noiseFloorDb !== undefined ? Math.max(4, Math.min(100, 100 + view.noiseFloorDb)) : 30;
  const speechPct = view.speechEnergyDb !== undefined ? Math.max(4, Math.min(100, 100 + view.speechEnergyDb)) : 70;
  return (
    <section className="panel analysis-panel" id="section-analysis" aria-labelledby="analysis-heading">
      <div className="panel-heading">
        <div className="panel-title"><BarChart3 /><h2 id="analysis-heading">Speech analysis</h2></div>
        <SourceTag kind={view.source} />
      </div>
      <div className="analysis-body">
        <div className="analysis-visual">
          <Segmented className="analysis-tabs" label="Analysis view" value={tab} onChange={onTab} options={[{ value: "vad", label: "VAD" }, { value: "spectrum", label: "Spectrum" }, { value: "floor", label: "Noise floor" }]} />
          {tab === "vad" ? (
            <div className="vad-chart">
              <div className="vad-track" role="img" aria-label={`Voice activity: ${view.vadBins.filter(Boolean).length} of ${view.vadBins.length} segments contain speech`}>
                {view.vadBins.map((speech, index) => <span key={index} className={speech ? "speech" : ""} />)}
              </div>
              <div className="chart-time">{[0, 0.25, 0.5, 0.75, 1].map((f) => <span key={f}>{formatTime(f * view.duration)}</span>)}</div>
              <div className="legend"><span><i className="speech-key" />Speech (VAD)</span><span><i className="silence-key" />Non-speech</span></div>
            </div>
          ) : tab === "spectrum" ? (
            <div className="spectrum-wrap">
              <div className="spectrum-chart" role="img" aria-label="Average spectrum from 0 to 4 kHz">
                {view.spectrum.map((value, index) => <span key={index} style={{ height: `${Math.max(3, value * 100)}%` }} />)}
              </div>
              <div className="chart-time"><span>0 Hz</span><span>1 kHz</span><span>2 kHz</span><span>3 kHz</span><span>4 kHz</span></div>
            </div>
          ) : (
            <div className="noise-floor-chart">
              <div><span>Speech frames</span><i className="floor-line speech-line" style={{ maxWidth: `${speechPct}%` }} /><b>{formatDb(view.speechEnergyDb)}</b></div>
              <div><span>Non-speech floor</span><i className="floor-line noise-line" style={{ maxWidth: `${floorPct}%` }} /><b>{formatDb(view.noiseFloorDb)}</b></div>
              <small>Levels in dBFS. The floor is estimated from frames the VAD marks as non-speech.</small>
            </div>
          )}
        </div>
        <div className="metric-list">
          <Metric label="Estimated SNR" value={formatDb(view.snrDb)} note={note} />
          <Metric label="Noise floor" value={formatDb(view.noiseFloorDb)} note="Non-speech frames" />
          <Metric label="Speech energy" value={formatDb(view.speechEnergyDb)} note="Speech frames" />
          <div className="guard-metric">
            <ShieldCheck size={16} />
            <span>Speech guard</span>
            <strong>{view.guardStatus ?? "Unverified"}{view.guardScore !== undefined ? ` · ${view.guardScore.toFixed(2)}` : ""}</strong>
          </div>
        </div>
      </div>
    </section>
  );
}
