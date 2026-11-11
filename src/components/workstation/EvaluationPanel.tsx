import { useRef, useState, type ChangeEvent } from "react";
import { BarChart3, CloudDownload, FileDown, FileUp, LoaderCircle, RotateCcw, Trophy } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { fetchEvaluation, parseEvaluationReport, type EvaluationReport, type EvaluationRow, type EvaluationSection, type ProcessingResult } from "@/lib/bharatnoise-api";
import { formatPercent } from "@/lib/metrics";
import { Segmented, SourceTag, downloadBlob } from "./shared";

const SECTION_LABELS: Record<EvaluationSection, string> = { comparison: "Pipeline comparison", ablation: "Ablation", snr: "SNR buckets", noise: "Noise type" };
const EMPTY_REPORT: EvaluationReport = { available: false, reason: "No benchmark results have been generated yet.", sections: {} };

type Props = { report: EvaluationReport | null; onReport: (report: EvaluationReport | null, source: "service" | "imported" | null) => void; reportSource: "service" | "imported" | null; apiUrl: string; lastResult: ProcessingResult | null };

export function bestRow(rows: EvaluationRow[]) {
  return rows.filter((row) => row.wer != null).sort((a, b) => (a.wer ?? 1) - (b.wer ?? 1))[0];
}

export function EvaluationPanel({ report, onReport, reportSource, apiUrl, lastResult }: Props) {
  const [section, setSection] = useState<EvaluationSection>("comparison");
  const [loading, setLoading] = useState(false);
  const importRef = useRef<HTMLInputElement | null>(null);
  const active = report ?? EMPTY_REPORT;
  const isSample = !report;
  const isUnavailable = !active.available;
  const rows = active.sections[section] ?? [];
  const best = bestRow(rows);
  const baseline = rows.find((row) => row.candidate === "C0") ?? rows[0];
  const maxWer = Math.max(0, ...rows.map((row) => row.wer ?? 0));
  const c0Wins = !isSample && !isUnavailable && section === "comparison" && best && (best.candidate === "C0" || best.id === baseline?.id);

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
            <span>{isSample ? "No benchmark results loaded · import measured results or connect a service" : isUnavailable ? active.reason : `${active.dataset ?? "Measured results"}${active.clipCount != null ? ` · ${active.clipCount} clips` : ""}${active.measuredAt ? ` · ${active.measuredAt}` : ""}`}</span>
          </div>
        </div>
        <SourceTag kind={isSample || isUnavailable ? "empty" : reportSource === "service" ? "service" : "imported"} />
      </div>
      <div className="eval-toolbar">
        <Segmented className="eval-tabs" label="Evaluation section" value={section} onChange={setSection} options={(Object.keys(SECTION_LABELS) as EvaluationSection[]).map((value) => ({ value, label: SECTION_LABELS[value] }))} />
        <div className="eval-actions">
          <input ref={importRef} type="file" accept="application/json,.json" className="visually-hidden" onChange={(event) => void importFile(event)} aria-label="Import evaluation JSON" tabIndex={-1} />
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={!apiUrl || loading} title={apiUrl ? "GET /evaluation" : "Connect a service first"}>{loading ? <LoaderCircle className="spin" /> : <CloudDownload />}Load from service</Button>
          <Button variant="outline" size="sm" onClick={() => importRef.current?.click()}><FileUp />Import</Button>
          <Button variant="outline" size="sm" onClick={exportCsv}><FileDown />CSV</Button>
          {!isSample && <Button variant="ghost" size="sm" onClick={() => onReport(null, null)} aria-label="Clear evaluation results"><RotateCcw /></Button>}
        </div>
      </div>
      {c0Wins && (
        <p className="honesty-banner">
          Nothing beats C0 on this set. Report what you measured: how much aggressive denoising hurts Prisma, and how much the guard and consensus recover.
        </p>
      )}
      <div className="evaluation-content">
        {isUnavailable ? (
          <p className="chart-empty">{active.reason ?? "No benchmark results have been generated yet."}</p>
        ) : <div className="eval-table-wrap">
          <table className="eval-table">
            <thead><tr><th>ID</th><th>Pipeline → Prisma</th><th>WER ↓</th><th>CER ↓</th><th>Latency</th>{section === "comparison" && <th>Δ vs C0</th>}</tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className={best && row.id === best.id ? "highlight-row" : ""}>
                  <td>{row.id}</td>
                  <td>{row.pipeline}</td>
                  <td>{row.wer == null ? "—" : formatPercent(row.wer)}</td>
                  <td>{row.cer == null ? "—" : formatPercent(row.cer)}</td>
                  <td>{row.latencyMs == null ? "—" : `${Math.round(row.latencyMs).toLocaleString()} ms`}</td>
                  {section === "comparison" && <td>{row.wer != null && baseline?.wer != null && row.id !== baseline.id ? `${((row.wer - baseline.wer) * 100).toFixed(2)} pts` : "—"}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>}
        {!isUnavailable && <div className="chart-box">
          <div className="chart-box-heading">WER by row <span>{isSample ? "No measurements" : "Measured · lower is better"}</span></div>
          {rows.some((row) => row.wer !== undefined) ? (
            <div className="bar-chart" role="img" aria-label="Word error rate by pipeline">
              {rows.filter((row) => row.wer != null).map((row) => (
                <div className="bar-column" key={row.id}>
                  <span>{(row.wer! * 100).toFixed(1)}</span>
                  <i className={best && row.id === best.id ? "best" : ""} style={{ height: `${Math.max(4, (maxWer > 0 ? row.wer! / maxWer : 0) * 88)}%` }} />
                  <small>{row.id}</small>
                </div>
              ))}
            </div>
          ) : (
            <div className="chart-empty"><BarChart3 /><span>Awaiting labeled dev-set measurements</span></div>
          )}
        </div>}
      </div>
      {!isMockResult(lastResult) && lastResult?.evaluation && (
        <div className="service-evaluation">
          Last service run: {[
            lastResult.evaluation.wer !== undefined ? `WER ${formatPercent(lastResult.evaluation.wer)}` : "WER not reported",
            lastResult.evaluation.cer !== undefined ? `CER ${formatPercent(lastResult.evaluation.cer)}` : null,
            lastResult.evaluation.calls !== undefined ? `${lastResult.evaluation.calls} Prisma call(s)` : null,
            lastResult.evaluation.latencyMs !== undefined ? `${Math.round(lastResult.evaluation.latencyMs)} ms` : null,
          ].filter(Boolean).join(" · ")}. {lastResult.evaluation.note ?? ""}
        </div>
      )}
    </section>
  );
}

function isMockResult(result: ProcessingResult | null): boolean {
  return result?.gnaniMode === "mock";
}
