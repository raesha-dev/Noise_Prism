import { Check, FileDown, History, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { HistoryEntry } from "@/lib/history";
import { downloadBlob, formatDb } from "./workstation-utils";

export function HistoryPanel({
  entries,
  onClear,
  onRestore,
}: {
  entries: HistoryEntry[];
  onClear: () => void;
  onRestore: (entry: HistoryEntry) => void;
}) {
  return (
    <section className="panel history-panel" id="section-history" aria-labelledby="history-heading">
      <div className="panel-heading">
        <div className="panel-title">
          <History />
          <div>
            <h2 id="history-heading">Experiment history</h2>
            <span>Saved in this browser · last 25 runs</span>
          </div>
        </div>
        <div className="eval-actions">
          <Button
            variant="ghost"
            size="sm"
            disabled={entries.length === 0}
            onClick={() =>
              downloadBlob(
                new Blob([JSON.stringify(entries, null, 2)], { type: "application/json" }),
                "bharatnoise-history.json",
              )
            }
          >
            <FileDown />
            Export
          </Button>
          <Button variant="ghost" size="sm" onClick={onClear} disabled={entries.length === 0}>
            <RotateCcw />
            Clear
          </Button>
        </div>
      </div>
      {entries.length === 0 ? (
        <p>No runs yet. Run the pipeline to record an experiment.</p>
      ) : (
        <ul className="history-list">
          {entries.map((entry) => (
            <li key={entry.id}>
              <button
                onClick={() => onRestore(entry)}
                disabled={!entry.result}
                title={entry.result ? "Show this result" : "No result was stored"}
              >
                <Check />
                <span className="history-name">{entry.fileName}</span>
                <span
                  className={`source-tag ${entry.result?.gnaniMode === "mock" ? "source-demo" : "source-service"}`}
                >
                  {entry.result?.gnaniMode === "mock" ? "Mock GNANI" : "Service"}
                </span>
                <span>{entry.mode === "fast" ? "Fast" : "Max"}</span>
                <span>{entry.selectedCandidate ?? "—"}</span>
                <span>{formatDb(entry.snrDb)}</span>
                <span>
                  {entry.latencyMs ? `${Math.round(entry.latencyMs).toLocaleString()} ms` : "—"}
                </span>
                <time dateTime={entry.createdAt}>{new Date(entry.createdAt).toLocaleString()}</time>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
