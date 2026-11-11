import { useState } from "react";
import { Copy, FileAudio } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import type { TrackId } from "@/lib/bharatnoise-api";
import { characterErrorRate, diffWords, formatPercent, wordErrorRate } from "@/lib/metrics";
import { Segmented, SourceTag, trackName } from "./shared";

export type TranscriptView = { id: TrackId; text?: string; source: "service" | "sample" | "missing" };

type Props = { transcripts: TranscriptView[]; selected: TrackId; baseline?: string; reference: string; onReference: (value: string) => void; modelLabel: string };

export function TranscriptPanel({ transcripts, selected, baseline, reference, onReference, modelLabel }: Props) {
  const [tab, setTab] = useState<"compare" | "single">("compare");
  const [showReference, setShowReference] = useState(false);
  const shown = tab === "single" ? transcripts.filter((item) => item.id === selected) : transcripts;

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Transcript copied.");
    } catch {
      toast.error("Copy is blocked in this browser.");
    }
  }

  return (
    <section className="panel transcript-panel" id="section-transcript" aria-labelledby="transcript-heading">
      <div className="panel-heading">
        <div className="panel-title"><FileAudio /><div><h2 id="transcript-heading">Transcript Studio</h2><span>{modelLabel} · verbatim</span></div></div>
        <Segmented className="transcript-tabs" label="Transcript view" value={tab} onChange={setTab} options={[{ value: "compare", label: "Compare" }, { value: "single", label: "Selected" }]} />
      </div>
      <div className={`transcript-cards ${shown.length === 1 ? "single" : ""}`}>
        {shown.map((item) => {
          const wer = item.text && reference.trim() ? wordErrorRate(reference, item.text) : null;
          const cer = item.text && reference.trim() ? characterErrorRate(reference, item.text) : null;
          const tokens = item.text && baseline && item.id !== "C0" ? diffWords(baseline, item.text) : null;
          return (
            <article className="transcript-card" key={item.id}>
              <div className="transcript-card-head">
                <strong>{item.id === "C0" ? "Original (C0)" : `${item.id} · ${trackName(item.id)}`}</strong>
                <span className="transcript-card-tools">
                  <SourceTag kind={item.source === "service" ? "service" : "sample"} />
                  {item.text && <Button variant="ghost" size="icon" className="tiny-icon" aria-label={`Copy ${item.id} transcript`} onClick={() => void copy(item.text!)}><Copy /></Button>}
                </span>
              </div>
              {item.text ? (
                <p>{tokens ? tokens.map((token, i) => <span key={i} className={token.kind === "changed" ? "diff-changed" : ""}>{token.word} </span>) : item.text}</p>
              ) : (
                <p className="transcript-missing">Not transcribed for this run. Fast mode sends only the routed candidate to Prisma.</p>
              )}
              {item.source === "sample" && <small>Illustrative transcript · not generated from your clip</small>}
              {wer !== null && <div className="transcript-scores"><span>WER <b>{formatPercent(wer)}</b></span><span>CER <b>{formatPercent(cer)}</b></span><small>vs your reference</small></div>}
            </article>
          );
        })}
      </div>
      <div className="reference-block">
        <button className="link-button" onClick={() => setShowReference((value) => !value)} aria-expanded={showReference}>
          {showReference ? "Hide reference transcript" : "Score against a reference transcript"}
        </button>
        {showReference && (
          <>
            <label className="visually-hidden" htmlFor="reference-text">Reference transcript</label>
            <textarea id="reference-text" className="reference-input" rows={2} placeholder="Paste the human-verified transcript for this clip" value={reference} onChange={(event) => onReference(event.currentTarget.value)} />
            <small>WER and CER are computed in your browser after lower-casing and removing punctuation. Words that differ from C0 are highlighted.</small>
          </>
        )}
      </div>
    </section>
  );
}
