"""Drop-in `codex exec` shim backed by `devin.exe -p` for benchmark scripts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

MODEL_MAP = {
    ("gpt-6-astra", "low"): "gpt-6-astra-low",
    ("gpt-6-astra", "medium"): "gpt-6-astra-medium",
    ("gpt-6-astra", "high"): "gpt-6-astra-high",
    ("gpt-6.1-sol", "low"): "gpt-6-sol-low",
    ("gpt-6.1-sol", "medium"): "gpt-6-sol-medium",
    ("gpt-6.1-sol", "high"): "gpt-6-sol-high",
    ("gpt-6-sol", "low"): "gpt-6-sol-low",
    ("gpt-6-sol", "medium"): "gpt-6-sol-medium",
    ("gpt-6-sol", "high"): "gpt-6-sol-high",
}


def find_devin_exe() -> str:
    found = shutil.which("devin") or shutil.which("devin.exe")
    if found:
        return found
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "devin.exe"
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError("devin.exe not found in PATH or WinGet Links")


FAST_MODEL_MAP = {
    "gpt-6-astra": "gpt-6-luna-medium-priority",
    "gpt-6.1-sol": "gpt-6-sol-low-priority",
    "gpt-6-sol": "gpt-6-sol-low-priority",
}


def resolve_devin_model(model: str, effort: str) -> str:
    override = os.environ.get("DEVIN_BENCH_MODEL", "").strip()
    if override:
        return override
    if os.environ.get("DEVIN_FAST", "").strip().lower() in {"1", "true", "yes"}:
        fast_hit = FAST_MODEL_MAP.get(model.strip().lower())
        if fast_hit:
            return fast_hit
        return "gpt-6-luna-medium-priority"
    key = (model.strip().lower(), effort.strip().lower())
    if key in MODEL_MAP:
        return MODEL_MAP[key]
    if model.startswith(("gpt-6-", "claude-", "gemini-", "deepseek-", "kimi-", "swe-")):
        return model
    return "gpt-6-sol-high"


def extract_json_text(raw: str) -> str:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        json.loads(candidate)
        return candidate
    json.loads(text)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("subcmd", nargs="?")
    parser.add_argument("-m", "--model", default="gpt-6.1-sol")
    parser.add_argument("-c", "--config", action="append", default=[])
    parser.add_argument("--output-schema", type=Path, default=None)
    parser.add_argument("-o", "--output", type=Path, default=None)
    args, rest = parser.parse_known_args()

    effort = "medium"
    for item in args.config:
        if "model_reasoning_effort" in item:
            effort = item.split("=", 1)[-1].strip().strip("\"'")

    prompt = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not prompt.strip() and rest:
        prompt = rest[-1]
    if not prompt.strip():
        print("Empty prompt", file=sys.stderr)
        return 2

    if args.output_schema and args.output_schema.exists():
        schema_txt = args.output_schema.read_text(encoding="utf-8")
        prompt = (
            f"{prompt}\n\n[STRICT OUTPUT REQUIREMENT]\n"
            f"Return ONLY a valid JSON object matching this JSON Schema (no markdown fences, no extra text):\n"
            f"{schema_txt}\n"
        )

    devin_exe = find_devin_exe()
    devin_model = resolve_devin_model(args.model, effort)

    with tempfile.TemporaryDirectory(prefix="devin_shim_") as td:
        pfile = Path(td) / "prompt.txt"
        pfile.write_text(prompt, encoding="utf-8")
        cmd = [
            devin_exe,
            "-p",
            "--prompt-file",
            str(pfile),
            "--model",
            devin_model,
            "--respect-workspace-trust",
            "false",
            "--verbosity",
            "0",
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            return proc.returncode

        out_text = proc.stdout.strip()
        if args.output_schema:
            out_text = extract_json_text(out_text)

        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(out_text, encoding="utf-8")
        else:
            print(out_text)

        # Emit minimal Codex-compatible JSONL event on stdout for callers that parse stdout.jsonl
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": out_text}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
