# 提交格式规范（Submission Spec）

每次提交包含两个文件：`submission.jsonl`（逐题作答）与 `submission_meta.json`（方案声明）。

## 1. `submission.jsonl`

每行一个 JSON 对象，必须精确覆盖对应 `split` 的全部题号（无缺失、无重复、无多余题号）：

```json
{
  "id": 1,
  "answer": "举人原指汉代郡国守相察举荐举之人，隋唐至宋逐渐指代地方解送参加礼部省试的士子，明清时期定型为乡试考中者（俗称孝廉），取得参加会试资格……",
  "citations": [
    {"source": "中国科举辞典", "loc": "p.123", "quote": "……"}
  ],
  "latency_ms": 1420,
  "tokens": 860
}
```

- `id` (`int`, 必填)：题目编号。
- `answer` (`str`, 必填)：最终作答文本。裁判前会按 `MAX_ANSWER_CHARS = 1600` 截断。
- `citations` (`list[dict]`, 选填但强烈建议)：用于核验“有据引用率”与可追溯性。
- `latency_ms` / `tokens` (`int`, 选填)：用于记录工程开销。

## 2. `submission_meta.json`

```json
{
  "submission_id": "team-x-shc-v1-dev60",
  "team_name": "Team X",
  "method_name": "Multi-Agent HistRAG v1",
  "track": "track3_open",
  "split": "dev60",
  "model": "gpt-6.1-sol",
  "corpus_description": "二十四史选举志 + 中国科举辞典 + 中国科举制度通史",
  "repo_url": "https://github.com/your-org/your-prohist-solution",
  "commit_sha": "a1b2c3d4",
  "verified": false
}
```
