"""Shared validation helpers for the human-reviewed team audio set."""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

LANGUAGE_PREFIXES = {"hi-IN": "hi", "kn-IN": "kn", "en-IN": "en"}
SPEAKER_PATTERN = re.compile(r"^spk_[a-z0-9]+$")
ID_PATTERN = re.compile(r"^(hi|kn|en)_(\d{2})$")
ALLOWED_SPLITS = {"dev", "test"}
ALLOWED_SOURCE_TYPES = {"human_recording", "synthetic_tts"}


def nfc(value: str) -> str:
    """Canonicalize text and filenames because macOS may expose NFD filenames."""
    return unicodedata.normalize("NFC", value)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def review_path(team_dir: Path, requested: Path | None = None) -> Path:
    """Use the required review file, or the observed audit-extended file explicitly."""
    path = requested or team_dir / "voice_group_review.csv"
    if path.is_file():
        return path
    extended = team_dir / "voice_group_review_with_audit.csv"
    if requested is None and extended.is_file():
        return extended
    return path


@dataclass
class TeamValidation:
    script_rows: list[dict[str, str]]
    review_rows: list[dict[str, str]]
    audio_by_name: dict[str, Path]
    split_by_id: dict[str, str]
    issues: list[tuple[str, str]]
    split_basis: str


def validate_team_data(
    team_dir: Path,
    review_file: Path | None = None,
    *,
    allow_sentence_split: bool = False,
) -> TeamValidation:
    """Validate metadata and file linkage without inferring speaker/source labels."""
    script_path = team_dir / "script_with_voice_filenames.csv"
    resolved_review_path = review_path(team_dir, review_file)
    issues: list[tuple[str, str]] = []
    try:
        script_rows = read_csv(script_path)
    except OSError as exc:
        return TeamValidation([], [], {}, {}, [("", f"Cannot read script CSV: {exc}")], "")
    try:
        review_rows = read_csv(resolved_review_path)
    except OSError as exc:
        return TeamValidation(
            script_rows, [], {}, {}, [("", f"Cannot read review CSV {resolved_review_path}: {exc}")], ""
        )

    script_by_id: dict[str, dict[str, str]] = {}
    review_by_id: dict[str, dict[str, str]] = {}
    for label, rows, index in (
        ("script", script_rows, script_by_id),
        ("review", review_rows, review_by_id),
    ):
        for row in rows:
            row_id = (row.get("id") or "").strip()
            if not row_id:
                issues.append(("", f"{label} row has no id"))
            elif row_id in index:
                issues.append((row_id, f"duplicate id in {label} CSV"))
            else:
                index[row_id] = row
    if set(script_by_id) != set(review_by_id):
        for row_id in sorted(set(script_by_id) - set(review_by_id)):
            issues.append((row_id, "id is missing from review CSV"))
        for row_id in sorted(set(review_by_id) - set(script_by_id)):
            issues.append((row_id, "id is missing from script CSV"))

    for row_id in sorted(set(script_by_id) & set(review_by_id)):
        script = script_by_id[row_id]
        review = review_by_id[row_id]
        for field in ("language", "filename"):
            if nfc((script.get(field) or "").strip()) != nfc((review.get(field) or "").strip()):
                issues.append((row_id, f"{field} differs between script and review CSV"))
        if nfc(script.get("text", "")) != nfc(review.get("reference_text", "")):
            issues.append((row_id, "text differs from reference_text after NFC normalization"))
        if nfc(script.get("voice_group_suggestion", "")) != nfc(review.get("voice_group_suggestion", "")):
            issues.append((row_id, "voice_group_suggestion differs between script and review CSV"))
        language = (script.get("language") or "").strip()
        match = ID_PATTERN.fullmatch(row_id)
        expected_prefix = LANGUAGE_PREFIXES.get(language)
        if match is None or expected_prefix is None or match.group(1) != expected_prefix:
            issues.append((row_id, "id prefix does not match language"))
    if len(script_rows) != 54:
        issues.append(("", f"expected 54 script rows, found {len(script_rows)}"))
    by_sentence: dict[str, set[str]] = {}
    for row_id in script_by_id:
        match = ID_PATTERN.fullmatch(row_id)
        if match:
            by_sentence.setdefault(match.group(2), set()).add(match.group(1))
    for sentence, languages in sorted(by_sentence.items()):
        if languages != {"hi", "kn", "en"}:
            issues.append(("", f"sentence {sentence} is missing one or more language versions"))

    audio_dir = team_dir / "audio"
    audio_paths = sorted(
        path for path in audio_dir.iterdir()
        if path.is_file() and not path.name.startswith(".")
    ) if audio_dir.is_dir() else []
    audio_by_name: dict[str, Path] = {}
    for path in audio_paths:
        key = nfc(path.name)
        if key in audio_by_name:
            issues.append(("", f"multiple audio files normalize to filename {key!r}"))
        else:
            audio_by_name[key] = path
    referenced: set[str] = set()
    for row_id, row in script_by_id.items():
        filename = nfc((row.get("filename") or "").strip())
        matches = [path for path in audio_paths if nfc(path.name) == filename]
        if len(matches) != 1:
            issues.append((row_id, f"filename resolves to {len(matches)} audio files: {filename!r}"))
        else:
            referenced.add(filename)
    for filename in sorted(set(audio_by_name) - referenced):
        issues.append(("", f"unreferenced audio file: {filename!r}"))

    speakers: dict[str, set[str]] = {}
    valid_speakers: set[str] = set()
    splits: dict[str, str] = {}
    for row_id, row in review_by_id.items():
        speaker = (row.get("speaker_id_confirmed") or "").strip()
        split = (row.get("split") or "").strip().lower()
        source_type = (row.get("source_type") or "").strip()
        quiet_room = (row.get("quiet_room") or "").strip().lower()
        if not SPEAKER_PATTERN.fullmatch(speaker):
            issues.append((row_id, "speaker_id_confirmed must match spk_[a-z0-9]+"))
        else:
            valid_speakers.add(speaker)
            speakers.setdefault(speaker, set()).add(split)
        if source_type not in ALLOWED_SOURCE_TYPES:
            issues.append((row_id, "source_type must be human_recording or synthetic_tts"))
        elif source_type == "human_recording" and quiet_room not in {"yes", "no"}:
            issues.append((row_id, "quiet_room must be yes or no for human_recording"))
        elif source_type == "synthetic_tts" and quiet_room and quiet_room not in {"yes", "no"}:
            issues.append((row_id, "quiet_room must be yes, no, or blank"))
        if split and split not in ALLOWED_SPLITS:
            issues.append((row_id, "split must be dev or test"))
        elif split:
            splits[row_id] = split

    source_types = {
        (row.get("source_type") or "").strip()
        for row in review_by_id.values()
        if (row.get("source_type") or "").strip()
    }
    split_basis = (
        "voice_disjoint_synthetic"
        if source_types == {"synthetic_tts"}
        else "speaker_disjoint"
    )
    if len(valid_speakers) < 2:
        if not allow_sentence_split:
            issues.append(("", "fewer than two confirmed speakers; use --allow-sentence-split only if approved"))
        else:
            split_basis = "sentence_disjoint_NOT_speaker_disjoint"
    if allow_sentence_split:
        split_basis = "sentence_disjoint_NOT_speaker_disjoint"
        splits = {}
        for row_id in review_by_id:
            match = ID_PATTERN.fullmatch(row_id)
            if match:
                sentence = int(match.group(2))
                splits[row_id] = "dev" if sentence % 2 else "test"

    if split_basis in {"speaker_disjoint", "voice_disjoint_synthetic"}:
        for speaker, speaker_splits in speakers.items():
            nonempty = speaker_splits - {""}
            if len(nonempty) > 1:
                issues.append(("", f"confirmed speaker/voice {speaker} is assigned to multiple splits"))
        for row_id, row in review_by_id.items():
            speaker = (row.get("speaker_id_confirmed") or "").strip()
            if SPEAKER_PATTERN.fullmatch(speaker) and not (row.get("split") or "").strip():
                issues.append((row_id, "split is required for source-disjoint evaluation"))

    counts: Counter[tuple[str, str]] = Counter()
    for row_id, split in splits.items():
        language = (script_by_id.get(row_id, {}).get("language") or "").strip()
        counts[(split, language)] += 1
    for split in ("dev", "test"):
        if not any(value == split for value in splits.values()):
            issues.append(("", f"{split} split is empty"))
        for language in LANGUAGE_PREFIXES:
            count = counts[(split, language)]
            if count < 5:
                issues.append(("", f"UNDERPOWERED: {split}/{language} has {count} clips; fewer than 5"))
    return TeamValidation(script_rows, review_rows, audio_by_name, splits, issues, split_basis)


def count_review_rows(review_rows: list[dict[str, str]], splits: dict[str, str]) -> Counter[tuple[str, str, str, str]]:
    """Count only human-provided labels; missing values remain explicitly unknown."""
    counts: Counter[tuple[str, str, str, str]] = Counter()
    for row in review_rows:
        row_id = (row.get("id") or "").strip()
        counts[
            (
                splits.get(row_id, (row.get("split") or "").strip() or "(blank)"),
                (row.get("language") or "").strip() or "(blank)",
                (row.get("speaker_id_confirmed") or "").strip() or "(blank)",
                (row.get("source_type") or "").strip() or "(blank)",
            )
        ] += 1
    return counts
