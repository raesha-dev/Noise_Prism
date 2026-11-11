import type { AccuracyMode, ProcessingResult, TrackId } from "./bharatnoise-api";

export type HistoryEntry = {
  id: string;
  fileName: string;
  mode: AccuracyMode;
  source: "service" | "demo";
  selectedCandidate?: TrackId;
  snrDb?: number;
  latencyMs?: number;
  createdAt: string;
  result?: ProcessingResult;
};

const KEY = "bharatnoise.history.v1";
export const HISTORY_LIMIT = 25;

export function loadHistory(): HistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(KEY) ?? "[]") as unknown;
    return Array.isArray(parsed) ? (parsed as HistoryEntry[]).slice(0, HISTORY_LIMIT) : [];
  } catch {
    return [];
  }
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
