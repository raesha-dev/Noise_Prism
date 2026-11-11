import type { AccuracyMode, ProcessingResult, TrackId } from "./bharatnoise-api";

export type HistoryEntry = {
  id: string;
  fileName: string;
  mode: AccuracyMode;
  source: "service";
  selectedCandidate?: TrackId | undefined;
  snrDb?: number | undefined;
  latencyMs?: number | undefined;
  createdAt: string;
  result?: ProcessingResult | undefined;
};

const KEY = "bharatnoise.history.v1";
export const HISTORY_LIMIT = 25;

export function loadHistory(): HistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(KEY) ?? "[]") as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isHistoryEntry).slice(0, HISTORY_LIMIT);
  } catch {
    return [];
  }
}

function isHistoryEntry(value: unknown): value is HistoryEntry {
  if (!value || typeof value !== "object") return false;
  const entry = value as Record<string, unknown>;
  return (
    entry["source"] === "service" &&
    typeof entry["id"] === "string" &&
    typeof entry["fileName"] === "string" &&
    (entry["mode"] === "fast" || entry["mode"] === "max") &&
    typeof entry["createdAt"] === "string"
  );
}

export function saveHistory(entries: HistoryEntry[]): HistoryEntry[] {
  const trimmed = entries.slice(0, HISTORY_LIMIT);
  try {
    window.localStorage.setItem(KEY, JSON.stringify(trimmed));
  } catch {
    // Storage full or blocked: keep the in-memory list only.
  }
  return trimmed;
}
