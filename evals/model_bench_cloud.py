#!/usr/bin/env python3
"""Cloud benchmark: same 210-question bank as model_bench.py, but fired at
an OpenAI-compatible cloud endpoint (Kimi K2.6 via the agent gateway).

Fair-comparison notes vs the local run:
- Same questions, same system prompt, same rubric grader.
- temperature=1 (the only value this endpoint allows for k2d6-agent).
- reasoning_effort="low" (endpoint minimum; local gpt-oss ran at "none",
  so the cloud model gets MORE reasoning, not less).

Usage:
    KIMI_API_KEY=... KIMI_BASE_URL=... \
        .venv/bin/python evals/model_bench_cloud.py [--model k2d6-agent] [--limit N]

Results append to evals/model_bench_results_cloud.jsonl; already-answered
questions are skipped, so the run is resumable in slices.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import requests

from model_bench import grade  # same grader = fair comparison

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "model_bench_questions.json"
RESULTS = HERE / "model_bench_results_cloud.jsonl"

SYSTEM = ("You are a precise technical expert. Answer the question "
          "directly and concisely. No preamble, no disclaimers.")


def ask(base: str, key: str, model: str, question: str,
        timeout: int = 180) -> tuple[str, float]:
    t0 = time.time()
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": question},
            ],
            "temperature": 1,
            "reasoning_effort": "low",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
    return (content or ""), time.time() - t0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="k2d6-agent")
    parser.add_argument("--limit", type=int, default=0,
                        help="max NEW questions this run (0 = all remaining)")
    args = parser.parse_args()

    base = os.environ["KIMI_BASE_URL"].rstrip("/")
    key = os.environ["KIMI_API_KEY"]

    questions = json.loads(QUESTIONS.read_text())
    done = set()
    if RESULTS.exists():
        for line in RESULTS.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("model") == args.model:
                    done.add(rec["q"])

    remaining = [q for q in questions if q["q"] not in done]
    if args.limit:
        remaining = remaining[: args.limit]
    print(f"model={args.model}  remaining={len(remaining)}  done={len(done)}")

    with RESULTS.open("a") as out:
        for i, item in enumerate(remaining, 1):
            try:
                answer, secs = ask(base, key, args.model, item["q"])
                passed = grade(answer, item["accept"])
            except Exception as exc:  # noqa: BLE001
                answer, secs, passed = f"ERROR: {exc}", 0.0, False
            rec = {"model": args.model, "cat": item["cat"], "q": item["q"],
                   "pass": passed, "secs": round(secs, 1),
                   "answer": answer[:500]}
            out.write(json.dumps(rec) + "\n")
            out.flush()
            mark = "ok" if passed else "XX"
            print(f"[{i:3d}/{len(remaining)}] {mark} {item['cat']:9s} "
                  f"{secs:5.1f}s  {item['q'][:60]}", flush=True)

    records = [json.loads(l) for l in RESULTS.read_text().splitlines()
               if l.strip() and json.loads(l).get("model") == args.model]
    by_cat: dict[str, list[bool]] = {}
    for r in records:
        by_cat.setdefault(r["cat"], []).append(r["pass"])
    total = len(records)
    passed = sum(1 for r in records if r["pass"])
    print("\n== SUMMARY ==")
    for cat in sorted(by_cat):
        vals = by_cat[cat]
        print(f"  {cat:9s} {sum(vals):3d}/{len(vals):3d} "
              f"({100 * sum(vals) / len(vals):.0f}%)")
    if total:
        print(f"  TOTAL     {passed:3d}/{total:3d} "
              f"({100 * passed / total:.1f}%)")


if __name__ == "__main__":
    main()
