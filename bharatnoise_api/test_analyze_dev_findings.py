import csv

from bharatnoise_api.scripts.analyze_dev_findings import generate


def _write(path, rows):
    fields = [
        "id", "source_clip_id", "split", "language", "source_type", "noise_type",
        "target_snr_db", "reference", "hypothesis", "error", "candidate",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _row(identifier, hypothesis, candidate):
    return {
        "id": identifier, "source_clip_id": identifier, "split": "dev",
        "language": "hi-IN", "source_type": "synthetic_tts",
        "noise_type": "babble", "target_snr_db": "0",
        "reference": "a b", "hypothesis": hypothesis, "error": "",
        "candidate": candidate,
    }


def test_dev_findings_marks_small_strata_underpowered_without_transcripts(tmp_path):
    c0, c1, repeat_a, repeat_b = [tmp_path / f"{name}.csv" for name in ("C0", "C1", "rA", "rB")]
    _write(c0, [_row("one", "a b", "C0"), _row("two", "a b", "C0")])
    _write(c1, [_row("one", "x y", "C1"), _row("two", "a b", "C1")])
    _write(repeat_a, [_row("one", "a b", "C0"), _row("two", "a b", "C0")])
    _write(repeat_b, [_row("one", "a b", "C0"), _row("two", "a b", "C0")])
    output = tmp_path / "findings.md"

    generate([("C0", c0), ("C1", c1)], repeat_a, repeat_b, output, n_boot=200)

    content = output.read_text(encoding="utf-8")
    assert "UNDERPOWERED" in content
    assert "WRONG_WORDS" in content
    assert "x y" not in content
    assert "n_source_clips" in content
