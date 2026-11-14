from __future__ import annotations

from .compare import _bootstrap_corpus_delta, summarise


def _row(speaker: str, hypothesis: str) -> dict[str, str]:
    return {
        "id": f"{speaker}-{hypothesis}",
        "speaker_id": speaker,
        "reference": "a b",
        "hypothesis": hypothesis,
    }


def test_paired_bootstrap_resamples_speakers_as_clusters():
    pairs = [
        (_row("speaker-a", "a b"), _row("speaker-a", "")),
        (_row("speaker-a", "a b"), _row("speaker-a", "")),
        (_row("speaker-b", "a b"), _row("speaker-b", "a b")),
    ]

    interval = _bootstrap_corpus_delta(pairs, n_boot=400, seed=7)
    duplicated_interval = _bootstrap_corpus_delta(pairs * 2, n_boot=400, seed=7)

    assert interval == _bootstrap_corpus_delta(pairs, n_boot=400, seed=7)
    assert interval == duplicated_interval


def test_paired_bootstrap_falls_back_to_utterance_clusters_without_speaker_ids():
    pairs = [
        ({"reference": "a b", "hypothesis": "a b"}, {"reference": "a b", "hypothesis": "a"}),
        ({"reference": "a b", "hypothesis": "a b"}, {"reference": "a b", "hypothesis": "a b"}),
    ]

    low, high = _bootstrap_corpus_delta(pairs, n_boot=200, seed=3)

    assert low is not None
    assert high is not None
    assert low <= high


def test_underpowered_summary_reports_wer_without_comparative_claims():
    pairs = [
        (
            {"id": "mix-1", "source_clip_id": "source-1", "reference": "a b", "hypothesis": "a b"},
            {"id": "mix-1", "source_clip_id": "source-1", "reference": "a b", "hypothesis": ""},
        ),
    ]

    result = summarise(pairs, n_boot=200, seed=3)

    assert result["n_source_clips"] == 1
    assert result["underpowered"] is True
    assert result["wer"]["B"] == 1.0
    assert result["wer"]["delta"] is None
    assert result["B_better"] is None
