"""Day-1 Evon test (run from the Mac once the ssh tunnel to the GPU is up).

Checks, per the plan (§5.4): JSON lands in `content` (prints `reasoning_content` too), validates against the
frame schema with thinking off, and measures latency. Compares each frame with the rules extractor.

  EVON_URL=https://<workspace>--bharatvoice-evon-serve.modal.run/v1 uv run python -m infra.evon_day1_test
"""
import json
import statistics
import time

import httpx

from app.brain.extract_evon import EVON_API_KEY, EVON_MODEL, EVON_URL, EvonExtractor, build_messages
from app.brain.extract_rules import Context
from app.brain.extract_rules import extract as rules
from app.brain.frame import StrictFrame, frame_json_schema

TURNS = [
    ("भैया, दो किलो Aashirvaad आता और एक लीटर Amul Taaza दूध भेज देना।", Context()),
    ("nahi nahi, doodh do litre kar do", Context(cart_phrases=["ek litre Amul Taaza Milk"])),
    ("atta hata do, uski jagah Pillsbury de do", Context(cart_phrases=["do kilo Aashirvaad Atta"])),
    ("Ram Ram! dhai sau gram haldi aur ek packet Parle-G chahiye", Context()),
    ("atta konya chahiye", Context(cart_phrases=["do kilo Aashirvaad Atta"])),
]


def main():
    # first request after deploy can take minutes (cold start: load ~60 GB into the GPU)
    http = httpx.Client(timeout=900, headers={"Authorization": f"Bearer {EVON_API_KEY}"} if EVON_API_KEY else {})
    base = EVON_URL.rsplit("/v1", 1)[0]
    t_wait = time.perf_counter()
    while True:  # cold start: Modal's proxy times out long requests, so poll /health until vLLM is up
        try:
            r = http.get(f"{base}/health", timeout=120, follow_redirects=True)
            if r.status_code == 200:
                break
            print("health:", r.status_code)
        except httpx.HTTPError as e:
            print("health: waiting", type(e).__name__)
        if time.perf_counter() - t_wait > 780:
            raise SystemExit("server not healthy in 13 min: check `modal app logs bharatvoice-evon`, then stop the app")
        time.sleep(10)
    print(f"healthy after {time.perf_counter()-t_wait:.0f}s")
    print("models:", http.get(f"{EVON_URL}/models").json()["data"][0]["id"])
    print("\n--- raw field check (content vs reasoning_content) ---")
    body = {"model": EVON_MODEL, "messages": build_messages(TURNS[0][0], TURNS[0][1]), "temperature": 0.0,
            "max_tokens": 400, "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {"name": "turn_frame", "schema": frame_json_schema()}}}
    t0 = time.perf_counter()
    msg = http.post(f"{EVON_URL}/chat/completions", json=body).json()["choices"][0]["message"]
    print(f"latency {1000*(time.perf_counter()-t0):.0f} ms")
    print("content          :", repr(msg.get("content"))[:300])
    print("reasoning_content:", repr(msg.get("reasoning_content") or msg.get("reasoning"))[:300])
    StrictFrame.model_validate_json(msg["content"])  # raises if the JSON is not in content or invalid
    print("OK: valid frame in `content`")

    print("\n--- 5 turns: Evon vs rules ---")
    ex = EvonExtractor(timeout=30)
    for t, ctx in TURNS:
        f, r = ex(t, ctx), rules(t, ctx)
        print(f"\n{t}\n  evon  [{f.source}/{f.speech_register}] {json.dumps([a.model_dump(exclude_none=True) for a in f.actions], ensure_ascii=False)}"
              f"\n  rules [{r.speech_register}] {json.dumps([a.model_dump(exclude_none=True) for a in r.actions], ensure_ascii=False)}")
    lat = ex.stats["latency_ms"]
    print(f"\nlatency ms: median {statistics.median(lat):.0f}, max {max(lat):.0f}; fallbacks {ex.stats['fallbacks']}, retries {ex.stats['retries']}")


if __name__ == "__main__":
    main()
