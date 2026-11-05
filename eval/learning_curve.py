"""Learning curve: does the shop get better at each regular customer, call after call?

Three personas with their own words ("kapde dhone aala surf" = Ghadi, "atta" = Pillsbury 10 kilo for Tau ji).
Each makes N consecutive calls. Memory OFF: every call starts from zero. Memory ON: the agent learns only from
the previous calls' CONFIRMED orders. Measured per call index: clarifying questions, turns, Order Exact Match.

  uv run python -m eval.learning_curve                          # text mode, zero credits
  uv run python -m eval.learning_curve --mode audio --budget 15 # Timbre -> G.711+noise -> Prisma (with memory keyterms)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import time
from pathlib import Path

from app.brain.dm import Session
from app.brain.memory import CustomerMemory
from app.brain.text import key
from app.gnani_client import get_client
from app.stt_prisma import STTConfig, transcribe
from eval.channel import apply

ROOT = Path(__file__).resolve().parent.parent
# (said, sku, usual qty, spoken qty, answer to "which one?")
PERSONAS = {
    "CUST-002": {"voice": ("Omkar", "hi-en"), "open": "Ram Ram bhai,", "close": "bhej diyo", "yes": "haan, pakka kar do",
                 "done": "bas itna e", "items": [
        ("kapde dhone aala surf", "ghadi-1kg", 2, "do packet", "Ghadi aala"),
        ("lal aala sabun", "lifebuoy", 3, "teen", "Lifebuoy aala"),
        ("atta", "pillsbury-atta", 10, "das kilo", "Pillsbury aala"),
        ("doodh", "mother-dairy-toned", 2, "do litre", "Mother Dairy aala"),
        ("chai patti", "red-label-250", 1, "ek packet", "Red Label aala")]},
    "CUST-001": {"voice": ("Shlok", "hi-en"), "open": "Bhaiya,", "close": "bhej dena", "yes": "haan, confirm kar do",
                 "done": "bas itna hi", "items": [
        ("atta", "aashirvaad-atta", 5, "paanch kilo", "Aashirvaad wala"),
        ("tel", "fortune-mustard-oil", 1, "ek litre", "Fortune sarson wala"),
        ("nahane ka sabun", "dettol-soap", 2, "do", "Dettol wala"),
        ("biscuit", "good-day", 3, "teen packet", "Good Day wala"),
        ("doodh", "amul-taaza", 2, "do litre", "Amul Taaza wala")]},
    "CUST-003": {"voice": ("Bhavna", "hi-IN"), "open": "भैया,", "close": "भेज दीजिए", "yes": "हाँ, कर दीजिए",
                 "done": "बस इतना ही", "items": [
        ("आटा", "fortune-atta", 5, "पाँच किलो", "फॉर्च्यून वाला"),
        ("दूध", "amul-gold", 1, "एक लीटर", "अमूल गोल्ड"),
        ("बिस्कुट", "marie-gold", 2, "दो पैकेट", "मैरी गोल्ड"),
        ("चाय पत्ती", "tata-tea-250", 1, "एक पैकेट", "टाटा छोटा वाला"),
        ("साबुन", "lux", 2, "दो", "लक्स")]},
}


def plan_calls(cid: str, n_calls: int, seed: int = 11) -> list[list[tuple]]:
    """Each call orders 3 of the persona's 5 usual items; on even calls one item is said without a quantity."""
    rng = random.Random(f"{cid}-{seed}")
    items = PERSONAS[cid]["items"]
    calls = []
    for i in range(n_calls):
        pick = rng.sample(items, 3)
        calls.append([(it, not (i % 2 == 1 and j == 0)) for j, it in enumerate(pick)])
    return calls


def utterance(cid: str, call: list[tuple]) -> str:
    p = PERSONAS[cid]
    joiner = " और " if cid == "CUST-003" else " aur "
    parts = [f"{it[3]} {it[0]}" if with_qty else it[0] for it, with_qty in call]
    return f"{p['open']} {joiner.join(parts)} {p['close']}"


def answer(cid: str, s: Session, call: list[tuple], last: str) -> str:
    p = PERSONAS[cid]
    pend = s.pending
    if pend is not None:
        said = key(pend.said or pend.phrase)
        cand_ids = [c.id for c in pend.candidates]
        item = next((it for it, _ in call if it[1] in cand_ids), None)  # the product actually being offered
        if item is None:
            item = next((it for it, _ in call if key(it[0]) in said.split() or said == key(it[0])), None)
        if pend.kind in ("brand_choice", "size_choice"):
            return item[4] if item else last
        if pend.kind in ("qty", "unit", "set_qty"):
            it = item or next((it for it, _ in call if pend.candidates and it[1] == pend.candidates[0].id), None)
            return it[3] if it else last
        return "haan"  # qty_default, qty_confirm, substitute_offer
    if s.awaiting_confirm:
        return p["yes"]
    return p["done"]


def run_call(cid: str, call: list[tuple], memory, mode: str, cfg: STTConfig, idx: int) -> dict:
    client = get_client()
    s = Session(cid, memory=memory, session_id=f"lc-{cid}-{idx}")
    s.greet()
    text, last, steps, questions = utterance(cid, call), "", [], 0
    first = True
    for step in range(10):
        if s.order or s.handed_off:
            break
        said = text if first else answer(cid, s, call, last)
        first = False
        last = said
        heard = said
        if mode == "audio":
            v, lang = PERSONAS[cid]["voice"]
            wav = client.tts(said, v, model="timbre-v2.5", language=lang)
            seed = int(hashlib.sha1(f"{cid}|{idx}|{step}".encode()).hexdigest()[:8], 16)
            heard = transcribe(apply(wav, "g711_10db", seed=seed), s, cfg, filename="lc.wav").get("transcript", "")
        r = s.handle(heard)
        if any(e["type"] == "question" for e in r.events):
            questions += 1
        steps.append({"said": said, "heard": heard, "reply": r.reply, "memory_hits": r.trace.get("memory_hits")})
    gold = sorted((it[1], float(it[2])) for it, _ in call)
    final = sorted((l["sku"], float(l["qty"])) for l in s.order["lines"]) if s.order else None
    return {"customer": cid, "call": idx + 1, "questions": questions, "turns": len(steps), "oem": final == gold,
            "handoff": s.handed_off, "final": final, "gold": gold, "steps": steps,
            "learned": (s.order or {}).get("learned", [])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["text", "audio"], default="text")
    ap.add_argument("--calls", type=int, default=5)
    ap.add_argument("--budget", type=float)
    ap.add_argument("--global-keyterms", action="store_true", help="ablation: shop-wide brand keyterms even for known customers")
    a = ap.parse_args()
    if a.mode == "audio" and a.budget is None:
        raise SystemExit("audio mode needs --budget")
    cfg = STTConfig(biasing="dynamic", personal=not a.global_keyterms)
    run_id = time.strftime("%m%d-%H%M%S") + f"-learning-{a.mode}" + ("-global" if a.global_keyterms else "-personal")
    out = ROOT / "eval" / "results" / run_id
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    client = get_client()
    with client.run(run_id, budget=a.budget or 0.0):
        for cid in PERSONAS:
            calls = plan_calls(cid, a.calls)
            mem = CustomerMemory(cid)
            for cond in ("off", "on"):
                for i, call in enumerate(calls):
                    r = run_call(cid, call, mem if cond == "on" else None, a.mode, cfg, i)
                    r["memory"] = cond
                    rows.append(r)
    (out / "calls.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    summary = {}
    print(f"\n{'call':>5} | {'memory OFF: questions / exact':>30} | {'memory ON: questions / exact':>29}")
    for i in range(a.calls):
        row = {}
        for cond in ("off", "on"):
            rs = [r for r in rows if r["memory"] == cond and r["call"] == i + 1]
            row[cond] = {"questions": round(statistics.mean(r["questions"] for r in rs), 2),
                         "oem": round(sum(r["oem"] for r in rs) / len(rs), 3),
                         "turns": round(statistics.mean(r["turns"] for r in rs), 2), "n": len(rs)}
        summary[i + 1] = row
        print(f"{i + 1:>5} | {row['off']['questions']:>17.2f} / {row['off']['oem']:>5.0%}      |"
              f" {row['on']['questions']:>16.2f} / {row['on']['oem']:>5.0%}")
    for cond in ("off", "on"):
        rs = [r for r in rows if r["memory"] == cond]
        summary[f"all_{cond}"] = {"questions_per_call": round(statistics.mean(r["questions"] for r in rs), 2),
                                  "oem": round(sum(r["oem"] for r in rs) / len(rs), 3)}
    summary["_meta"] = {"run_id": run_id, "mode": a.mode, "calls": a.calls, "personas": list(PERSONAS),
                        "spent_credits": client.spent_in_run(run_id)}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"\nall calls: OFF {summary['all_off']}  ON {summary['all_on']}")
    print(f"spent ~{summary['_meta']['spent_credits']:.3f} credits | {out}")


if __name__ == "__main__":
    main()
