#!/usr/bin/env python3
"""Model benchmark: fire a big question bank at one Ollama model and grade
the answers with rule-based rubrics (keyword sets).

Usage:
    .venv/bin/python evals/model_bench.py [--model gpt-oss:20b] [--limit N]

Results append to evals/model_bench_results.jsonl; already-answered
questions are skipped, so the run is resumable in slices. A summary is
printed at the end of every invocation.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "model_bench_questions.json"
RESULTS = HERE / "model_bench_results.jsonl"

SYSTEM = ("You are a precise technical expert. Answer the question "
          "directly and concisely. No preamble, no disclaimers.")


def normalize(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("²", "^2").replace("³", "^3").replace("−", "-")
    t = re.sub(r"\s+", " ", t)
    return t


def grade(answer: str, accept: list[list[str]]) -> bool:
    norm = normalize(answer)
    compact = norm.replace(" ", "")
    for kw_set in accept:
        ok = True
        for kw in kw_set:
            k = normalize(kw)
            if k not in norm and k.replace(" ", "") not in compact:
                ok = False
                break
        if ok:
            return True
    return False


def ask(base: str, model: str, question: str, timeout: int = 120) -> tuple[str, float]:
    t0 = time.time()
    resp = requests.post(
        f"{base}/v1/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": question},
            ],
            "temperature": 0.4,
            "extra_body": {"reasoning_effort": "none"},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
    return (content or ""), time.time() - t0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--limit", type=int, default=0,
                        help="max NEW questions this run (0 = all remaining)")
    parser.add_argument("--base", default="http://localhost:11434")
    args = parser.parse_args()

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
                answer, secs = ask(args.base, args.model, item["q"])
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

    # Summary across all recorded results for this model.
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
