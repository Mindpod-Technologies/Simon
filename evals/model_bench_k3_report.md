# Model Bench — kimi-k3 vs local fleet (210 questions)

**Date:** 2026-09-15 · **Bank:** `model_bench_questions.json` (210 q: 35 each AWS, Azure, GCP, ML, calculus, history)
**Method:** identical system prompt + keyword rubric for every model (`model_bench.grade`).
Cloud: `model_bench_cloud.py --model kimi-k3` against `https://api.moonshot.ai/v1`, reasoning_effort=low.

## Headline (official rubric)

| Model | Score | % | Avg s/q |
|---|---|---|---|
| **kimi-k3** (cloud) | **203/210** | **96.7%** | 10.2 |
| k2d6-agent (cloud) | 200/210 | 95.2% | 8.3 |
| qwen3:8b (local) | 190/210 | 90.5% | 9.2 |
| huihui qwen3-abliterated 8b (local) | 184/210 | 87.6% | 8.1 |
| gpt-oss:20b (local) | 179/210 | 85.2% | 3.2 |

## kimi-k3 by category

| Category | Score |
|---|---|
| AWS | 35/35 (100%) |
| Azure | 35/35 (100%) |
| GCP | 35/35 (100%) |
| History | 35/35 (100%) |
| ML | 35/35 (100%) |
| Calculus | 28/35 (80%) ⚠️ |

## ⚠️ Rubric caveat: K3's calculus "failures" are all correct

All 7 calculus misses were manually verified as **mathematically correct answers**
that the keyword rubric cannot parse (Unicode superscripts `eˣ`, `½`, `−`, and
`\frac` LaTeX). e.g.:

- `∫ sin(2x) dx = −½ cos(2x) + C` — correct, with u-substitution derivation AND a verification check
- `d/dx(aˣ) = aˣ ln(a)` — correct
- MVT statement — textbook-perfect

**True knowledge score: 210/210.** The same formatting caveat depresses every
model's calculus row (qwen3:8b also lost 7), so relative rankings stand, but the
rubric should gain a Unicode/LaTeX normalizer (`model_bench_regrade.py` extended).

## Takeaways

1. **K3 is the strongest brain available to Simon** — 100% on five of six
   categories; perfect on manual review. It beats the best local model by 6+
   points even under the harsh rubric.
2. **Local fleet holds up for cloud trivia** (qwen3:8b 90.5%) — fine for the
   fast tier and offline operation.
3. **Calculus/formatting is the rubric's weakness, not the models'.**
4. Latency: K3 ~10s/q at reasoning_effort=low, and unlike local models it does
   not pay Simon's ~20k-token prompt-processing cost — in-app hard turns are
   ~5–8s vs ~30–45s local.

Raw data: `model_bench_results_cloud.jsonl` (kimi-k3 rows), `model_bench_results.jsonl` (local).
