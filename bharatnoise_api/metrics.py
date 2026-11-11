"""Transcript normalization and error metrics shared by the API and benchmarks."""
from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass
from typing import Sequence


def normalize(text: str) -> str:
    """Apply the benchmark's case-folding, NFC, punctuation and whitespace rules."""
    normalized = unicodedata.normalize("NFC", text.lower())
    chars = [" " if unicodedata.category(char)[0] in {"P", "S"} else char for char in normalized]
    return " ".join("".join(chars).split())


@dataclass(frozen=True)
class Alignment:
    sub: int
    dele: int
    ins: int
    n: int

    @property
    def errors(self) -> int:
        return self.sub + self.dele + self.ins


def align_counts(reference: Sequence[str], hypothesis: Sequence[str]) -> Alignment:
    """Count substitutions, deletions and insertions using deterministic edit alignment."""
    rows = len(reference) + 1
    cols = len(hypothesis) + 1
    costs = [[0] * cols for _ in range(rows)]
    back: list[list[str]] = [[""] * cols for _ in range(rows)]
    for i in range(1, rows):
        costs[i][0], back[i][0] = i, "del"
    for j in range(1, cols):
        costs[0][j], back[0][j] = j, "ins"
    for i in range(1, rows):
        for j in range(1, cols):
            if reference[i - 1] == hypothesis[j - 1]:
                costs[i][j], back[i][j] = costs[i - 1][j - 1], "ok"
                continue
            options = (
                (costs[i - 1][j - 1] + 1, "sub"),
                (costs[i - 1][j] + 1, "del"),
                (costs[i][j - 1] + 1, "ins"),
            )
            costs[i][j], back[i][j] = min(options, key=lambda item: item[0])
    sub = dele = ins = 0
    i, j = len(reference), len(hypothesis)
    while i or j:
        op = back[i][j]
        if op == "ok":
            i, j = i - 1, j - 1
        elif op == "sub":
            sub += 1
            i, j = i - 1, j - 1
        elif op == "del":
            dele += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return Alignment(sub, dele, ins, len(reference))


@dataclass(frozen=True)
class UtteranceScore:
    word: Alignment
    char_errors: int
    char_n: int


def score_utterance(reference: str, hypothesis: str) -> UtteranceScore:
    """Score normalized words and space-free characters."""
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    word = align_counts(ref.split(), hyp.split())
    chars = align_counts(list(ref.replace(" ", "")), list(hyp.replace(" ", "")))
    return UtteranceScore(word, chars.errors, chars.n)


def paired_bootstrap(
    differences: Sequence[float], n_boot: int = 2000, seed: int = 0
) -> tuple[float | None, float | None]:
    """Return a percentile 95% interval for paired per-clip differences."""
    if not differences:
        return None, None
    rng = random.Random(seed)
    estimates = sorted(
        sum(rng.choice(differences) for _ in differences) / len(differences)
        for _ in range(n_boot)
    )
    return estimates[int(0.025 * (n_boot - 1))], estimates[int(0.975 * (n_boot - 1))]


def win_tie_loss(differences: Sequence[float]) -> tuple[int, int, int]:
    """Count paired outcomes where a negative B-minus-A delta is a B win."""
    return (
        sum(value > 0 for value in differences),
        sum(value == 0 for value in differences),
        sum(value < 0 for value in differences),
    )
