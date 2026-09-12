#!/usr/bin/env python3
"""Re-grade model_bench_results.jsonl with a LaTeX-aware normalizer.

The first-pass grader missed correct answers written in LaTeX notation
(e.g. \\frac{-3\\sin(3x)}{1} or \\(a^x\\ln a\\)). This re-scorer does NOT
re-run the model — it re-reads the recorded answers, strips LaTeX, widens a
few rubric keyword sets where the answer used equivalent wording, and
re-counts. Anything still failing is printed for manual review.
"""
import json, re, sys

RESULTS = (sys.argv[1] if len(sys.argv) > 1
           else "/Users/mpt_ai_machine/simon/evals/model_bench_results.jsonl")
QUESTIONS = "/Users/mpt_ai_machine/simon/evals/model_bench_questions.json"

def delatex(t: str) -> str:
    t = t.lower()
    # common latex constructs -> plain text
    t = re.sub(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", t)
    t = t.replace("\\sqrt", " sqrt ").replace("\\ln", " ln ").replace("\\log", " log ")
    for fn in ["sin", "cos", "tan", "sec", "csc", "cot", "exp"]:
        t = t.replace("\\" + fn, " " + fn + " ")
    t = t.replace("\\cdot", "*").replace("\\times", "*").replace("\\pi", "pi")
    t = t.replace("\\infty", "infinity").replace("\\sum", "sum").replace("\\int", "integral")
    t = re.sub(r"\\[a-zA-Z]+", " ", t)          # any remaining \command
    t = t.replace("\\", " ")
    for ch in "{}$":
        t = t.replace(ch, " ")
    t = t.replace("\\(", " ").replace("\\)", " ")
    t = re.sub(r"\s+", " ", t)
    return t

def grade(answer: str, accept):
    norm = delatex(answer)
    for kwset in accept:
        if all(k.lower() in norm for k in kwset):
            return True
    return False

def main():
    qs = {q["q"]: q for q in json.load(open(QUESTIONS))}
    recs = [json.loads(l) for l in open(RESULTS) if l.strip()]
    cats = {}
    still_failing = []
    for r in recs:
        q = qs[r["q"]]
        ok = grade(r["answer"], q["accept"])
        c = cats.setdefault(r["cat"], [0, 0])
        c[1] += 1
        if ok:
            c[0] += 1
        else:
            still_failing.append((r["cat"], r["q"], r["answer"][:300]))
    total_p = sum(c[0] for c in cats.values())
    total_n = sum(c[1] for c in cats.values())
    print("=== RE-GRADED (LaTeX-aware) ===")
    for c, (p, n) in sorted(cats.items()):
        print(f"  {c:10s} {p}/{n}  ({100*p/n:.0f}%)")
    print(f"  {'TOTAL':10s} {total_p}/{total_n}  ({100*total_p/total_n:.1f}%)")
    print(f"\n=== STILL FAILING: {len(still_failing)} ===")
    for cat, q, a in still_failing:
        print(f"\n[{cat}] {q}\n  A: {a}")

if __name__ == "__main__":
    main()
