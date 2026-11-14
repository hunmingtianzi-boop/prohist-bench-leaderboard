"""Shared constants, split loaders, rubric parser, and submission validators for ProHist-Bench Leaderboard."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LEADERBOARD_DIR = Path(__file__).resolve().parent
CSV_PATH = ROOT / "20251230-ichbench_qa_360_tagging.csv"

SPLIT_FILES: dict[str, Path] = {
    "dev60": ROOT / "rag_exp" / "split" / "dev_ids.json",
    "test300": ROOT / "rag_exp" / "split" / "test_ids.json",
    "holdout60": ROOT / "swarm_fp_exp" / "v2" / "retrieval" / "holdout60_ids.json",
}

MAX_ANSWER_CHARS = 1600
CATEGORIES = ("名词解释", "简答题", "论述题")
VALID_TRACKS = ("track3_open",)

WEIGHT_RE = re.compile(r"\{\s*([+-]?\d+)\s*\}")


def load_split_ids(split: str) -> list[int]:
    """Return sorted integer question IDs for a named benchmark split."""
    if split not in SPLIT_FILES:
        raise ValueError(f"Unknown split {split!r}; expected one of {sorted(SPLIT_FILES)}")
    path = SPLIT_FILES[split]
    if not path.exists():
        exported = LEADERBOARD_DIR / "data" / f"questions_{split}.jsonl"
        if exported.exists():
            return sorted(
                int(json.loads(line)["id"])
                for line in exported.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        raise FileNotFoundError(f"Split file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        for key in ("ids", "qids", "question_ids", "holdout_ids", "dev_ids", "test_ids"):
            if key in raw and isinstance(raw[key], list):
                raw = raw[key]
                break
        else:
            raise ValueError(f"Unsupported dict split format in {path}")
    if not isinstance(raw, list):
        raise ValueError(f"Split file {path} must contain a JSON list of question IDs")
    return sorted({int(x) for x in raw})


def parse_criteria(rubrics_text: str) -> list[dict[str, Any]]:
    """Parse `{+N}` / `{-N}` weighted criteria lines from CSV rubrics."""
    items: list[dict[str, Any]] = []
    for line in str(rubrics_text or "").splitlines():
        match = WEIGHT_RE.search(line)
        if match is None:
            continue
        desc = line[: match.start()].strip()
        desc = re.sub(r"^\d+\s*[、.．)）]\s*", "", desc)
        items.append({"index": len(items) + 1, "points": int(match[1]), "criterion": desc})
    if not items or not any(item["points"] > 0 for item in items):
        raise ValueError("Rubrics must contain at least one positive-weight criterion")
    return items


def load_benchmark_rows(split: str | None = None, include_gold: bool = False) -> dict[int, dict[str, Any]]:
    """Load benchmark questions from CSV, optionally filtered to a split and/or with gold rubrics."""
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Benchmark CSV not found: {CSV_PATH}")
    allowed_ids: set[int] | None = set(load_split_ids(split)) if split else None

    rows: dict[int, dict[str, Any]] = {}
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            qid = int(row["id"])
            if allowed_ids is not None and qid not in allowed_ids:
                continue
            item: dict[str, Any] = {
                "id": qid,
                "category": str(row["category"]).strip(),
                "question": str(row["question"]).strip(),
            }
            if include_gold:
                item["reference_answer"] = str(row["reference_answer"]).strip()
                item["rubrics"] = str(row["rubrics"]).strip()
                item["criteria"] = parse_criteria(row["rubrics"])
            rows[qid] = item

    if allowed_ids is not None:
        missing = allowed_ids - set(rows.keys())
        if missing:
            raise ValueError(f"Missing {len(missing)} question IDs in CSV for split {split}: {sorted(missing)[:10]}")
    return dict(sorted(rows.items()))


def load_submission_jsonl(path: Path, expected_ids: set[int]) -> dict[int, dict[str, Any]]:
    """Load and validate a participant `submission.jsonl` file against expected question IDs."""
    if not path.exists():
        raise FileNotFoundError(f"Submission file not found: {path}")

    records: dict[int, dict[str, Any]] = {}
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON ({exc})") from exc
        if not isinstance(obj, dict) or "id" not in obj or "answer" not in obj:
            raise ValueError(f"{path}:{line_no}: each record must be an object with 'id' and 'answer'")
        qid = int(obj["id"])
        if qid in records:
            raise ValueError(f"{path}:{line_no}: duplicate question id {qid}")
        ans = str(obj["answer"] or "")
        citations = obj.get("citations", [])
        if citations is None:
            citations = []
        if not isinstance(citations, list):
            raise ValueError(f"{path}:{line_no}: 'citations' must be a list if provided")
        records[qid] = {
            "id": qid,
            "answer": ans[:MAX_ANSWER_CHARS],
            "full_chars": len(ans),
            "over_cap_chars": max(0, len(ans) - MAX_ANSWER_CHARS),
            "empty": not bool(ans.strip()),
            "citations": citations,
            "has_citations": len(citations) > 0,
            "latency_ms": obj.get("latency_ms"),
            "tokens": obj.get("tokens"),
        }

    missing = expected_ids - set(records.keys())
    extra = set(records.keys()) - expected_ids
    if missing:
        raise ValueError(f"{path}: missing {len(missing)} expected question IDs: {sorted(missing)[:10]}")
    if extra:
        raise ValueError(f"{path}: contains {len(extra)} unexpected question IDs: {sorted(extra)[:10]}")
    return dict(sorted(records.items()))


def validate_submission_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Validate required fields for a leaderboard `submission_meta.json`."""
    required = ("submission_id", "team_name", "method_name", "track", "split", "model", "corpus_description")
    missing = [k for k in required if not str(meta.get(k, "")).strip()]
    if missing:
        raise ValueError(f"submission_meta missing required fields: {missing}")
    if meta["track"] not in VALID_TRACKS:
        raise ValueError(f"Invalid track {meta['track']!r}; expected one of {VALID_TRACKS}")
    if meta["split"] not in SPLIT_FILES:
        raise ValueError(f"Invalid split {meta['split']!r}; expected one of {sorted(SPLIT_FILES)}")
    return meta
