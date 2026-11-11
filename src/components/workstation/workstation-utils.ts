import type { TrackId } from "@/lib/bharatnoise-api";

export const trackDefs: { id: TrackId; name: string; detail: string; color: "cyan" | "violet" }[] =
  [
    { id: "C0", name: "Original", detail: "Reference input", color: "cyan" },
    { id: "C1", name: "Hygiene", detail: "Band-limit · hum notch · gain", color: "cyan" },
    { id: "C2", name: "Enhanced", detail: "Wiener / spectral gate · blend", color: "violet" },
    { id: "C3", name: "DeepFilterNet", detail: "8 → 48 → 8 kHz · optional", color: "cyan" },
  ];

export const trackName = (id: TrackId) => trackDefs.find((track) => track.id === id)?.name ?? id;

export const stageNames = [
  "Ingest & prepare",
  "Analyse VAD / SNR",
  "Generate candidates",
  "Speech-preservation guard",
  "Route & transcribe",
];

export const ASR_MODELS = [
  { value: "prisma-v2.5", label: "Prisma v2.5 · primary" },
  { value: "prisma-v3.0", label: "Prisma v3.0" },
];

export function formatTime(seconds: number) {
  const whole = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export const formatDb = (value?: number) =>
  value === undefined || !Number.isFinite(value) ? "—" : `${value.toFixed(1)} dB`;

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
