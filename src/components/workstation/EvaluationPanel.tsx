import { useRef, useState, type ChangeEvent } from "react";
import { BarChart3, CloudDownload, FileDown, FileUp, LoaderCircle, RotateCcw, Trophy } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { fetchEvaluation, parseEvaluationReport, type EvaluationReport, type EvaluationRow, type EvaluationSection, type ProcessingResult } from "@/lib/bharatnoise-api";
import { formatPercent } from "@/lib/metrics";
import { Segmented, SourceTag, downloadBlob } from "./shared";

export const SAMPLE_REPORT: EvaluationReport = {
  dataset: "Illustrative sample",
  sections: {
    comparison: [
      { id: "A", pipeline: "C0 · Original", candidate: "C0", wer: 0.1892, cer: 0.0961, latencyMs: 412 },
      { id: "B", pipeline: "C0 · Tuned VAD", candidate: "C0", wer: 0.1783, cer: 0.0902, latencyMs: 418 },
      { id: "C", pipeline: "C1 · Hygiene", candidate: "C1", wer: 0.1671, cer: 0.0849, latencyMs: 427 },
      { id: "D", pipeline: "C2 · Enhanced blend", candidate: "C2", wer: 0.1534, cer: 0.0781, latencyMs: 436 },
      { id: "E", pipeline: "C3 · DeepFilterNet", candidate: "C3", wer: 0.1627, cer: 0.0836, latencyMs: 455 },
      { id: "F", pipeline: "Consensus C0 + C1 + C2", candidate: "consensus", wer: 0.1496, cer: 0.0764, latencyMs: 1298 },
    ],
    ablation: [
      { id: "A", pipeline: "Original input", wer: 0.1892, cer: 0.0961, latencyMs: 412 },
      { id: "B", pipeline: "+ Telephony VAD", wer: 0.1783, cer: 0.0902, latencyMs: 418 },
      { id: "C", pipeline: "+ Speech guard" },
      { id: "D", pipeline: "+ Candidate consensus" },
    ],
    snr: [
      { id: "1", pipeline: "SNR < 0 dB" }, { id: "2", pipeline: "SNR 0–5 dB" }, { id: "3", pipeline: "SNR 5–10 dB" }, { id: "4", pipeline: "SNR > 10 dB" },
    ],
    noise: [
      { id: "1", pipeline: "Traffic / babble" }, { id: "2", pipeline: "Vehicle / road" }, { id: "3", pipeline: "Fan / hum" }, { id: "4", pipeline: "Channel / codec" },
    ],
  },
};

const SECTION_LABELS: Record<EvaluationSection, string> = { comparison: "Pipeline comparison", ablation: "Ablation", snr: "SNR buckets", noise: "Noise type" };

type Props = { report: EvaluationReport | null; onReport: (report: EvaluationReport | null, source: "service" | "imported" | null) => void; reportSource: "service" | "imported" | null; apiUrl: string; lastResult: ProcessingResult | null };

export function bestRow(rows: EvaluationRow[]) {
  return rows.filter((row) => row.wer !== undefined).sort((a, b) => (a.wer ?? 1) - (b.wer ?? 1))[0];
}

export function EvaluationPanel({ report, onReport, reportSource, apiUrl, lastResult }: Props) {
  const [section, setSection] = useState<EvaluationSection>("comparison");
  const [loading, setLoading] = useState(false);
  const importRef = useRef<HTMLInputElement | null>(null);
  const active = report ?? SAMPLE_REPORT;
  const isSample = !report;
  const rows = active.sections[section] ?? [];
  const best = bestRow(rows);
  const baseline = rows.find((row) => row.candidate === "C0") ?? rows[0];
  const maxWer = Math.max(0.0001, ...rows.map((row) => row.wer ?? 0));
  const c0Wins = !isSample && section === "comparison" && best && (best.candidate === "C0" || best.id === baseline?.id);

  async function load() {
    setLoading(true);
    try {
      onReport(await fetchEvaluation(apiUrl), "service");
      toast.success("Evaluation report loaded from your service.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not load the evaluation report.");
    } finally {
      setLoading(false);
    }
  }

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    try {
      onReport(parseEvaluationReport(JSON.parse(await file.text())), "imported");
      toast.success(`${file.name} imported.`);
    } catch (error) {
      toast.error(error instanceof SyntaxError ? "That file is not valid JSON." : error instanceof Error ? error.message : "Import failed.");
    }
  }

  function exportCsv() {
    const lines = ["section,id,pipeline,wer,cer,latency_ms"];
    for (const [name, list] of Object.entries(active.sections)) {
      for (const row of list ?? []) lines.push([name, row.id, `"${row.pipeline.replace(/"/g, '""')}"`, row.wer ?? "", row.cer ?? "", row.latencyMs ?? ""].join(","));
    }
    downloadBlob(new Blob([lines.join("\n")], { type: "text/csv" }), isSample ? "bharatnoise-evaluation-SAMPLE.csv" : "bharatnoise-evaluation.csv");
  }

  return (
    <section className="panel evaluation-panel" id="section-evaluation" aria-labelledby="evaluation-heading">
      <div className="panel-heading">
        <div className="panel-title">
          <Trophy />
          <div>
            <h2 id="evaluation-heading">Evaluation lab</h2>
            <span>{isSample ? "Sample rows · load your labeled dev-set results" : `${active.dataset ?? "Dev set"}${active.clipCount ? ` · ${active.clipCount} clips` : ""}${active.measuredAt ? ` · ${active.measuredAt}` : ""}`}</span>
          </div>
        </div>
        <SourceTag kind={isSample ? "sample" : reportSource === "service" ? "service" : "imported"} />
      </div>
      <div className="eval-toolbar">
        <Segmented className="eval-tabs" label="Evaluation section" value={section} onChange={setSection} options={(Object.keys(SECTION_LABELS) as EvaluationSection[]).map((value) => ({ value, label: SECTION_LABELS[value] }))} />
        <div className="eval-actions">
          <input ref={importRef} type="file" accept="application/json,.json" className="visually-hidden" onChange={(event) => void importFile(event)} aria-label="Import evaluation JSON" tabIndex={-1} />
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={!apiUrl || loading} title={apiUrl ? "GET /evaluation" : "Connect a service first"}>{loading ? <LoaderCircle className="spin" /> : <CloudDownload />}Load from service</Button>
          <Button variant="outline" size="sm" onClick={() => importRef.current?.click()}><FileUp />Import</Button>
          <Button variant="outline" size="sm" onClick={exportCsv}><FileDown />CSV</Button>
          {!isSample && <Button variant="ghost" size="sm" onClick={() => onReport(null, null)} aria-label="Return to sample rows"><RotateCcw /></Button>}
        </div>
      </div>
      {c0Wins && (
        <p className="honesty-banner">
          Nothing beats C0 on this set. Report what you measured: how much aggressive denoising hurts Prisma, and how much the guard and consensus recover.
        </p>
      )}
      <div className="evaluation-content">
        <div className="eval-table-wrap">
          <table className="eval-table">
            <thead><tr><th>ID</th><th>Pipeline → Prisma</th><th>WER ↓</th><th>CER ↓</th><th>Latency</th>{section === "comparison" && <th>Δ vs C0</th>}</tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className={best && row.id === best.id ? "highlight-row" : ""}>
                  <td>{row.id}</td>
                  <td>{row.pipeline}</td>
                  <td>{row.wer === undefined ? "Pending" : formatPercent(row.wer)}</td>
                  <td>{row.cer === undefined ? "Pending" : formatPercent(row.cer)}</td>
                  <td>{row.latencyMs === undefined ? "—" : `${Math.round(row.latencyMs).toLocaleString()} ms`}</td>
                  {section === "comparison" && <td>{row.wer !== undefined && baseline?.wer !== undefined && row.id !== baseline.id ? `${((row.wer - baseline.wer) * 100).toFixed(2)} pts` : "—"}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="chart-box">
          <div className="chart-box-heading">WER by row <span>{isSample ? "Illustrative · lower is better" : "Lower is better"}</span></div>
          {rows.some((row) => row.wer !== undefined) ? (
            <div className="bar-chart" role="img" aria-label="Word error rate by pipeline">
              {rows.map((row) => (
                <div className="bar-column" key={row.id}>
                  <span>{row.wer === undefined ? "—" : (row.wer * 100).toFixed(1)}</span>
                  <i className={best && row.id === best.id ? "best" : ""} style={{ height: `${row.wer === undefined ? 2 : Math.max(4, (row.wer / maxWer) * 88)}%` }} />
                  <small>{row.id}</small>
                </div>
              ))}
            </div>
          ) : (
            <div className="chart-empty"><BarChart3 /><span>Awaiting labeled dev-set measurements</span></div>
          )}
        </div>
      </div>
      {lastResult?.evaluation && (
        <div className="service-evaluation">
          Last service run: WER {formatPercent(lastResult.evaluation.wer)} · CER {formatPercent(lastResult.evaluation.cer)} · {lastResult.evaluation.calls ?? "?"} Prisma call(s){lastResult.evaluation.latencyMs ? ` · ${Math.round(lastResult.evaluation.latencyMs)} ms` : ""}. {lastResult.evaluation.note ?? ""}
        </div>
      )}
    </section>
  );
}
