"""Export clean, tag-stripped benchmark questions (no answers, rubrics, or dynasty tags)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from schema import LEADERBOARD_DIR, load_benchmark_rows


def export_split_questions(split: str, out_path: Path) -> int:
    """Write `{id, category, question}` JSONL for `split` and return exported count."""
    rows = load_benchmark_rows(split=split, include_gold=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows.values():
            clean = {
                "id": row["id"],
                "category": row["category"],
                "question": row["question"],
            }
            fh.write(json.dumps(clean, ensure_ascii=False) + "\n")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export clean ProHist-Bench questions for participants.")
    parser.add_argument("--split", default="dev60", choices=("dev60", "holdout60", "test300"))
    parser.add_argument("--out", type=Path, default=None, help="Output JSONL path")
    args = parser.parse_args()

    out_path = args.out or (LEADERBOARD_DIR / "data" / f"questions_{args.split}.jsonl")
    count = export_split_questions(args.split, out_path)
    print(f"Exported {count} clean questions for split={args.split} -> {out_path}")


if __name__ == "__main__":
    main()
