import type { ReactNode } from "react";
import type { TrackId } from "@/lib/bharatnoise-api";

export const trackDefs: { id: TrackId; name: string; detail: string; color: "cyan" | "violet" }[] = [
  { id: "C0", name: "Original", detail: "Reference input", color: "cyan" },
  { id: "C1", name: "Hygiene", detail: "Band-limit · hum notch · gain", color: "cyan" },
  { id: "C2", name: "Enhanced", detail: "Wiener / spectral gate · blend", color: "violet" },
  { id: "C3", name: "DeepFilterNet", detail: "8 → 48 → 8 kHz · optional", color: "cyan" },
];

export const trackName = (id: TrackId) => trackDefs.find((track) => track.id === id)?.name ?? id;

export const stageNames = ["Ingest & prepare", "Analyse VAD / SNR", "Generate candidates", "Speech-preservation guard", "Route & transcribe"];

export const sampleTexts: Record<TrackId, string> = {
  C0: "haan mujhe voh plan kal tak bhej dena varna problem ho jayegi",
  C1: "haan mujhe voh plan kal tak bhej dena varna problem ho jayegi",
  C2: "haan mujhe woh plan kal tak bhej dena warna problem ho jayegi",
  C3: "haan mujhe woh plan kal tak bhej dena varna problem ho jayegi",
};

export const ASR_MODELS = [
  { value: "prisma-v2.5", label: "Prisma v2.5 · primary" },
  { value: "prisma-v3.0", label: "Prisma v3.0" },
];

export function formatTime(seconds: number) {
  const whole = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export const formatDb = (value?: number) => (value === undefined || !Number.isFinite(value) ? "—" : `${value.toFixed(1)} dB`);

/** Deterministic illustrative waveform used only when no clip is loaded. */
export function illustrativePeaks(row: number, bins = 320): number[] {
  const amp = [0.82, 0.62, 1.04, 0.75][row] ?? 1;
  return Array.from({ length: bins }, (_, i) => {
    const base = Math.sin(i * 0.74) * 11 + Math.sin(i * 1.93) * 7 + Math.sin(i * 0.19) * 6;
    const speech = Math.sin(i * 0.071) * 9 + Math.sin(i * 0.23) * 5;
    const v = 21 + (base + speech) * amp + Math.sin((i + row * 21) * 0.51) * 8;
    return Math.max(0.1, Math.min(1, v / 45));
  });
}

export function WaveformPath({ peaks }: { peaks: number[] }) {
  const d = peaks
    .map((peak, index) => {
      const height = Math.max(1.5, Math.min(46, peak * 44));
      const x = (index / Math.max(1, peaks.length - 1)) * 900;
      return `M ${x.toFixed(1)} ${(24 - height / 2).toFixed(1)} v ${height.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg viewBox="0 0 900 48" preserveAspectRatio="none" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export function SourceTag({ kind }: { kind: "service" | "estimate" | "sample" | "demo" | "imported" }) {
  const label = { service: "Service result", estimate: "Browser estimate", sample: "Illustrative", demo: "Demo", imported: "Imported" }[kind];
  return <span className={`source-tag source-${kind}`}>{label}</span>;
}

export function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label, className = "" }: { value: T; options: { value: T; label: ReactNode }[]; onChange: (value: T) => void; label: string; className?: string | undefined }) {
  return (
    <div className={`segmented ${className}`} role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button key={option.value} type="button" role="radio" aria-checked={value === option.value} className={value === option.value ? "active" : ""} onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function downloadBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
