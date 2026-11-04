"""Voice-cloning flow check (plan U6) using a SYNTHETIC reference voice (a Timbre voice), no real person.

  uv run python -m infra.clone_check                       # synthetic reference (Timbre 'Deepak')
  uv run python -m infra.clone_check --ref data/voices/shopkeeper_ref.wav --save   # consenting person, saves embedding

--save writes data/voices/shopkeeper_embedding.json, which the agent uses when AGENT_CLONED_VOICE=1.
"""
from __future__ import annotations

import argparse
import io
import json
import time
from pathlib import Path

import soundfile as sf

from app.gnani_client import get_client
from app.tts_timbre import CLONE_FILE

REF_TEXT = ("Namaste ji, main Sharma General Store se bol raha hoon. Aapka order main abhi likh leta hoon. "
            "Atta, daal, chawal, tel, sab kuch hai humare paas. Bataiye kya bhejna hai?")


def secs(b: bytes) -> float:
    i = sf.info(io.BytesIO(b))
    return i.frames / i.samplerate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", type=Path)
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    c = get_client()
    ref = a.ref.read_bytes() if a.ref else c.tts(REF_TEXT, "Deepak", model="timbre-v2.5", language="hi-en")
    print(f"reference: {secs(ref):.1f}s ({'file ' + str(a.ref) if a.ref else 'synthetic Timbre Deepak'})")
    t0 = time.perf_counter()
    emb = c.clone_embedding(ref, a.ref.name if a.ref else "ref.wav")
    print(f"embedding: shape={emb.get('shape')} dtype={emb.get('dtype')} ({1000*(time.perf_counter()-t0):.0f} ms)")
    t0 = time.perf_counter()
    out = c.tts("Ram Ram ji! Do kilo Aashirvaad atta aur ek litre doodh, likh liya. Aur ke chahiye?", "",
                language="hi-en", speaker_embedding=emb)
    print(f"cloned TTS: {secs(out):.1f}s audio in {1000*(time.perf_counter()-t0):.0f} ms")
    Path("traces").mkdir(exist_ok=True)
    Path("traces/clone_check.wav").write_bytes(out)
    print("wrote traces/clone_check.wav")
    if a.save:
        CLONE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CLONE_FILE.write_text(json.dumps(emb))
        print(f"saved {CLONE_FILE} (set AGENT_CLONED_VOICE=1 to use it)")


if __name__ == "__main__":
    main()
