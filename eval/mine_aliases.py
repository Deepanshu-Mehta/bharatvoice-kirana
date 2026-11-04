"""STT-aware alias mining (plan U4): Timbre -> phone channel -> Prisma loopback over every catalog name.

For each product name/alias, synthesise "<qty> <name> de dena" with MINING voices (disjoint from eval voices),
pass it through clean and G.711+10 dB channels, transcribe with the production STT config (static keyterms),
and run the resolver on the transcript. Every transcription that does NOT resolve to the right product becomes:
  * an stt_alias for that product   -> data/stt_aliases_mined.yaml (merged by the catalog loader)
  * a Prisma find-and-replace pattern for the 10 most-confused products -> data/substitution_rules_mined.json

  uv run python -m eval.mine_aliases --dry-run
  uv run python -m eval.mine_aliases --budget 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from app.brain.catalog import load_catalog
from app.brain.extract_rules import extract
from app.brain.resolver import Resolver
from app.gnani_client import GnaniAPIError, get_client
from app.stt_prisma import STTConfig, transcribe
from eval.channel import apply

ROOT = Path(__file__).resolve().parent.parent
MINING_VOICES = [("Yashvi", "hi-en"), ("Jalaj", "hi-en"), ("Pranav", "hi-en")]  # not used by eval scenarios
CONDITIONS = ["clean", "g711_10db"]


def mining_items(per_product: int = 2) -> list[tuple[str, str]]:
    out = []
    for p in load_catalog().products:
        names = [a for a in p.aliases if a.isascii() and not any(ch.isdigit() for ch in a)]
        names = sorted(set(names), key=lambda a: (len(a.split()) != 2, len(a)))[:per_product]
        qty = "ek packet" if p.unit in ("pkt", "pc") else "ek kilo" if p.unit == "kg" else "ek litre"
        out += [(p.id, f"{qty} {n} de dena") for n in names]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--per-product", type=int, default=2)
    a = ap.parse_args()
    items = mining_items(a.per_product)
    n = len(items) * len(MINING_VOICES[:2]) * len(CONDITIONS)
    print(f"{len(items)} phrases x 2 voices x {len(CONDITIONS)} conditions = {n} STT calls (+{len(items) * 2} TTS)")
    if a.dry_run:
        return
    if a.budget is None:
        raise SystemExit("needs --budget")
    client, res, cfg = get_client(), Resolver(), STTConfig(biasing="static")
    confusions: dict[str, Counter] = defaultdict(Counter)
    total = wrong = 0
    with client.run("mine-aliases", budget=a.budget):
        for i, (sku, text) in enumerate(items):
            voice, lang = MINING_VOICES[i % 2], None
            for v_name, v_lang in (MINING_VOICES[i % 3], MINING_VOICES[(i + 1) % 3]):
                try:
                    wav = client.tts(text, v_name, model="timbre-v2.5", language=v_lang)
                except GnaniAPIError:
                    continue
                for cond in CONDITIONS:
                    seed = int(hashlib.sha1(f"{text}|{v_name}|{cond}".encode()).hexdigest()[:8], 16)
                    try:
                        heard = transcribe(apply(wav, cond, seed=seed), None, cfg, filename="m.wav")["transcript"]
                    except GnaniAPIError:
                        continue
                    total += 1
                    f = extract(heard)
                    phrase = next((x.product_phrase for x in f.actions if x.type == "add" and x.product_phrase), "")
                    r = res.resolve(phrase or heard)
                    if not r.top or r.top.id != sku:
                        wrong += 1
                        if phrase:
                            confusions[sku][phrase.strip()] += 1
            if i % 20 == 0:
                print(f"  {i}/{len(items)}  confusion rate so far {wrong}/{total}")
    print(f"\nloopback name-resolution errors: {wrong}/{total} ({wrong / max(total, 1):.1%})")
    mined = {sku: sorted(c, key=lambda k: -c[k]) for sku, c in confusions.items()}
    (ROOT / "data" / "stt_aliases_mined.yaml").write_text(yaml.safe_dump(
        {"_note": "Mined by eval/mine_aliases.py (Timbre->channel->Prisma loopback). Review before trusting.",
         "aliases": mined}, allow_unicode=True, sort_keys=True))
    cat = load_catalog()
    top = sorted(confusions, key=lambda s: -sum(confusions[s].values()))[:10]
    rules = [{"patterns": [p for p in mined[s] if len(p) >= 3][:10], "replacement": cat.get(s).brand or cat.get(s).name}
             for s in top]
    rules = [r for r in rules if r["patterns"]]
    (ROOT / "data" / "substitution_rules_mined.json").write_text(json.dumps(rules, ensure_ascii=False, indent=1))
    for s in top:
        print(f"  {s:24s} {sum(confusions[s].values()):3d}  e.g. {list(confusions[s])[:4]}")
    print(f"spent ~{client.spent_in_run('mine-aliases'):.3f} credits")


if __name__ == "__main__":
    main()
