"""Held-out check for mined aliases: product names spoken by EVAL voices (never used for mining).

  uv run python -m eval.heldout_names --budget 5
Reports name-resolution accuracy with mined aliases + substitution rules ON vs OFF (same audio, cached STT where
the request is identical).
"""
import argparse
import hashlib
import importlib
import os

from app.gnani_client import get_client
from eval.channel import apply

VOICES = [("Kaveri", "hi-en"), ("Shlok", "hi-en"), ("Girish", "hi-en")]
ITEMS = [("tide-1kg", "ek packet Tide powder de dena"), ("tide-1kg", "Tide wala washing powder ek kilo"),
         ("rin-1kg", "ek packet Rin powder de dena"), ("rin-1kg", "Rin wala surf ek kilo"),
         ("red-label-250", "Red Label chai patti chhota packet"), ("red-label-250", "Brooke Bond Red Label ek packet"),
         ("clinic-plus-175", "Clinic Plus shampoo ek bottle"), ("clinic-plus-175", "ek Clinic Plus de dena"),
         ("kabuli-chana", "ek kilo kabuli chana de dena"), ("kabuli-chana", "safed kabuli chana ek kilo"),
         ("maachis", "ek packet maachis de dena"), ("maachis", "do maachis bhej dena")]


def run(mined: bool, budget: float) -> float:
    os.environ["MINED_ALIASES"] = "1" if mined else "0"
    import app.brain.catalog as catalog
    catalog.load_catalog.cache_clear()
    import app.brain.resolver as resolver
    import app.stt_prisma as stt
    import app.brain.extract_rules as er
    importlib.reload(resolver)
    er._alias_ngrams.cache_clear()
    c, res, cfg = get_client(), resolver.Resolver(), stt.STTConfig(biasing="static")
    ok = n = 0
    with c.run(f"heldout-{int(mined)}", budget=budget):
        for sku, text in ITEMS:
            for v, lang in VOICES:
                wav = c.tts(text, v, model="timbre-v2.5", language=lang)
                for cond in ("clean", "g711_10db"):
                    seed = int(hashlib.sha1(f"{text}|{v}|{cond}".encode()).hexdigest()[:8], 16)
                    heard = stt.transcribe(apply(wav, cond, seed=seed), None, cfg, filename="h.wav")["transcript"]
                    f = er.extract(heard)
                    phrase = next((a.product_phrase for a in f.actions if a.type == "add" and a.product_phrase), heard)
                    r = res.resolve(phrase)
                    n += 1
                    ok += bool(r.top and r.top.id == sku)
                    if not (r.top and r.top.id == sku) and mined:
                        print(f"   miss {sku:16s} heard={heard!r}")
    return ok / n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=5)
    a = ap.parse_args()
    off = run(False, a.budget)
    on = run(True, a.budget)
    print(f"held-out name resolution (n={len(ITEMS) * len(VOICES) * 2}): mined OFF {off:.1%}  ->  mined ON {on:.1%}")
