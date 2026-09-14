#!/usr/bin/env python3
"""A/B eval: mindpod-qwen3:8b (fine-tuned) vs qwen3:8b (base fast tier).

16 Mindpod fact questions + 4 general controls, keyword rubric.
Usage: eval_ab.py <model> <out.json>
"""
import json, re, sys, time, urllib.request

Q = [
 # (question, [all-of groups], [any-of groups], [forbidden])
 ("What is AngelMind?", [["security","governance"]], [["touched","approval","evidence"]], ["alibaba"]),
 ("Is AngelMind available for purchase today?", [], [["in development","not generally available","early access","not ga"]], []),
 ("What does MITB stand for?", [["minds in a techs box"]], [], []),
 ("Who is MITB for?", [["msp"]], [["lean it"]], []),
 ("What environments does MITB support?", [["entra","azure"]], [["microsoft 365","m365"]], ["google workspace"]),
 ("What is the AI Pulse Check?", [["assessment"]], [["30-day","30 day"]], []),
 ("What is Quotewren?", [["quot"]], [["invoice","payment"]], []),
 ("How much does Quotewren Solo cost per month?", [["19"]], [], ["39/month for solo"]),
 ("How much does Quotewren Pro cost per month?", [["39"]], [], ["199","399"]),
 ("What is SupplyMind.ei?", [["tariff"]], [["supply"]], []),
 ("What were the Michael scanner's recall and precision figures?", [["62.2","93.3"]], [], []),
 ("Who founded Mindpod Technologies?", [["jaras funderburg"]], [], []),
 ("Where is Mindpod Technologies based?", [["atlanta"]], [], []),
 ("What are Mindpod's five layers of Enterprise Intelligence?", [["strategic","governance","cloud","product"]], [["application","automation"]], []),
 ("How does Mindpod handle high-risk AI actions?", [["human"]], [["approval","approve","sign-off"]], []),
 ("Can Mindpod help a law firm adopt AI safely?", [], [["privilege","aba","512"]], []),
 # controls
 ("What is the capital of Japan?", [["tokyo"]], [], []),
 ("What is 17 times 24?", [["408"]], [], []),
 ("In one sentence, what is a TCP three-way handshake?", [["syn"]], [["ack"]], []),
 ("Write a haiku about rain.", [], [], []),
]

def ask(model, q):
    body = json.dumps({"model": model, "messages": [{"role":"user","content": q}],
                       "stream": False, "options": {"temperature": 0.1}}).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=240) as r:
        d = json.loads(r.read())
    return d["message"]["content"], round(time.time()-t0, 1)

def score(ans, allg, anyg, forb):
    a = re.sub(r"<think>.*?</think>", " ", ans, flags=re.S).lower()
    if len(a.strip()) < 5: return 0
    for g in allg:
        if not all(k in a for k in g): return 0
    for g in anyg:
        if not any(k in a for k in g): return 0
    if any(k in a for k in forb): return 0
    return 1

def main():
    model, out = sys.argv[1], sys.argv[2]
    lo, hi = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (0, len(Q))
    results, facts, controls = [], 0, 0
    for i, (q, allg, anyg, forb) in enumerate(Q[lo:hi], start=lo):
        ans, secs = ask(model, q)
        s = score(ans, allg, anyg, forb)
        if i < 16: facts += s
        else: controls += s
        results.append({"q": q, "score": s, "secs": secs, "ans": ans[:400]})
        print(f"[{i+1:2d}/20] {'PASS' if s else 'FAIL'} ({secs}s) {q[:60]}", flush=True)
    summary = {"model": model, "facts": f"{facts}/16", "controls": f"{controls}/4",
               "total_s": round(sum(r["secs"] for r in results), 1), "results": results}
    json.dump(summary, open(out, "w"), indent=2)
    print(f"\n{model}: slice {lo}-{hi}, facts {facts}, controls {controls}, total {summary['total_s']}s")

main()
