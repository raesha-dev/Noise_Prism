/** Transcript metrics computed in the browser against a user-supplied reference. */

export function normalizeTranscript(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFC")
    .replace(/[\p{P}\p{S}]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function editDistance<T>(a: T[], b: T[]): number {
  let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= b.length; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      current[j] = Math.min(
        (previous[j] ?? 0) + 1,
        (current[j - 1] ?? 0) + 1,
        (previous[j - 1] ?? 0) + cost,
      );
    }
    previous = current;
  }
  return previous[b.length] ?? 0;
}

/** Word error rate as a fraction (0.15 = 15%). Returns null when the reference is empty. */
export function wordErrorRate(reference: string, hypothesis: string): number | null {
  const ref = normalizeTranscript(reference).split(" ").filter(Boolean);
  if (ref.length === 0) return null;
  const hyp = normalizeTranscript(hypothesis).split(" ").filter(Boolean);
  return editDistance(ref, hyp) / ref.length;
}

/** Character error rate as a fraction, ignoring spaces. Returns null when the reference is empty. */
export function characterErrorRate(reference: string, hypothesis: string): number | null {
  const ref = [...normalizeTranscript(reference).replace(/ /g, "")];
  if (ref.length === 0) return null;
  const hyp = [...normalizeTranscript(hypothesis).replace(/ /g, "")];
  return editDistance(ref, hyp) / ref.length;
}

export type DiffToken = { word: string; kind: "same" | "changed" };

/** Marks words in `hypothesis` that are not part of the longest common word sequence with `base`. */
export function diffWords(base: string, hypothesis: string): DiffToken[] {
  const a = base.split(/\s+/).filter(Boolean);
  const b = hypothesis.split(/\s+/).filter(Boolean);
  const key = (word: string) => normalizeTranscript(word);
  const table = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i]![j] =
        key(a[i]!) === key(b[j]!)
          ? table[i + 1]![j + 1]! + 1
          : Math.max(table[i + 1]![j]!, table[i]![j + 1]!);
    }
  }
  const tokens: DiffToken[] = [];
  let i = 0,
    j = 0;
  while (j < b.length) {
    if (i < a.length && key(a[i]!) === key(b[j]!)) {
      tokens.push({ word: b[j]!, kind: "same" });
      i += 1;
      j += 1;
    } else if (i < a.length && table[i + 1]![j]! >= table[i]![j + 1]!) {
      i += 1;
    } else {
      tokens.push({ word: b[j]!, kind: "changed" });
      j += 1;
    }
  }
  return tokens;
}

export const formatPercent = (value?: number | null, digits = 2) =>
  value === undefined || value === null || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(digits)}%`;
