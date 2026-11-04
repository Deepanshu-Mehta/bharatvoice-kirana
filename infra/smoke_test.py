"""Day-1 smoke test: verify request/response shapes and latency with a handful of calls.

  uv run python -m infra.smoke_test            # 1 TTS + 2 STT (loopback)
  uv run python -m infra.smoke_test --measure  # +10 TTS +10 STT for credit-cost measurement

All calls go through the guarded, cached client. Re-running is free (cache hits).
"""
from __future__ import annotations

import argparse
import io
import time

import soundfile as sf

from app.gnani_client import get_client

HINGLISH = "Bhaiya, do kilo Aashirvaad atta aur ek litre Amul Taaza doodh bhej dena."
MEASURE_TEXTS = [
    "Paanch kilo chawal aur aadha kilo cheeni chahiye.",
    "Ek packet Maggi aur do Parle-G de dena.",
    "Fortune sarson ka tel ek litre bhejo.",
    "Nahi nahi, doodh do litre kar do.",
    "Tata chai patti ka bada packet hai kya?",
    "Surf Excel ka ek kilo wala packet.",
    "Dhai sau gram haldi aur paav kilo jeera.",
    "Ek darjan ande aur ek bread.",
    "Atta hata do, uski jagah Pillsbury de do.",
    "Haan, confirm kar do. Kitna hua total?",
]


def wav_secs(b: bytes) -> float:
    i = sf.info(io.BytesIO(b))
    return i.frames / i.samplerate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", action="store_true")
    args = ap.parse_args()
    c = get_client()

    t0 = time.perf_counter()
    audio = c.tts(HINGLISH, "Poorvi", model="timbre-v2.5", language="hi-en")
    print(f"TTS Poorvi hi-en: {len(audio)} bytes, {wav_secs(audio):.2f}s audio, {1000*(time.perf_counter()-t0):.0f} ms")

    for model, lang, fmt in [("prisma-v3.0", "hi-IN,en-IN", "codemix"), ("prisma-v2.5", "hi-IN", None)]:
        t0 = time.perf_counter()
        r = c.stt(audio, "loop.wav", model=model, language_code=lang, format=fmt,
                  keyterms=["Aashirvaad", "Amul Taaza"] if model == "prisma-v3.0" else None)
        print(f"STT {model} {lang} {fmt}: {1000*(time.perf_counter()-t0):.0f} ms cached={r['_cached']}")
        print("   ", {k: v for k, v in r.items() if k != "_cached"})

    if args.measure:
        units_sec = 0.0
        units_chr = 0
        for t in MEASURE_TEXTS:
            a = c.tts(t, "Poorvi", model="timbre-v2.5", language="hi-en")
            units_chr += len(t)
            units_sec += wav_secs(a)
            c.stt(a, "m.wav", model="prisma-v3.0", language_code="hi-IN,en-IN")
        print(f"measure batch: {units_chr} TTS chars, {units_sec:.1f}s STT audio")

    print(c.report())


if __name__ == "__main__":
    main()
