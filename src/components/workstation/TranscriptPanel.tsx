import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, FileAudio, Square, Volume2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  synthesizeTranscript,
  type AccuracyMode,
  type LanguageCode,
  type OutputFormat,
  type TrackId,
} from "@/lib/bharatnoise-api";
import { characterErrorRate, diffWords, formatPercent, wordErrorRate } from "@/lib/metrics";
import { Segmented, SourceTag } from "./shared";
import { trackName } from "./workstation-utils";

export type TranscriptView = {
  id: TrackId;
  text?: string | undefined;
  source: "service" | "missing";
  reason?: string | undefined;
  truncated?: boolean | undefined;
};

type Props = {
  transcripts: TranscriptView[];
  selected: TrackId;
  baseline?: string | undefined;
  reference: string;
  onReference: (value: string) => void;
  mode: AccuracyMode;
  modelLabel: string;
  requestedLanguageCode: LanguageCode;
  outputFormat: OutputFormat;
  detectedLanguageCode?: string | undefined;
  mockMode: boolean;
  onSelect?: ((id: TrackId) => void) | undefined;
  apiUrl: string;
};

const formatLabels: Record<OutputFormat, string> = {
  auto: "automatic format",
  verbatim: "verbatim",
  transcribe: "transcribe",
  codemix: "codemix",
  transliterate: "transliterate",
};

export function TranscriptPanel({
  transcripts,
  selected,
  baseline,
  reference,
  onReference,
  mode,
  modelLabel,
  requestedLanguageCode,
  outputFormat,
  detectedLanguageCode,
  mockMode,
  onSelect,
  apiUrl,
}: Props) {
  const [tab, setTab] = useState<"compare" | "single">("compare");
  const [showReference, setShowReference] = useState(false);
  const [speakingId, setSpeakingId] = useState<TrackId | null>(null);
  const [readAloudMessage, setReadAloudMessage] = useState("");
  const [speechAudio, setSpeechAudio] = useState<{ url: string; transcriptId: TrackId } | null>(
    null,
  );
  const speechRequestRef = useRef<AbortController | null>(null);
  const speechAudioUrlRef = useRef<string | null>(null);
  const speechAudioElementRef = useRef<HTMLAudioElement | null>(null);
  const shown = tab === "single" ? transcripts.filter((item) => item.id === selected) : transcripts;
  const hasServiceTranscripts = transcripts.some((item) => item.source === "service");
  const bestReferenceMatch = useMemo(() => {
    if (mockMode || !reference.trim()) return undefined;
    return transcripts
      .filter((item) => item.source === "service" && item.text?.trim())
      .map((item) => ({
        id: item.id,
        wer: wordErrorRate(reference, item.text ?? ""),
      }))
      .filter((item): item is { id: TrackId; wer: number } => item.wer !== null)
      .sort((left, right) => left.wer - right.wer)[0];
  }, [mockMode, reference, transcripts]);

  useEffect(
    () => () => {
      speechRequestRef.current?.abort();
      if (speechAudioUrlRef.current) URL.revokeObjectURL(speechAudioUrlRef.current);
    },
    [],
  );

  useEffect(() => {
    if (bestReferenceMatch) onSelect?.(bestReferenceMatch.id);
  }, [bestReferenceMatch, onSelect]);

  useEffect(() => {
    if (!speechAudio || !speechAudioElementRef.current) return;
    void speechAudioElementRef.current.play().then(
      () => setReadAloudMessage(`Playing Gnani speech for ${speechAudio.transcriptId}.`),
      (error: unknown) => {
        const detail =
          error instanceof DOMException && error.name === "NotAllowedError"
            ? "Automatic playback was blocked by the browser."
            : "The generated audio could not be played automatically.";
        setReadAloudMessage(`${detail} Use the audio player below to listen.`);
      },
    );
  }, [speechAudio]);

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Transcript copied.");
    } catch {
      toast.error("Copy is blocked in this browser.");
    }
  }

  async function readAloud(id: TrackId, text: string) {
    if (speakingId === id) {
      speechRequestRef.current?.abort();
      speechRequestRef.current = null;
      setSpeakingId(null);
      setReadAloudMessage("Speech generation cancelled.");
      return;
    }
    speechRequestRef.current?.abort();
    if (speechAudioUrlRef.current) URL.revokeObjectURL(speechAudioUrlRef.current);
    speechAudioUrlRef.current = null;
    setSpeechAudio(null);
    const controller = new AbortController();
    speechRequestRef.current = controller;
    setSpeakingId(id);
    setReadAloudMessage(
      "Sending the complete transcript to Gnani for multilingual speech synthesis…",
    );
    try {
      const audio = await synthesizeTranscript(apiUrl, text, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(audio);
      speechAudioUrlRef.current = url;
      setSpeechAudio({ url, transcriptId: id });
      setReadAloudMessage(`Gnani speech for ${id} is ready. Use the audio player below to listen.`);
    } catch (error) {
      if (speechRequestRef.current !== controller) return;
      if (controller.signal.aborted) {
        setReadAloudMessage("Speech generation cancelled.");
      } else {
        const message = error instanceof Error ? error.message : "Could not generate speech audio.";
        setReadAloudMessage(message);
        toast.error(message);
      }
    } finally {
      if (speechRequestRef.current === controller) {
        speechRequestRef.current = null;
        setSpeakingId(null);
      }
    }
  }

  return (
    <section
      className="panel transcript-panel"
      id="section-transcript"
      aria-labelledby="transcript-heading"
    >
      <div className="panel-heading">
        <div className="panel-title">
          <FileAudio />
          <div>
            <h2 id="transcript-heading">Transcript Studio</h2>
            <span>
              {modelLabel} · {formatLabels[outputFormat]}
              {detectedLanguageCode ? ` · detected ${detectedLanguageCode}` : ""}
            </span>
          </div>
        </div>
        <Segmented
          className="transcript-tabs"
          label="Transcript view"
          value={tab}
          onChange={setTab}
          options={[
            { value: "compare", label: "Compare" },
            { value: "single", label: "Selected" },
          ]}
        />
      </div>
      {detectedLanguageCode &&
        requestedLanguageCode !== "indic-auto" &&
        detectedLanguageCode !== requestedLanguageCode && (
          <p className="transcript-missing">
            Requested {requestedLanguageCode}, but Prisma detected {detectedLanguageCode}. For
            multilingual audio, try Prisma v3.0 Auto-detect on the original (C0) audio.
          </p>
        )}
      {!mockMode && reference.trim() && bestReferenceMatch && (
        <p className="transcript-reference-match" role="status">
          Selected {bestReferenceMatch.id}: lowest WER ({formatPercent(bestReferenceMatch.wer)})
          among returned candidates against your supplied reference. This is reference-based, not a
          general accuracy guarantee.
        </p>
      )}
      {!mockMode && hasServiceTranscripts && !reference.trim() && (
        <p className="transcript-missing">
          The service selection is routing or transcript consensus, not a verified correctness
          ranking. Add a human-verified reference to compare WER, or select a transcript below.
        </p>
      )}
      {readAloudMessage && (
        <p className="transcript-missing" role="status">
          {readAloudMessage}
        </p>
      )}
      {mockMode && (
        <p className="transcript-missing" role="status">
          Gnani read-aloud is unavailable in mock mode.
        </p>
      )}
      <div className={`transcript-cards ${shown.length === 1 ? "single" : ""}`}>
        {shown.map((item) => {
          const wer =
            !mockMode && item.text?.trim() && reference.trim()
              ? wordErrorRate(reference, item.text)
              : null;
          const cer =
            !mockMode && item.text?.trim() && reference.trim()
              ? characterErrorRate(reference, item.text)
              : null;
          const tokens =
            item.text?.trim() && baseline && item.id !== "C0"
              ? diffWords(baseline, item.text)
              : null;
          const hasText = Boolean(item.text?.trim());
          return (
            <article className="transcript-card" key={item.id}>
              <div className="transcript-card-head">
                <strong>
                  {item.id === "C0" ? "Original (C0)" : `${item.id} · ${trackName(item.id)}`}
                </strong>
                <span className="transcript-card-tools">
                  <SourceTag kind={item.source === "service" ? "service" : "empty"} />
                  {hasText && onSelect && (
                    <Button
                      variant={item.id === selected ? "secondary" : "ghost"}
                      size="sm"
                      aria-pressed={item.id === selected}
                      aria-label={`Select ${item.id} transcript`}
                      onClick={() => onSelect(item.id)}
                    >
                      {item.id === selected ? <Check /> : null}
                      {item.id === selected ? "Selected" : "Select"}
                    </Button>
                  )}
                  {hasText && !mockMode && (
                    <>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="tiny-icon"
                        aria-label={
                          speakingId === item.id
                            ? `Cancel ${item.id} speech generation`
                            : `Read ${item.id} transcript aloud`
                        }
                        title={
                          speakingId === item.id
                            ? `Cancel ${item.id} speech generation`
                            : `Read ${item.id} transcript aloud`
                        }
                        onClick={() => void readAloud(item.id, item.text!)}
                      >
                        {speakingId === item.id ? <Square /> : <Volume2 />}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="tiny-icon"
                        aria-label={`Copy ${item.id} transcript`}
                        onClick={() => void copy(item.text!)}
                      >
                        <Copy />
                      </Button>
                    </>
                  )}
                </span>
              </div>
              {hasText ? (
                <p>
                  {tokens
                    ? tokens.map((token, i) => (
                        <span key={i}>
                          {token.kind === "changed" ? (
                            <mark className="diff-changed">{token.word}</mark>
                          ) : (
                            token.word
                          )}{" "}
                        </span>
                      ))
                    : item.text}
                </p>
              ) : (
                <p className="transcript-missing">
                  {item.reason ??
                    (mode === "fast"
                      ? "Not transcribed this run. Fast mode sends only the routed candidate to Prisma."
                      : "No transcript was returned for this candidate in this run.")}
                </p>
              )}
              {hasText && item.truncated && (
                <p className="transcript-missing">
                  Gnani marked this transcript as truncated; the returned text may be incomplete.
                </p>
              )}
              {wer !== null && (
                <div className="transcript-scores">
                  <span>
                    WER <b>{formatPercent(wer)}</b>
                  </span>
                  <span>
                    CER <b>{formatPercent(cer)}</b>
                  </span>
                  <small>vs your reference</small>
                </div>
              )}
            </article>
          );
        })}
      </div>
      {speechAudio && (
        <div className="transcript-tts">
          <span id="gnani-transcript-audio-label">Gnani speech · {speechAudio.transcriptId}</span>
          <audio
            id="gnani-transcript-audio"
            aria-labelledby="gnani-transcript-audio-label"
            ref={speechAudioElementRef}
            controls
            preload="metadata"
            src={speechAudio.url}
          />
        </div>
      )}
      <div className="reference-block">
        <button
          className="link-button"
          onClick={() => setShowReference((value) => !value)}
          aria-expanded={showReference}
        >
          {showReference ? "Hide reference transcript" : "Score against a reference transcript"}
        </button>
        {showReference && (
          <>
            <label className="visually-hidden" htmlFor="reference-text">
              Reference transcript
            </label>
            <textarea
              id="reference-text"
              className="reference-input"
              rows={2}
              placeholder="Paste the human-verified transcript for this clip"
              value={reference}
              onChange={(event) => onReference(event.currentTarget.value)}
            />
            <small>
              {mockMode
                ? "Mock transcripts cannot be scored."
                : "WER and CER are computed in your browser after lower-casing and removing punctuation. Words that differ from C0 are highlighted."}
            </small>
          </>
        )}
      </div>
    </section>
  );
}
