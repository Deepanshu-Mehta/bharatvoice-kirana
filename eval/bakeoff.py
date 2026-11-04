"""STT bake-off (plan §3.2): which Prisma config gets the ENTITIES right, per register.

Clips come from real speakers (data/real/bakeoff/<speaker>/<prompt>.wav, recorded via /record) or, to validate the
pipeline before recordings exist, from Timbre voices (--source tts). Scoring is automatic: the transcript goes through
the same rules extractor + resolver + quantity parser as the agent and is compared with the prompt's gold entities.

  uv run python -m eval.bakeoff --source tts --budget 5           # pipeline check on synthetic voices
  uv run python -m eval.bakeoff --source real --budget 10          # the real bake-off (after recording)
  uv run python -m eval.bakeoff --source real --condition g711_10db --budget 10
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import yaml
from rapidfuzz.distance import Levenshtein

from app.brain.extract_rules import extract
from app.brain.qty import parse_qty
from app.brain.resolver import Resolver
from app.brain.text import to_roman
from app.gnani_client import CreditGuardError, GnaniAPIError, get_client
from app.stt_prisma import TOPIC, load_substitutions
from eval.channel import apply

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "data" / "prompts" / "bakeoff.yaml"
REAL = ROOT / "data" / "real" / "bakeoff"
TTS_VOICES = {"hinglish": ("Devika", "hi-en"), "haryanvi": ("Roopesh", "hi-en"), "hindi": ("Nalini", "hi-IN")}
HARYANVI_WORDS = ["konya", "mhara", "thara", "ghana", "diyo", "aala", "Ram Ram"]
GENERIC_PROMPT = "Boliye, kya bhejna hai?"


def configs(brands: list[str]) -> dict[str, dict]:
    single = [b for b in brands if b.isalpha()][:100]
    return {
        "A v2.5 hi-IN": dict(model="prisma-v2.5", language_code="hi-IN"),
        "B v2.5 en-IN +bias": dict(model="prisma-v2.5", language_code="en-IN", bias_list=single, bias_score=1.5),
        "C v3.0 codemix": dict(model="prisma-v3.0", language_code="hi-IN,en-IN", format="codemix"),
        "D v3.0 translit": dict(model="prisma-v3.0", language_code="indic-auto", format="transliterate"),
        "E C+keyterms+topic": dict(model="prisma-v3.0", language_code="hi-IN,en-IN", format="codemix",
                                   keyterms=brands, topic=TOPIC),
        "F E+previous_text": dict(model="prisma-v3.0", language_code="hi-IN,en-IN", format="codemix",
                                  keyterms=brands, topic=TOPIC, previous_text=GENERIC_PROMPT),
        "G F+haryanvi ctx": dict(model="prisma-v3.0", language_code="hi-IN,en-IN", format="codemix",
                                 keyterms=HARYANVI_WORDS + brands, topic=TOPIC + " Caller may speak Haryanvi.",
                                 previous_text=GENERIC_PROMPT),
        "H F+mined subs": dict(model="prisma-v3.0", language_code="hi-IN,en-IN", format="codemix",
                               keyterms=brands, topic=TOPIC, previous_text=GENERIC_PROMPT,
                               substitution_map=load_substitutions()),
    }


def score(transcript: str, gold: dict, res: Resolver) -> dict:
    f = extract(transcript)
    if "items" in gold:
        pred = set()
        prod_ok = qty_ok = 0
        for a in f.actions:
            if a.type != "add" or not a.product_phrase:
                continue
            r = res.resolve(a.product_phrase)
            q = parse_qty(a.qty_text) if a.qty_text else None
            sku = r.top.id if r.top else None
            v, u = q.normalized() if q else (1.0, None)
            pred.add((sku, round(v, 3)))
        gold_set = {(s, round(float(q) if u not in ("pc",) or s == "eggs" else float(q), 3)) for s, q, u in gold["items"]}
        gold_skus = {s for s, _, _ in gold["items"]}
        prod_ok = len({s for s, _ in pred} & gold_skus)
        qty_ok = len(pred & gold_set)
        n = len(gold["items"])
        # packets/pieces: "do Lux" -> qty 2 with unit None -> compare value only (done above)
        return {"exact": pred == gold_set, "product_acc": prod_ok / n, "entity_acc": qty_ok / n}
    a = f.actions[0] if f.actions else None
    ok = a is not None and a.type == gold["intent"]
    if ok and "qty" in gold:
        q = parse_qty(a.qty_text or "")
        ok = q is not None and abs(q.normalized()[0] - gold["qty"][0]) < 1e-6
    if ok and "sku" in gold:
        r = res.resolve(a.product_phrase or "")
        ok = bool(r.top and r.top.id == gold["sku"])
    return {"exact": ok, "product_acc": float(ok), "entity_acc": float(ok)}


def clips(source: str, prompts: list[dict]):
    c = get_client()
    if source == "tts":
        for p in prompts:
            v, lang = TTS_VOICES[p["register"]]
            yield "tts-" + v, p, c.tts(p["text"], v, model="timbre-v2.5", language=lang), p["text"]
        return
    for spk_dir in sorted(REAL.glob("*")):
        meta = {}
        mf = spk_dir / "manifest.jsonl"
        if mf.exists():
            for line in mf.read_text().splitlines():
                m = json.loads(line)
                meta[m["prompt_id"]] = m
        for p in prompts:
            wav = spk_dir / f"{p['id']}.wav"
            if wav.exists():
                yield spk_dir.name, p, wav.read_bytes(), meta.get(p["id"], {}).get("text_said") or p["text"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["tts", "real"], default="tts")
    ap.add_argument("--condition", default="clean")
    ap.add_argument("--configs", nargs="*")
    ap.add_argument("--budget", type=float, required=True)
    a = ap.parse_args()
    prompts = yaml.safe_load(PROMPTS.read_text())["prompts"]
    from app.brain.catalog import load_catalog
    brands = [t for t in load_catalog().top_terms() if len(t.replace(" ", "")) > 4]
    cfgs = configs(brands)
    if a.configs:
        cfgs = {k: v for k, v in cfgs.items() if any(k.startswith(x) for x in a.configs)}
    res, client = Resolver(), get_client()
    agg: dict[tuple, list] = defaultdict(list)
    rows = []
    run_id = f"bakeoff-{a.source}-{a.condition}"
    try:
        with client.run(run_id, budget=a.budget):
            for speaker, p, wav, ref in clips(a.source, prompts):
                seed = int(hashlib.sha1(f"{speaker}|{p['id']}".encode()).hexdigest()[:8], 16)
                audio = apply(wav, a.condition, seed=seed)
                for name, kw in cfgs.items():
                    if name.startswith("G") and p["register"] != "haryanvi":
                        continue
                    try:
                        out = client.stt(audio, "clip.wav", **kw)
                    except GnaniAPIError as e:
                        print(f"! {speaker}/{p['id']} {name}: {e.status}")
                        continue
                    hyp = out.get("transcript", "")
                    s = score(hyp, p["gold"], res)
                    cer = Levenshtein.normalized_distance(to_roman(ref), to_roman(hyp))
                    row = {"speaker": speaker, "prompt": p["id"], "register": p["register"], "config": name,
                           "hyp": hyp, "cer": round(cer, 3), **s}
                    rows.append(row)
                    agg[(name, p["register"])].append(row)
                    agg[(name, "ALL")].append(row)
    except CreditGuardError as e:
        print(f"! stopped by credit guard: {e}")
    out_dir = ROOT / "eval" / "results" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rows.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    regs = ["ALL", "hinglish", "haryanvi", "hindi"]
    print(f"\n{'config':22s} " + " ".join(f"{r:>18s}" for r in regs) + "   (entity acc / exact / CER)")
    table = {}
    for name in cfgs:
        cells = []
        for r in regs:
            v = agg.get((name, r), [])
            if not v:
                cells.append(f"{'-':>18s}")
                continue
            ea = sum(x["entity_acc"] for x in v) / len(v)
            ex = sum(x["exact"] for x in v) / len(v)
            ce = sum(x["cer"] for x in v) / len(v)
            table.setdefault(name, {})[r] = {"n": len(v), "entity_acc": round(ea, 3), "exact": round(ex, 3), "cer": round(ce, 3)}
            cells.append(f"{ea:6.2f}/{ex:4.2f}/{ce:4.2f}".rjust(18))
        print(f"{name:22s} " + " ".join(cells))
    (out_dir / "summary.json").write_text(json.dumps(table, indent=1))
    print(f"\nspent ~{client.spent_in_run(run_id):.3f} credits | {out_dir}")


if __name__ == "__main__":
    main()
