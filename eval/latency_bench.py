"""Latency benchmark: spoken turns through the real server (Prisma -> brain -> Timbre SSE), p50/p95 per stage.

  uv run uvicorn app.server:app --port 8080 &   then   uv run python -m eval.latency_bench --turns 20
Customer audio is synthetic (cached Timbre clips); timings come from the server's own per-turn measurements.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics

import io

import numpy as np
import soundfile as sf
import websockets

from app.gnani_client import get_client

LINES = ["Bhaiya, do kilo Aashirvaad atta bhej dena", "ek litre Amul Taaza doodh aur aadha kilo cheeni",
         "teen packet Parle-G aur do packet Good Day", "paav kilo jeera aur dhai sau gram haldi",
         "ek packet Surf Excel aur do Lux sabun", "nahi nahi, doodh do litre kar do", "bas itna hi"]


_rng = np.random.default_rng()


def dither(wav: bytes) -> bytes:
    x, sr = sf.read(io.BytesIO(wav), dtype="float32")
    x = x + _rng.normal(0, 1e-4, size=x.shape).astype("float32")
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


async def one_call(url: str, clips: list[bytes]) -> list[dict]:
    out = []
    async with websockets.connect(url, max_size=2**24, open_timeout=60) as ws:
        async def drain():
            while True:
                m = await asyncio.wait_for(ws.recv(), 120)
                if isinstance(m, bytes):
                    continue
                m = json.loads(m)
                if m["type"] == "timings":
                    return m
                if m["type"] in ("reply_end",) and not out and "greet" not in drained:
                    drained.add("greet")
                    return None
        drained: set[str] = set()
        await drain()  # greeting
        for clip in clips:
            await ws.send(dither(clip))  # unique audio every turn: no STT cache hits, like real callers
            t = await drain()
            if t:
                out.append(t)
    return out


async def main_async(url: str, turns: int) -> None:
    c = get_client()
    clips = [c.tts(t, v, model="timbre-v2.5", language="hi-en") for t, v in
             zip(LINES, ["Kaveri", "Shlok", "Devika", "Girish", "Shlok", "Kaveri", "Devika"])]
    rows: list[dict] = []
    while len(rows) < turns:
        rows += await one_call(url, clips[:6])
    rows = rows[:turns]
    print(f"{len(rows)} spoken turns")
    for k in ("stt_ms", "brain_ms", "tts_first_ms", "to_first_audio_ms"):
        v = sorted(r[k] for r in rows if k in r)
        if v:
            p95 = v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))]
            print(f"  {k:18s} p50 {statistics.median(v):6.0f} ms   p95 {p95:6.0f} ms")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8080/ws/call?customer=CUST-003")
    ap.add_argument("--turns", type=int, default=20)
    a = ap.parse_args()
    asyncio.run(main_async(a.url, a.turns))


if __name__ == "__main__":
    main()
