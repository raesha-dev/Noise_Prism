"""Load baseline CSVs and calculate honest paired comparisons."""
from __future__ import annotations

import csv
import random
import statistics
from pathlib import Path
from typing import Any

try:
    from .metrics import normalize, score_utterance, win_tie_loss
except ImportError:
    from metrics import normalize, score_utterance, win_tie_loss

MIN_REPORTABLE_SOURCE_CLIPS = 20


def load(path: Path) -> list[dict[str, str]]:
    """Read a benchmark CSV without inventing defaults for absent metric fields."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def paired_rows(
    a_rows: list[dict[str, str]], b_rows: list[dict[str, str]]
) -> tuple[list[tuple[dict[str, str], dict[str, str]]], str]:
    """Match successful rows by clip id and report how many were excluded."""
    a_by_id = {row.get("id", ""): row for row in a_rows}
    b_by_id = {row.get("id", ""): row for row in b_rows}
    pairs: list[tuple[dict[str, str], dict[str, str]]] = []
    skipped = 0
    for clip_id in sorted(a_by_id.keys() & b_by_id.keys()):
        left, right = a_by_id[clip_id], b_by_id[clip_id]
        if (
            left.get("error")
            or right.get("error")
            or not normalize(left.get("reference", ""))
            or not normalize(right.get("reference", ""))
            or normalize(left.get("reference", "")) != normalize(right.get("reference", ""))
        ):
            skipped += 1
            continue
        pairs.append((left, right))
    absent = len(a_by_id.keys() ^ b_by_id.keys())
    return pairs, f"skipped {skipped} errored/empty pairs and {absent} unmatched rows"


def _row_errors(row: dict[str, str]) -> tuple[int, int, int, int]:
    score = score_utterance(row.get("reference", ""), row.get("hypothesis", ""))
    return score.word.errors, score.word.n, score.char_errors, score.char_n


def _rate(rows: list[dict[str, str]], char: bool = False) -> float | None:
    counts = [_row_errors(row) for row in rows]
    errors = sum(item[2 if char else 0] for item in counts)
    total = sum(item[3 if char else 1] for item in counts)
    return errors / total if total else None


def _float_delta(left: float | None, right: float | None) -> float | None:
    return right - left if left is not None and right is not None else None


def _bootstrap_corpus_delta(
    pairs: list[tuple[dict[str, str], dict[str, str]]], n_boot: int, seed: int, char: bool = False
) -> tuple[float | None, float | None]:
    """Bootstrap speakers when confirmed, else source clips, then utterances."""
    rng = random.Random(seed)
    clusters: dict[str, list[tuple[dict[str, str], dict[str, str]]]] = {}
    for index, pair in enumerate(pairs):
        source_clip_id = pair[0].get("source_clip_id", "").strip()
        speaker_id = pair[0].get("speaker_id", "").strip()
        cluster_id = (
            f"speaker:{speaker_id}" if speaker_id
            else f"source_clip:{source_clip_id}" if source_clip_id
            else f"utterance:{index}"
        )
        clusters.setdefault(cluster_id, []).append(pair)
    cluster_ids = list(clusters)
    estimates: list[float] = []
    for _ in range(n_boot):
        sampled_clusters = [cluster_ids[rng.randrange(len(cluster_ids))] for _ in cluster_ids]
        sample = [pair for cluster_id in sampled_clusters for pair in clusters[cluster_id]]
        a_errors = a_words = b_errors = b_words = 0
        for left, right in sample:
            a = _row_errors(left)
            b = _row_errors(right)
            error_index, total_index = (2, 3) if char else (0, 1)
            a_errors += a[error_index]
            a_words += a[total_index]
            b_errors += b[error_index]
            b_words += b[total_index]
        if a_words and b_words:
            estimates.append(b_errors / b_words - a_errors / a_words)
    if not estimates:
        return None, None
    estimates.sort()
    low = estimates[int(0.025 * (len(estimates) - 1))]
    high = estimates[int(0.975 * (len(estimates) - 1))]
    return low, high


def summarise(
    pairs: list[tuple[dict[str, str], dict[str, str]]], n_boot: int = 2000, seed: int = 0
) -> dict[str, Any]:
    """Summarize paired WER/CER deltas (B minus A), bootstrap intervals and wins."""
    if not pairs:
        return {
            "clips": 0, "n_source_clips": 0, "underpowered": True,
            "wer": None, "cer": None, "B_better": None, "tie": None, "A_better": None,
        }
    source_clip_ids = {
        right.get("source_clip_id", "").strip() or right.get("id", "").strip()
        for _, right in pairs
        if right.get("source_clip_id", "").strip() or right.get("id", "").strip()
    }
    underpowered = len(source_clip_ids) < MIN_REPORTABLE_SOURCE_CLIPS
    a_rows = [left for left, _ in pairs]
    b_rows = [right for _, right in pairs]
    wer_a, wer_b = _rate(a_rows), _rate(b_rows)
    cer_a, cer_b = _rate(a_rows, char=True), _rate(b_rows, char=True)
    wer_diffs: list[float] = []
    for left, right in pairs:
        e_a, n_a, c_a, cn_a = _row_errors(left)
        e_b, n_b, c_b, cn_b = _row_errors(right)
        if n_a and n_b:
            wer_diffs.append(e_b / n_b - e_a / n_a)
    lo, hi = _bootstrap_corpus_delta(pairs, n_boot, seed)
    win, tie, loss = win_tie_loss(wer_diffs)
    if underpowered:
        lo = hi = None
        cer_lo = cer_hi = None
        win = tie = loss = None
    else:
        cer_lo, cer_hi = _bootstrap_corpus_delta(pairs, n_boot, seed, char=True)
    per_clip_changes = []
    for left, right in pairs:
        left_errors, left_words, _, _ = _row_errors(left)
        right_errors, right_words, _, _ = _row_errors(right)
        if left_words and right_words:
            per_clip_changes.append(right_errors / right_words - left_errors / left_words)
    return {
        "clips": len(pairs),
        "n_source_clips": len(source_clip_ids),
        "underpowered": underpowered,
        "wer": {
            "A": wer_a, "B": wer_b,
            "delta": None if underpowered else _float_delta(wer_a, wer_b),
            "ci95": [lo, hi],
        },
        "cer": {
            "A": cer_a,
            "B": cer_b,
            "delta": None if underpowered else _float_delta(cer_a, cer_b),
            "ci95": [cer_lo, cer_hi],
        },
        "median_delta_wer": None if underpowered or not per_clip_changes else statistics.median(per_clip_changes),
        "B_better": loss,
        "tie": tie,
        "A_better": win,
    }
