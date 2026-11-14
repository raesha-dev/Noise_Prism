"""Validate the team corpus and print every human-review blocker."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .team_data import count_review_rows, review_path, validate_team_data
except ImportError:
    from team_data import count_review_rows, review_path, validate_team_data

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-dir", type=Path, default=ROOT / "data" / "team")
    parser.add_argument("--review", type=Path)
    parser.add_argument("--allow-sentence-split", action="store_true")
    args = parser.parse_args()
    chosen_review = review_path(args.team_dir, args.review)
    if chosen_review.name == "voice_group_review_with_audit.csv" and args.review is None:
        print(f"Using available review sheet: {chosen_review}")
        print("Expected voice_group_review.csv is absent; the review sheet was not modified.")
    validation = validate_team_data(
        args.team_dir, chosen_review, allow_sentence_split=args.allow_sentence_split
    )
    if validation.split_basis:
        print(f"split_basis={validation.split_basis}")
    else:
        print("split_basis=unresolved")
    counts = count_review_rows(validation.review_rows, validation.split_by_id)
    print("Clip counts by split x language x speaker x source_type:")
    print("split,language,speaker_id_confirmed,source_type,clips")
    for (split, language, speaker, source_type), count in sorted(counts.items()):
        print(f"{split},{language},{speaker},{source_type},{count}")
    for row_id, message in validation.issues:
        prefix = f"{row_id}: " if row_id else ""
        print(f"BLOCKING: {prefix}{message}")
    blockers = sum("UNDERPOWERED:" not in message for _, message in validation.issues)
    print(f"Blocking rows/issues: {blockers}")
    if blockers:
        print("No speaker, split, source type, or quiet-room label was inferred.")
        return 1
    if validation.issues:
        print("UNDERPOWERED strata are reported above; do not make powered claims for them.")
    print("Team metadata and NFC filename linkage passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
