import { Check, Download, Layers3, LoaderCircle, Square, Waves } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import type { AccuracyMode, ProcessingResult, TrackId } from "@/lib/bharatnoise-api";
import { getExecutionProfile } from "@/lib/processing-contract";
import { Segmented, formatDb, stageNames, trackName } from "./shared";

type Props = {
  busy: boolean;
  stage: number;
  result: ProcessingResult | null;
  resultSource: "service" | "demo" | null;
  routedCandidate: TrackId | null;
  routeNote: string;
  mode: AccuracyMode;
  onMode: (mode: AccuracyMode) => void;
  includeC3: boolean;
  onIncludeC3: (value: boolean) => void;
  compareModels: boolean;
  onCompareModels: (value: boolean) => void;
  connected: boolean;
  onRun: () => void;
  onCancel: () => void;
  onDownload: () => void;
  snrDb?: number | undefined;
};

export function PipelinePanel(props: Props) {
  const profile = getExecutionProfile(props.mode);
  const candidates = props.includeC3 ? "C0, C1, C2, C3" : "C0, C1, C2";
  const stageDetail = (index: number) => {
    if (index === 0) return "Resample to 8 kHz · mono · PCM16";
    if (index === 1) return props.snrDb !== undefined && props.stage >= 1 ? `SNR ${formatDb(props.snrDb)}` : "Telephony VAD + SNR";
    if (index === 2) return candidates;
    if (index === 3) return props.result?.analysis.guardStatus ?? (props.stage >= 3 ? (props.resultSource === "demo" ? "Demo · not measured" : "Checked") : "Rejects candidates that erase speech");
    return props.mode === "fast" ? "SNR router · 1 Prisma call" : `Consensus · ${profile.providerCalls.min}–${profile.providerCalls.max} Prisma calls`;
  };

  return (
    <section className="panel pipeline-panel" id="section-pipeline" aria-labelledby="pipeline-heading">
      <div className="panel-heading">
        <div className="panel-title"><Layers3 /><h2 id="pipeline-heading">Enhancement pipeline</h2></div>
        <span className={`run-indicator ${props.busy ? "running" : ""}`} aria-live="polite"><span />{props.busy ? "Working" : props.resultSource === "service" ? "Complete" : props.resultSource === "demo" ? "Demo run" : "Ready"}</span>
      </div>
      <ol className="stage-list">
        {stageNames.map((name, index) => {
          const done = props.stage > index || (!props.busy && props.stage >= index);
          const active = props.busy && props.stage === index;
          return (
            <li className={`stage-item ${active ? "active" : ""}`} key={name}>
              <span className={`stage-check ${done ? "done" : ""}`}>{active ? <LoaderCircle size={12} className="spin" /> : done ? <Check size={13} /> : String(index + 1)}</span>
              <span>{name}</span>
              <small>{stageDetail(index)}</small>
            </li>
          );
        })}
      </ol>
      <div className="selected-route">
        <span>ASR candidate</span>
        <span className="candidate-badge">{props.routedCandidate ? `${props.routedCandidate} · ${trackName(props.routedCandidate)}` : "Not routed yet"}</span>
      </div>
      {props.routeNote && <p className="route-note">{props.routeNote}</p>}
      <div className="mode-control">
        <span>Accuracy</span>
        <Segmented label="Accuracy mode" value={props.mode} onChange={props.onMode} options={[{ value: "fast", label: "Fast" }, { value: "max", label: "Max" }]} />
      </div>
      <span className="mode-caption">{props.mode === "fast" ? "1 API call · SNR router picks one candidate" : `${profile.label} · consensus across candidates`}</span>
      <div className="switch-row">
        <label htmlFor="include-c3">Include C3 DeepFilterNet</label>
        <Switch id="include-c3" checked={props.includeC3} onCheckedChange={props.onIncludeC3} />
      </div>
      <div className="switch-row" aria-disabled={props.mode !== "max"}>
        <label htmlFor="compare-models">Compare Prisma models</label>
        <Switch id="compare-models" checked={props.compareModels && props.mode === "max"} disabled={props.mode !== "max"} onCheckedChange={props.onCompareModels} />
      </div>
      {props.busy ? (
        <Button variant="outline" className="run-button" onClick={props.onCancel}><Square />Cancel</Button>
      ) : (
        <Button className="run-button" onClick={props.onRun}><Waves />{props.connected ? "Run pipeline" : "Run demo pipeline"}</Button>
      )}
      {props.result && (
        <Button variant="ghost" size="sm" className="download-button" onClick={props.onDownload}><Download />Download result JSON</Button>
      )}
    </section>
  );
}
