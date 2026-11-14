# ProHist-Bench（科举史）开放刷榜系统 · Track 3

欢迎参与 **ProHist-Bench 科举史问答基准（Track 3：开放语料 / Agent 自由解法）**！

本目录提供一套完整的**轻量闭环刷榜工具链**，可直接作为 GitHub 竞赛仓库使用：参与者按规则拉取题干、运行自己的解法、本地校验并声明分数；**刷新最高分者须提交 GitHub 仓库供主办方复现核验**。

## 快速开始

### 1. 导出干净题干（剥离答案、Rubrics 与朝代标签）

```powershell
python -X utf8 leaderboard/export_questions.py --split dev60
# 输出: leaderboard/data/questions_dev60.jsonl
```

### 2. 运行你的解法，生成 `submission.jsonl`

格式要求见 [SUBMISSION_SPEC.md](SUBMISSION_SPEC.md)，竞赛红线见 [RULES.md](RULES.md)。

### 3. 校验格式 & 静态反作弊扫描

```powershell
python -X utf8 leaderboard/eval_cli.py validate --submission path/to/submission.jsonl --split dev60
python -X utf8 leaderboard/eval_cli.py audit-repo --repo-dir path/to/your-repo
```

### 4. 标准化评分与上榜登记

```powershell
# 使用 OpenAI 兼容裁判接口评分（或 --backend codex；CI/联调可用 --backend mock）
python -X utf8 leaderboard/eval_cli.py score --submission path/to/submission.jsonl --split dev60 --backend openai --out score_report.json

# 注册到排行榜 submissions.json
python -X utf8 leaderboard/eval_cli.py register --report score_report.json --meta path/to/submission_meta.json
```

### 5. 查看排行榜

启动研究地图看板（`http://127.0.0.1:18766/`），点击顶部导航 **「排行榜」** 即可查看各赛道/题集的实时排名、分题型得分、字符数与核验状态。
