"""Unit tests for ProHist-Bench Track 3 leaderboard toolkit."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

LEADERBOARD_DIR = Path(__file__).resolve().parent.parent
if str(LEADERBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(LEADERBOARD_DIR))

from eval_cli import audit_repository, register_submission, score_submission
from export_questions import export_split_questions
from schema import CSV_PATH, load_split_ids, load_submission_jsonl


class LeaderboardTests(unittest.TestCase):
    def test_exported_question_files_are_clean_and_complete(self) -> None:
        expected_counts = {"dev60": 60, "holdout60": 60, "test300": 300}
        for split, count in expected_counts.items():
            qfile = LEADERBOARD_DIR / "data" / f"questions_{split}.jsonl"
            self.assertTrue(qfile.exists(), f"Missing exported file: {qfile}")
            lines = [ln for ln in qfile.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), count)
            for ln in lines:
                obj = json.loads(ln)
                self.assertEqual(set(obj.keys()), {"id", "category", "question"})

    @unittest.skipUnless(CSV_PATH.exists(), "Requires private benchmark CSV")
    def test_export_questions_strips_gold_and_dynasty_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "questions_dev60.jsonl"
            count = export_split_questions("dev60", out_path)
            self.assertEqual(count, 60)
            lines = out_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 60)
            for line in lines:
                obj = json.loads(line)
                self.assertEqual(set(obj.keys()), {"id", "category", "question"})

    @unittest.skipUnless(CSV_PATH.exists(), "Requires private benchmark CSV")
    def test_validate_score_and_register_mock_flow(self) -> None:
        ids = load_split_ids("dev60")
        self.assertEqual(len(ids), 60)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            sub_path = tmp_dir / "submission.jsonl"
            with sub_path.open("w", encoding="utf-8") as fh:
                for qid in ids:
                    fh.write(
                        json.dumps(
                            {
                                "id": qid,
                                "answer": "科举制度始于隋唐，宋代糊名誊录，明清八股取士，乡试中试者称举人。",
                                "citations": [{"source": "中国科举辞典", "loc": "p.1", "quote": "举人"}],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

            records = load_submission_jsonl(sub_path, set(ids))
            self.assertEqual(len(records), 60)

            report = score_submission(sub_path, split="dev60", backend="mock")
            self.assertEqual(report["split"], "dev60")
            self.assertGreaterEqual(report["overall_rs"], 0.0)
            self.assertEqual(len(report["per_question"]), 60)
            self.assertEqual(report["submission_stats"]["citation_coverage_pct"], 100.0)

            board_path = tmp_dir / "submissions.json"
            meta = {
                "submission_id": "test-team-v1",
                "team_name": "Test Team",
                "method_name": "Test Method",
                "track": "track3_open",
                "split": "dev60",
                "model": "test-model",
                "corpus_description": "test corpus",
                "repo_url": "https://github.com/example/test-repo",
                "commit_sha": "deadbeef",
                "verified": False,
            }
            entry = register_submission(report, meta, submissions_path=board_path)
            self.assertFalse(entry["verified"])
            saved = json.loads(board_path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["entries"]), 1)

    def test_audit_repo_detects_leakage_and_hardcoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "clean.py").write_text("def solve(q): return q\n", encoding="utf-8")
            self.assertEqual(audit_repository(repo)["status"], "PASS")

            (repo / "bad.py").write_text("if qid == 104:\n    pass\n", encoding="utf-8")
            res = audit_repository(repo)
            self.assertEqual(res["status"], "FAIL")
            self.assertEqual(res["findings"][0]["rule"], "hardcoded_qid")


if __name__ == "__main__":
    unittest.main()
