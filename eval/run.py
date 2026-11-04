"""Eval runner: replay reactive scenarios through the full pipeline and score Order Exact Match.

  uv run python -m eval.run --mode text                                   # brain only, zero credits
  uv run python -m eval.run --mode audio --condition g711_10db --budget 20 --dry-run
  uv run python -m eval.run --mode audio --condition clean --budget 20 --registers hinglish --limit 10

Audio mode: customer turn -> Timbre (cached) -> phone channel -> Prisma (with dialogue context) -> brain.
Writes eval/results/<run_id>/{calls.jsonl, summary.json}.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from app.brain.dm import Session
from app.brain.extract_rules import extract as rules_extract
from app.gnani_client import CreditGuardError, GnaniAPIError, get_client
from app.stt_prisma import STTConfig, transcribe
from eval.channel import CONDITIONS, apply

ROOT = Path(__file__).resolve().parent.parent
import os
SCEN = Path(os.environ.get("EVAL_SCENARIOS", ROOT / "data" / "scenarios"))
RESULTS = ROOT / "eval" / "results"
MAX_STEPS = 12


def load_scenarios(registers: list[str] | None, limit: int | None, ids: list[str] | None = None) -> list[dict]:
    out = []
    for p in sorted(SCEN.glob("*.json")):
        sc = json.loads(p.read_text())
        if registers and sc["register"] not in registers:
            continue
        if ids and sc["id"] not in ids:
            continue
        out.append(sc)
    if limit:
        by_reg = defaultdict(list)
        for sc in out:
            by_reg[sc["register"]].append(sc)
        out = [sc for v in by_reg.values() for sc in v[:limit]]
    return out


def _qeq(a: float, b: float) -> bool:
    return abs(a - b) < 1e-6


def score(final: list[tuple] | None, cart: list[tuple], gold: list[list]) -> dict:
    g = {(s, u): q for s, q, u in gold}
    c = {(s, u): q for s, q, u in cart}
    tp = sum(1 for k, q in c.items() if k in g and _qeq(g[k], q))
    prec = tp / len(c) if c else 0.0
    rec = tp / len(g) if g else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    oem = final is not None and len(final) == len(gold) and all(
        k in g and _qeq(g[k], q) for k, q in {(s, u): q for s, q, u in final}.items())
    return {"oem": bool(oem), "item_p": prec, "item_r": rec, "item_f1": f1}


def next_customer_text(s: Session, sc: dict, queue: list[str], answers: dict, last: str,
                       reprompted: bool = False) -> str:
    if reprompted and last:  # agent said "samajh nahi aaya, phir boliye": a real caller repeats themselves
        return last
    if s.pending:
        lst = answers.get(s.pending.kind) or []
        if lst:
            return lst.pop(0)
        if s.pending.kind in ("set_qty", "qty", "unit") and sc.get("_last_qty"):
            return sc["_last_qty"]
        return last
    if s.awaiting_confirm:
        return (answers.get("confirm") or ["haan"])[0]
    if queue:
        return queue.pop(0)
    return (answers.get("done") or ["bas itna hi"])[0]


def run_scenario(sc: dict, mode: str, condition: str, cfg: STTConfig, extractor, babble: list[bytes]) -> dict:
    client = get_client()
    s = Session(sc["customer"], extractor=extractor, session_id=f"eval-{sc['id']}")
    s.greet()
    queue = list(sc["turns"])
    answers = {k: list(v) for k, v in sc["answers"].items()}
    steps, last, questions, readbacks, reprompted = [], "", 0, 0, False
    for step in range(MAX_STEPS):
        if s.order or s.handed_off:
            break
        text = next_customer_text(s, sc, queue, answers, last, reprompted)
        last = text
        rec = {"step": step, "said": text}
        if mode == "audio":
            wav = client.tts(text, sc["voice"], model="timbre-v2.5", language=sc["tts_language"])
            seed = int(hashlib.sha1(f"{sc['id']}|{step}".encode()).hexdigest()[:8], 16)
            wav = apply(wav, condition, seed=seed, babble=[b for b in babble if b != wav][:4])
            t0 = time.perf_counter()
            r = transcribe(wav, s, cfg, filename="turn.wav")
            rec["stt_ms"] = round((time.perf_counter() - t0) * 1000)
            rec["stt_cached"] = r.get("_cached")
            heard = r.get("transcript", "")
        else:
            heard = text
        rec["heard"] = heard
        t0 = time.perf_counter()
        res = s.handle(heard)
        rec["brain_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        rec["reply"] = res.reply
        rec["pending"] = s.pending.kind if s.pending else None
        rec["source"] = res.frame.source if res.frame else None
        rec["resolutions"] = res.trace.get("resolutions")
        reprompted = any(e["type"] == "question" for e in res.events) and s.pending is None
        if any(e["type"] == "question" for e in res.events):
            questions += 1
        if any(e["type"] == "readback" for e in res.events):
            readbacks += 1
        steps.append(rec)
    cart = [(l["sku"], l["qty"], l["unit"]) for l in s.cart_snapshot()]
    final = [(l["sku"], l["qty"], l["unit"]) for l in s.order["lines"]] if s.order else None
    price_ok = all(abs(l["price"] - s.cat.get(l["sku"]).price) < 1e-9 for l in (s.order or {}).get("lines", []))
    return {"id": sc["id"], "register": sc["register"], "tags": sc["tags"], "draft": sc.get("draft", False),
            "gold": sc["gold"], "final": final, "cart": cart, "handoff": s.handed_off, "steps": steps,
            "questions": questions, "n_steps": len(steps), "hallucinated_price": not price_ok,
            "unconfirmed_order": bool(s.order) and readbacks == 0, **score(final, cart, sc["gold"])}


def estimate(scenarios: list[dict]) -> tuple[int, float]:
    costs = get_client().costs
    texts = [t for sc in scenarios for t in sc["turns"]] + [x for sc in scenarios for v in sc["answers"].values() for x in v]
    chars = sum(len(t) for t in texts)
    secs = 1.5 * chars / 13.0  # ~13 chars/s of speech; x1.5 for repeats after reprompts
    calls = sum(len(sc["turns"]) + 3 for sc in scenarios)
    return calls * 2, chars * costs.tts_per_char + secs * costs.stt_per_sec


def summarize(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups[r["register"]].append(r)
        groups["ALL"].append(r)
    out = {}
    for g, rs in groups.items():
        stt = [st["stt_ms"] for r in rs for st in r["steps"] if "stt_ms" in st and not st.get("stt_cached")]
        out[g] = {
            "n": len(rs), "OEM": round(sum(r["oem"] for r in rs) / len(rs), 3),
            "item_F1": round(statistics.mean(r["item_f1"] for r in rs), 3),
            "questions_per_call": round(statistics.mean(r["questions"] for r in rs), 2),
            "handoff_rate": round(sum(r["handoff"] for r in rs) / len(rs), 3),
            "hallucinated_price_rate": sum(r["hallucinated_price"] for r in rs) / len(rs),
            "unconfirmed_order_rate": sum(r["unconfirmed_order"] for r in rs) / len(rs),
            "stt_p50_ms": round(statistics.median(stt)) if stt else None,
            "stt_p95_ms": round(sorted(stt)[int(0.95 * (len(stt) - 1))]) if stt else None,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["text", "audio"], default="text")
    ap.add_argument("--condition", choices=list(CONDITIONS), default="clean")
    ap.add_argument("--registers", nargs="*")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--extractor", choices=["rules", "evon", "hybrid"], default="rules")
    ap.add_argument("--biasing", choices=["none", "static", "dynamic"], default="dynamic")
    ap.add_argument("--stt-format", default="codemix")
    ap.add_argument("--budget", type=float, help="credit budget for this run (required in audio mode)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    scenarios = load_scenarios(a.registers, a.limit, a.ids)
    calls, est = estimate(scenarios)
    print(f"{len(scenarios)} scenarios | mode={a.mode} condition={a.condition} extractor={a.extractor} "
          f"biasing={a.biasing} | worst-case ~{calls} API calls, ~{est:.2f} credits (cache hits cost 0)")
    if a.dry_run:
        return
    if a.mode == "audio" and a.budget is None:
        raise SystemExit("audio mode needs --budget <credits> (see the dry-run estimate)")
    extractor = rules_extract
    if a.extractor in ("evon", "hybrid"):
        from app.brain.extract_evon import EvonExtractor
        extractor = EvonExtractor(timeout=30)
        if a.extractor == "hybrid":
            from app.brain.extract_hybrid import HybridExtractor
            extractor = HybridExtractor(extractor)
    cfg = STTConfig(format=a.stt_format or None, biasing=a.biasing)
    run_id = time.strftime("%m%d-%H%M%S") + f"-{a.mode}-{a.condition}-{a.extractor}-{a.biasing}{('-' + a.tag) if a.tag else ''}"
    out_dir = RESULTS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    babble = []
    if a.mode == "audio":
        babble = [p.read_bytes() for p in sorted((ROOT / "eval" / "cache" / "tts").glob("*.wav"))[:8]]
    rows = []
    client = get_client()
    with client.run(run_id, budget=a.budget if a.budget is not None else 0.0):
        with (out_dir / "calls.jsonl").open("w") as f:
            for sc in scenarios:
                try:
                    row = run_scenario(sc, a.mode, a.condition, cfg, extractor, babble)
                except GnaniAPIError as e:  # one flaky API call must not lose the whole run
                    print(f"! {sc['id']}: API error {e.status}, skipped (not counted)")
                    continue
                except CreditGuardError as e:  # budget/cap reached: stop, keep partial results
                    print(f"! stopped by credit guard after {len(rows)} scenarios: {e}")
                    break
                rows.append(row)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                mark = "✓" if row["oem"] else "✗"
                print(f"{mark} {sc['id']:16s} F1={row['item_f1']:.2f} q={row['questions']} "
                      f"{'HANDOFF ' if row['handoff'] else ''}final={row['final']}" + ("" if row["oem"] else f" gold={sc['gold']}"))
    summ = summarize(rows)
    if hasattr(extractor, "stats"):
        summ["_extractor_stats"] = {k: (v if k != "latency_ms" else
                                        {"n": len(v), "p50": sorted(v)[len(v) // 2] if v else None})
                                    for k, v in getattr(extractor, "stats").items()}
    summ["_meta"] = {"run_id": run_id, "args": vars(a), "stt_config": cfg.name(), "spent_credits": client.spent_in_run(run_id)}
    (out_dir / "summary.json").write_text(json.dumps(summ, indent=1, ensure_ascii=False))
    print("\n" + json.dumps({k: v for k, v in summ.items() if k != "_meta"}, indent=1, default=str))
    print(f"spent ~{summ['_meta']['spent_credits']:.3f} credits | results: {out_dir}")


if __name__ == "__main__":
    main()
