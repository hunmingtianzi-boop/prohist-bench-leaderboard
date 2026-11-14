"""Standardized CLI for validating, auditing, scoring, and registering ProHist-Bench submissions."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
import subprocess
import tempfile
from typing import Any
from urllib import request

from schema import (
    CATEGORIES,
    LEADERBOARD_DIR,
    MAX_ANSWER_CHARS,
    load_benchmark_rows,
    load_split_ids,
    load_submission_jsonl,
    validate_submission_meta,
)

SUBMISSIONS_PATH = LEADERBOARD_DIR / "submissions.json"

JUDGE_SYSTEM_PROMPT = (
    "你是中国古代史开放题的阅卷员。每题提供题干、参考答案、带正负分值的评分条目，以及一份匿名考生答案。"
    "仅使用这些输入进行评阅。勿把参考答案中未在考生答案出现的表述当作考生说过的内容。"
    "按语义判断，接受史实等价的改写。正分条目在答案实质覆盖该条目的关键内容时计入，"
    "只泛泛提及关键词、猜测或同时给出互相矛盾的说法均不计入。"
    "负分条目仅在答案实际出现该条所说的错误时计入，遗漏加分点本身不触发扣分。"
    "每条至多记一次。不自行增删条目或改变分值。"
    "返回 JSON 对象 {\"id\": <整数题号>, \"hits\": [<触发的条目index整数数组>]}。"
)

FORBIDDEN_FILE_PATTERNS = (
    "20251230-ichbench_qa_360_tagging.csv",
    "reference_answer",
    "rubrics",
)
HARDCODED_QID_RE = re.compile(r"\b(?:qid|question_id)\s*==\s*\d+\b")


def summarize_submission(records: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Compute structural & length statistics for a validated submission."""
    n = len(records)
    lengths = [r["full_chars"] for r in records.values()]
    over_cap = sum(1 for r in records.values() if r["over_cap_chars"] > 0)
    empty = sum(1 for r in records.values() if r["empty"])
    with_cit = sum(1 for r in records.values() if r["has_citations"])
    return {
        "count": n,
        "empty_count": empty,
        "avg_chars": round(sum(lengths) / max(1, n), 1),
        "max_chars": max(lengths, default=0),
        "over_cap_count": over_cap,
        "max_answer_chars_cap": MAX_ANSWER_CHARS,
        "citation_coverage_pct": round(with_cit * 100.0 / max(1, n), 1),
    }


def audit_repository(repo_dir: Path) -> dict[str, Any]:
    """Scan a participant repository for forbidden benchmark leakage or per-question hardcoding."""
    if not repo_dir.exists() or not repo_dir.is_dir():
        raise FileNotFoundError(f"Repository directory not found: {repo_dir}")

    findings: list[dict[str, Any]] = []
    scanned_files = 0
    for path in sorted(repo_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(repo_dir)).replace("\\", "/")
        if any(part.startswith(".") or part in {"node_modules", "__pycache__", ".venv", "venv"} for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".js", ".ts", ".yaml", ".yml", ".json", ".toml", ".sh", ".ps1"}:
            continue
        scanned_files += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for pat in FORBIDDEN_FILE_PATTERNS:
            if pat in text:
                findings.append({"severity": "HIGH", "file": rel, "rule": "forbidden_reference", "detail": pat})
        for match in HARDCODED_QID_RE.finditer(text):
            findings.append({"severity": "HIGH", "file": rel, "rule": "hardcoded_qid", "detail": match.group(0)})

    status = "FAIL" if any(f["severity"] == "HIGH" for f in findings) else "PASS"
    return {"status": status, "scanned_files": scanned_files, "findings": findings}


def _mock_judge_item(item: dict[str, Any]) -> dict[str, Any]:
    """Deterministic offline rubric matcher for CI/unit testing (never used for official scores)."""
    ans = item["answer"]
    hits: list[int] = []
    for crit in item["criteria"]:
        if crit["points"] <= 0:
            continue
        tokens = [t for t in re.split(r"[\s，。、；：（）()“”\"']+", crit["criterion"]) if len(t) >= 2]
        if tokens and any(tok in ans for tok in tokens[:3]):
            hits.append(crit["index"])
    return {"id": item["id"], "hits": hits}


def _openai_judge_item(item: dict[str, Any], model: str, api_base: str, api_key: str) -> dict[str, Any]:
    """Score one question via OpenAI-compatible Chat Completions JSON API."""
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(item, ensure_ascii=False)},
        ],
    }
    req = request.Request(
        f"{api_base.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with request.urlopen(req, timeout=90) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return {"id": int(parsed["id"]), "hits": [int(x) for x in parsed.get("hits", [])]}


def _codex_judge_item(item: dict[str, Any], model: str) -> dict[str, Any]:
    """Score one question via workspace Codex CLI (if installed)."""
    codex_exe = os.environ.get(
        "CODEX_EXE",
        r"C:\Program Files\WindowsApps\OpenAI.Codex_26.1106.6628.0_x64__2p2nqsd0c76g0\app\resources\codex.exe",
    )
    if not Path(codex_exe).exists():
        raise FileNotFoundError(f"Codex CLI not found at {codex_exe}; set CODEX_EXE or use --backend openai")

    schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "hits": {"type": "array", "items": {"type": "integer"}}},
        "required": ["id", "hits"],
        "additionalProperties": False,
    }
    prompt = JUDGE_SYSTEM_PROMPT + "\n\n" + json.dumps(item, ensure_ascii=False)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        schema_path = tmp_dir / "schema.json"
        out_path = tmp_dir / "out.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        cmd = [
            codex_exe,
            "exec",
            "--skip-git-repo-check",
            "-m",
            model,
            "--output-schema",
            str(schema_path),
            "-o",
            str(out_path),
            prompt,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        parsed = json.loads(out_path.read_text(encoding="utf-8"))
    return {"id": int(parsed["id"]), "hits": [int(x) for x in parsed.get("hits", [])]}


def bootstrap_ci(values: list[float], n_boot: int = 2000, seed: int = 42) -> list[float]:
    """Return 95% bootstrap confidence interval `[low, high]` for the mean of `values`."""
    if not values:
        return [0.0, 0.0]
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    return [round(means[int(0.025 * n_boot)], 2), round(means[int(0.975 * n_boot)], 2)]


def score_submission(
    submission_path: Path,
    split: str,
    backend: str = "mock",
    judge_model: str = "gpt-6-astra",
    workers: int = 4,
) -> dict[str, Any]:
    """Validate and score a `submission.jsonl` on `split`, returning full `score_report` dict."""
    expected_ids = set(load_split_ids(split))
    records = load_submission_jsonl(submission_path, expected_ids)
    gold = load_benchmark_rows(split=split, include_gold=True)
    stats = summarize_submission(records)

    items = [
        {
            "id": qid,
            "category": gold[qid]["category"],
            "question": gold[qid]["question"],
            "reference_answer": gold[qid]["reference_answer"],
            "criteria": gold[qid]["criteria"],
            "answer": records[qid]["answer"],
        }
        for qid in sorted(expected_ids)
    ]

    api_key = os.environ.get("OPENAI_API_KEY", "")
    api_base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    if backend == "openai" and not api_key:
        raise EnvironmentError("OPENAI_API_KEY is required for --backend openai")

    def _eval_one(it: dict[str, Any]) -> dict[str, Any]:
        if backend == "mock":
            res = _mock_judge_item(it)
        elif backend == "openai":
            res = _openai_judge_item(it, model=judge_model, api_base=api_base, api_key=api_key)
        elif backend == "codex":
            res = _codex_judge_item(it, model=judge_model)
        else:
            raise ValueError(f"Unknown backend: {backend}")

        weights = {c["index"]: c["points"] for c in it["criteria"]}
        possible = sum(p for p in weights.values() if p > 0)
        hits = sorted({int(h) for h in res["hits"] if int(h) in weights})
        earned = sum(weights[h] for h in hits)
        rs = round(max(0, earned) * 100.0 / possible, 4)
        return {"id": it["id"], "category": it["category"], "hits": hits, "raw": earned, "possible": possible, "rs": rs}

    per_question: list[dict[str, Any]] = []
    if backend == "mock" or workers <= 1:
        per_question = [_eval_one(it) for it in items]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_eval_one, it) for it in items]
            for fut in as_completed(futs):
                per_question.append(fut.result())
        per_question.sort(key=lambda r: r["id"])

    all_scores = [r["rs"] for r in per_question]
    by_category: dict[str, float] = {}
    for cat in CATEGORIES:
        cat_scores = [r["rs"] for r in per_question if r["category"] == cat]
        if cat_scores:
            by_category[cat] = round(sum(cat_scores) / len(cat_scores), 2)

    return {
        "split": split,
        "judge_backend": backend,
        "judge_model": judge_model if backend != "mock" else "mock-offline",
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_rs": round(sum(all_scores) / len(all_scores), 2),
        "ci_95": bootstrap_ci(all_scores),
        "by_category": by_category,
        "submission_stats": stats,
        "per_question": per_question,
    }


def register_submission(report: dict[str, Any], meta: dict[str, Any], submissions_path: Path = SUBMISSIONS_PATH) -> dict[str, Any]:
    """Insert or update an entry in `submissions.json` and sort entries by split & overall_rs."""
    validate_submission_meta(meta)
    if meta["split"] != report["split"]:
        raise ValueError(f"Split mismatch: meta={meta['split']} vs report={report['split']}")

    if submissions_path.exists():
        board = json.loads(submissions_path.read_text(encoding="utf-8"))
    else:
        board = {"benchmark": "ProHist-Bench (科举史)", "track": "track3_open", "entries": []}

    entry = {
        "submission_id": meta["submission_id"],
        "team_name": meta["team_name"],
        "method_name": meta["method_name"],
        "track": meta["track"],
        "split": meta["split"],
        "model": meta["model"],
        "corpus_description": meta["corpus_description"],
        "repo_url": meta.get("repo_url", ""),
        "commit_sha": meta.get("commit_sha", ""),
        "overall_rs": report["overall_rs"],
        "ci_95": report.get("ci_95"),
        "by_category": report.get("by_category", {}),
        "avg_chars": report.get("submission_stats", {}).get("avg_chars"),
        "citation_coverage_pct": report.get("submission_stats", {}).get("citation_coverage_pct"),
        "judge_model": report.get("judge_model", ""),
        "verified": bool(meta.get("verified", False)),
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    entries = [e for e in board.get("entries", []) if not (e["submission_id"] == entry["submission_id"] and e["split"] == entry["split"])]
    entries.append(entry)
    entries.sort(key=lambda e: (e["split"], -float(e["overall_rs"]), e["submission_id"]))
    board["entries"] = entries
    board["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    submissions_path.parent.mkdir(parents=True, exist_ok=True)
    submissions_path.write_text(json.dumps(board, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description="ProHist-Bench Track 3 Leaderboard CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser("validate", help="Validate a submission.jsonl file")
    p_val.add_argument("--submission", type=Path, required=True)
    p_val.add_argument("--split", default="dev60", choices=("dev60", "holdout60", "test300"))

    p_aud = sub.add_parser("audit-repo", help="Static anti-cheating scan on a participant repo")
    p_aud.add_argument("--repo-dir", type=Path, required=True)

    p_sco = sub.add_parser("score", help="Score a submission.jsonl against benchmark rubrics")
    p_sco.add_argument("--submission", type=Path, required=True)
    p_sco.add_argument("--split", default="dev60", choices=("dev60", "holdout60", "test300"))
    p_sco.add_argument("--backend", default="openai", choices=("mock", "openai", "codex"))
    p_sco.add_argument("--judge-model", default="gpt-6-astra")
    p_sco.add_argument("--workers", type=int, default=4)
    p_sco.add_argument("--out", type=Path, default=None)

    p_reg = sub.add_parser("register", help="Register a score_report.json into leaderboard/submissions.json")
    p_reg.add_argument("--report", type=Path, required=True)
    p_reg.add_argument("--meta", type=Path, required=True)

    args = parser.parse_args()
    if args.cmd == "validate":
        expected = set(load_split_ids(args.split))
        recs = load_submission_jsonl(args.submission, expected)
        print(json.dumps({"status": "VALID", "split": args.split, **summarize_submission(recs)}, ensure_ascii=False, indent=2))
    elif args.cmd == "audit-repo":
        res = audit_repository(args.repo_dir)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if res["status"] != "PASS":
            raise SystemExit(1)
    elif args.cmd == "score":
        report = score_submission(args.submission, args.split, args.backend, args.judge_model, args.workers)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = {k: v for k, v in report.items() if k != "per_question"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.cmd == "register":
        report = json.loads(args.report.read_text(encoding="utf-8"))
        meta = json.loads(args.meta.read_text(encoding="utf-8"))
        entry = register_submission(report, meta)
        print(json.dumps(entry, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
