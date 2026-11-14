import { Check, Download, Layers3, LoaderCircle, Square, Waves } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import type {
  AccuracyMode,
  LanguageCode,
  OutputFormat,
  ProcessingResult,
  TrackId,
} from "@/lib/bharatnoise-api";
import { Segmented } from "./shared";
import { formatDb, stageNames, trackName } from "./workstation-utils";

type Props = {
  busy: boolean;
  stage: number;
  result: ProcessingResult | null;
  resultSource: "service" | null;
  routedCandidate: TrackId | null;
  routeNote: string;
  mode: AccuracyMode;
  onMode: (mode: AccuracyMode) => void;
  model: string;
  languageCode: LanguageCode;
  onLanguageCode: (language: LanguageCode) => void;
  outputFormat: OutputFormat;
  onOutputFormat: (format: OutputFormat) => void;
  includeC3: boolean;
  onIncludeC3: (value: boolean) => void;
  includeResearchCandidates: boolean;
  onIncludeResearchCandidates: (value: boolean) => void;
  oaLambda: number;
  oaSweep: number[];
  imcraFitted: boolean | null;
  imcraConfigError: string;
  onOaLambda: (value: number) => void;
  compareModels: boolean;
  onCompareModels: (value: boolean) => void;
  canRun: boolean;
  unavailableMessage: string;
  onRun: () => void;
  onCancel: () => void;
  onDownload: () => void;
  snrDb?: number | undefined;
};

export function PipelinePanel(props: Props) {
  const candidates = [
    "C0",
    "C1",
    "C2",
    ...(props.includeResearchCandidates ? ["C2B", "C2C"] : []),
    ...(props.includeC3 ? ["C3"] : []),
  ].join(", ");
  const stageDetail = (index: number) => {
    if (index === 0) return "Resample to 8 kHz · mono · PCM16";
    if (index === 1)
      return props.snrDb !== undefined && props.stage >= 1
        ? `SNR ${formatDb(props.snrDb)}`
        : "Telephony VAD + SNR";
    if (index === 2) return candidates;
    if (index === 3)
      return (
        props.result?.analysis.guardStatus ??
        (props.stage >= 3 ? "Waiting for service result" : "Rejects candidates that erase speech")
      );
    return props.mode === "fast"
      ? props.routeNote || "Baseline-only route (C0)"
      : "Consensus across candidates";
  };

  return (
    <section
      className="panel pipeline-panel"
      id="section-pipeline"
      aria-labelledby="pipeline-heading"
    >
      <div className="panel-heading">
        <div className="panel-title">
          <Layers3 />
          <h2 id="pipeline-heading">Enhancement pipeline</h2>
        </div>
        <span className={`run-indicator ${props.busy ? "running" : ""}`} aria-live="polite">
          <span />
          {props.busy ? "Working" : props.resultSource === "service" ? "Complete" : "Ready"}
        </span>
      </div>
      {!props.canRun && (
        <p className="chart-empty" role="status">
          {props.unavailableMessage}
        </p>
      )}
      <ol className="stage-list">
        {stageNames.map((name, index) => {
          const done = props.stage > index || (!props.busy && props.stage >= index);
          const active = props.busy && props.stage === index;
          return (
            <li className={`stage-item ${active ? "active" : ""}`} key={name}>
              <span className={`stage-check ${done ? "done" : ""}`}>
                {active ? (
                  <LoaderCircle size={12} className="spin" />
                ) : done ? (
                  <Check size={13} />
                ) : (
                  String(index + 1)
                )}
              </span>
              <span>{name}</span>
              <small>{stageDetail(index)}</small>
            </li>
          );
        })}
      </ol>
      <div className="selected-route">
        <span>ASR candidate</span>
        <span className="candidate-badge">
          {props.routedCandidate
            ? `${props.routedCandidate} · ${trackName(props.routedCandidate)}`
            : "Not routed yet"}
        </span>
      </div>
      {props.routeNote && <p className="route-note">{props.routeNote}</p>}
      <div className="mode-control">
        <span>Accuracy</span>
        <Segmented
          label="Accuracy mode"
          value={props.mode}
          onChange={props.onMode}
          options={[
            { value: "fast", label: "Fast" },
            { value: "max", label: "Max" },
          ]}
          disabled={!props.canRun || props.busy}
        />
      </div>
      <span className="mode-caption">
        {props.mode === "fast"
          ? props.routeNote || "Baseline-only route (C0)"
          : "Consensus across requested candidates"}
      </span>
      <label className="pipeline-select">
        <span>Recognition language</span>
        <select
          aria-label="Recognition language"
          value={props.languageCode}
          disabled={!props.canRun || props.busy}
          onChange={(event) => props.onLanguageCode(event.currentTarget.value as LanguageCode)}
        >
          <option value="indic-auto" disabled={props.model !== "prisma-v3.0"}>
            Auto-detect spoken language · Prisma v3.0
          </option>
          <option value="hi-IN">Hindi</option>
          <option value="kn-IN">Kannada</option>
          <option value="en-IN">English (India)</option>
          <option value="ta-IN">Tamil</option>
          <option value="te-IN">Telugu</option>
          <option value="bn-IN">Bengali</option>
          <option value="gu-IN">Gujarati</option>
          <option value="ml-IN">Malayalam</option>
          <option value="mr-IN">Marathi</option>
          <option value="pa-IN">Punjabi</option>
        </select>
      </label>
      <label className="pipeline-select">
        <span>Transcript format</span>
        <select
          aria-label="Transcript format"
          value={props.outputFormat}
          disabled={!props.canRun || props.busy}
          onChange={(event) => props.onOutputFormat(event.currentTarget.value as OutputFormat)}
        >
          <option value="auto">Automatic · Gnani default</option>
          <option value="verbatim">Verbatim · spoken form</option>
          <option value="transcribe">Transcribe · numbers as digits</option>
          <option value="codemix" disabled={props.model !== "prisma-v3.0"}>
            Codemix · English words in Latin script
          </option>
          <option value="transliterate" disabled={props.model !== "prisma-v3.0"}>
            Transliterate · all text in Latin script
          </option>
        </select>
      </label>
      <small className="mode-caption">
        {props.languageCode === "indic-auto"
          ? "Prisma v3.0 detects the spoken language. Automatic format defaults to verbatim."
          : props.outputFormat === "auto"
            ? "Automatic uses Gnani's model/language default; it does not select a format from the detected script."
            : "Format is sent to Gnani with each transcription request."}
      </small>
      <div className="switch-row">
        <label htmlFor="include-research-candidates">
          Include experimental spectral and voice-focus candidates
        </label>
        <Switch
          id="include-research-candidates"
          checked={props.includeResearchCandidates}
          disabled={!props.canRun || props.busy || props.oaSweep.length === 0}
          onCheckedChange={props.onIncludeResearchCandidates}
        />
      </div>
      {props.imcraConfigError && (
        <p className="chart-empty" role="status">
          {props.imcraConfigError}
        </p>
      )}
      {props.includeResearchCandidates && (
        <>
          <label className="pipeline-select">
            <span>Observation Addition λ</span>
            <select
              aria-label="Observation Addition lambda"
              value={props.oaLambda}
              disabled={!props.canRun || props.busy || props.oaSweep.length === 0}
              onChange={(event) => props.onOaLambda(Number(event.currentTarget.value))}
            >
              {props.oaSweep.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
            <small>
              {props.imcraConfigError ||
                `C2B applies the adaptive spectral filter, attenuates VAD non-speech, and levels speech. C2C adds λ × original, so some noise is reintroduced. Parameters are ${props.imcraFitted ? "fitted" : "unfitted"}.`}
            </small>
          </label>
          <small className="mode-caption">
            Fast mode transcribes only its routed candidate (C0 while the router is unfitted). Use
            Max to request transcripts for C2B and C2C in the same run.
          </small>
        </>
      )}
      <div className="switch-row">
        <label htmlFor="include-c3">Include C3 DeepFilterNet</label>
        <Switch
          id="include-c3"
          checked={props.includeC3}
          disabled={!props.canRun || props.busy}
          onCheckedChange={props.onIncludeC3}
        />
      </div>
      <div className="switch-row" aria-disabled={props.mode !== "max"}>
        <label htmlFor="compare-models">Compare Prisma models</label>
        <Switch
          id="compare-models"
          checked={props.compareModels && props.mode === "max"}
          disabled={
            !props.canRun ||
            props.busy ||
            props.mode !== "max" ||
            props.languageCode === "indic-auto"
          }
          onCheckedChange={props.onCompareModels}
        />
      </div>
      {props.busy ? (
        <Button variant="outline" className="run-button" onClick={props.onCancel}>
          <Square />
          Cancel
        </Button>
      ) : (
        <Button className="run-button" onClick={props.onRun} disabled={!props.canRun}>
          <Waves />
          Run pipeline
        </Button>
      )}
      {props.result && (
        <Button variant="ghost" size="sm" className="download-button" onClick={props.onDownload}>
          <Download />
          Download result JSON
        </Button>
      )}
    </section>
  );
}
