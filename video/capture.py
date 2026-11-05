"""Record raw footage for the demo video from REAL calls through the running app.

A headless Chromium opens the real call page (1080x1920: a 432x768 phone viewport at 2.5x). An init script
replaces the microphone with a synthetic stream; each caller line (Timbre voice -> simulated phone line) is played
into it, so the page's own voice detector, the server, Prisma, the agent, Evon and Munshi ji's streamed voice all
run exactly as for a visitor. We record:
  frames/   full-resolution screenshots in a tight loop (JPEG), each with its wall-clock timestamp
  events    every server message (transcript, cart, replies, timings, end) and when each caller line played
  audio     the shop's audio exactly as the page scheduled it, plus the caller lines, on one timeline

  uv run uvicorn app.server:app --port 8080 &          (EXTRACTOR=hybrid + Evon for the Evon scene)
  uv run python -m video.capture --scenes first again  # writes video/out/raw/<scene>/
  uv run python -m video.capture --shot                # one still of the film layout, for framing
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from playwright.async_api import async_playwright

from app.gnani_client import get_client
from eval.channel import apply, resample
from video.script import CHANNEL, SCENES, VOICES

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "video" / "out"
CLIPS = ROOT / "video" / "state" / "clips"
SR = 16000
W, H, DPR = 432, 768, 2.5

HARNESS_JS = r"""
(() => {
  const H = window.__H = { events: [], agent: [] };
  const log = (type, extra) => H.events.push(Object.assign({ type, wall: Date.now() }, extra || {}));
  let micCtx = null, micDest = null;
  const mic = () => { if (!micCtx) { micCtx = new AudioContext(); micDest = micCtx.createMediaStreamDestination(); } return micDest; };
  if (!navigator.mediaDevices) navigator.mediaDevices = {};
  // like a real browser, every getUserMedia() gets a fresh stream (the voice detector stops its tracks on pause)
  navigator.mediaDevices.getUserMedia = async () => {
    log("mic_open"); mic(); micDest = micCtx.createMediaStreamDestination(); await micCtx.resume(); return micDest.stream;
  };
  H.say = async (url) => {
    const d = mic(); await micCtx.resume();
    const ab = await micCtx.decodeAudioData(await (await fetch(url)).arrayBuffer());
    const src = micCtx.createBufferSource(); src.buffer = ab; src.connect(d);
    src.start(micCtx.currentTime + 0.05);
    log("say", { url, dur: ab.duration, at: Date.now() + 50 });
    await new Promise((r) => (src.onended = r));
    log("said", { url });
    return ab.duration;
  };
  // tap the shop's audio: every PCM chunk the page schedules, with the wall-clock time it will play
  const start = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (when = 0, ...rest) {
    try {
      if (this.context !== micCtx && this.buffer && this.buffer.numberOfChannels === 1) {
        const lead = Math.max(0, when - this.context.currentTime);
        const f = this.buffer.getChannelData(0), i16 = new Int16Array(f.length);
        for (let i = 0; i < f.length; i++) i16[i] = Math.max(-32768, Math.min(32767, Math.round(f[i] * 32767)));
        const u8 = new Uint8Array(i16.buffer); let s = "";
        for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
        H.agent.push({ at: Date.now() + lead * 1000, sr: this.buffer.sampleRate, n: f.length, b64: btoa(s) });
      }
    } catch (e) { log("tap_error", { e: String(e) }); }
    return start.call(this, when, ...rest);
  };
  const WS = window.WebSocket;
  window.WebSocket = class extends WS {
    constructor(...a) {
      super(...a); log("ws_open", { url: String(a[0]) });
      this.addEventListener("message", (ev) => { if (typeof ev.data === "string") { try { log("ws", { msg: JSON.parse(ev.data) }); } catch (e) {} } });
      this.addEventListener("close", () => log("ws_close"));
    }
  };
})();
"""

# Film framing: the real call UI, without the page's marketing copy around it. The top and bottom of the frame
# stay quiet for the titles and captions added in the edit.
FILM_CSS = """
.top, .intro, .who, .tryline, #hang, .sec, footer, #notice, .status { display: none !important; }
html, body { background: var(--neel, #1e2e78) !important; overflow: hidden !important; }
main { padding: 0 !important; }
.hero { display: flex !important; flex-direction: column !important; align-items: stretch !important;
        min-height: 768px !important; padding: 168px 22px 0 !important; gap: 14px !important; margin: 0 !important;
        background: var(--neel, #1e2e78) !important; }
.callctl { order: 2; margin: 0 !important; }
.talkrow { justify-content: center !important; }
.parchi-wrap { order: 1; margin: 0 !important; }
.parchi { min-height: 330px; }
.convo { display: none !important; }
.timing { color: #c9d2ff !important; text-align: center; margin-top: 8px !important; font-size: 14px !important; }
.incoming { order: 0; margin: 0 !important; }
"""


def make_clips(budget: float) -> None:
    """Caller lines: Timbre voice -> the same simulated phone line as the evals."""
    CLIPS.mkdir(parents=True, exist_ok=True)
    c = get_client()
    with c.run("video_clips", budget=budget):
        for sc in SCENES:
            voice, lang = VOICES[sc["voice"]]
            for i, text in enumerate(sc["lines"]):
                p = CLIPS / f"{sc['id']}-{i}.wav"
                if p.exists():
                    continue
                clean = c.tts(text, voice, model="timbre-v2.5", language=lang)
                (CLIPS / f"{sc['id']}-{i}.clean.wav").write_bytes(clean)
                p.write_bytes(apply(clean, CHANNEL, seed=100 + i))
    print(f"clips ready ({c.spent_in_run('video_clips'):.2f} credits)")


async def _wait(page, js: str, timeout: float) -> bool:
    try:
        await page.wait_for_function(js, timeout=timeout * 1000, polling=100)
        return True
    except Exception:
        return False


LISTENING = "() => document.getElementById('talk').dataset.state === 'listening'"
BUSY_OR_DONE = ("() => ['thinking','speaking'].includes(document.getElementById('talk').dataset.state)"
                " || !document.getElementById('stamp').hidden")
ANSWERED = "() => document.getElementById('talk').dataset.state === 'listening' || !document.getElementById('stamp').hidden"
AUDIO_DONE = "() => !__H.agent.length || Date.now() > Math.max(...__H.agent.map(a => a.at + 1000 * a.n / a.sr)) + 300"


async def open_page(browser, base: str, scene: dict | None):
    ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=DPR)
    await ctx.add_init_script(HARNESS_JS)

    async def clip(route):
        await route.fulfill(path=str(CLIPS / route.request.url.rsplit("/", 1)[1]), content_type="audio/wav")
    await ctx.route("**/__clip/*", clip)
    page = await ctx.new_page()
    cid = scene["customer"] if scene else "CUST-001"
    url = f"{base}/?outbound={cid}" if scene and scene.get("outbound") else f"{base}/"
    await page.goto(url, wait_until="networkidle")
    if not (scene and scene.get("outbound")):
        await page.click(f'.who button[data-cust="{cid}"]')
    await page.add_style_tag(content=FILM_CSS)
    await page.evaluate("document.fonts.ready.then(() => 1)")
    return ctx, page


async def capture(browser, base: str, scene: dict) -> None:
    if scene.get("fresh_memory"):  # retakes of the first call must start before the shop learned anything
        import httpx
        httpx.post(f"{base}/api/memory/{scene['customer']}/forget", timeout=10)
    out = OUT / "raw" / scene["id"]
    shutil.rmtree(out, ignore_errors=True)
    (out / "frames").mkdir(parents=True)
    ctx, page = await open_page(browser, base, scene)
    await _wait(page, "() => typeof vad !== 'undefined'", 30)
    cdp = await ctx.new_cdp_session(page)
    frames: list[dict] = []

    # Full-resolution frames: CDP screencast ignores the 2.5x pixel density (it yields 432x768), so grab
    # device-pixel screenshots in a tight loop instead (~15-25 fps; the UI changes slowly).
    stop = asyncio.Event()

    async def grab():
        while not stop.is_set():
            t = time.time()
            r = await cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 90, "optimizeForSpeed": True,
                                                         "clip": {"x": 0, "y": 0, "width": W, "height": H, "scale": DPR}})
            name = f"{len(frames):05d}.jpg"
            (out / "frames" / name).write_bytes(base64.b64decode(r["data"]))
            frames.append({"t": (t + time.time()) / 2, "file": name})
    grabber = asyncio.ensure_future(grab())
    t0 = time.time()
    marks = {"start": t0}
    await asyncio.sleep(1.2)
    if scene.get("outbound"):
        await asyncio.sleep(2.0)  # the incoming-call card rings for a moment (ring sound added in the edit)
        marks["pickup"] = time.time()
        await page.click("#pickup")
    else:
        marks["dial"] = time.time()
        await page.click("#talk")
    ok = await _wait(page, LISTENING, 60)
    print(f"  [{scene['id']}] greeting done: {ok}")
    for i, _ in enumerate(scene["lines"]):
        await asyncio.sleep(0.5)
        await page.evaluate("u => __H.say(u)", f"/__clip/{scene['id']}-{i}.wav")
        await _wait(page, BUSY_OR_DONE, 15)
        ok = await _wait(page, ANSWERED, 90)
        print(f"  [{scene['id']}] line {i} answered: {ok}")
    await _wait(page, AUDIO_DONE, 30)
    await asyncio.sleep(1.5)
    stop.set()
    await grabber
    events = await page.evaluate("__H.events")
    agent = await page.evaluate("__H.agent")
    await ctx.close()

    T0 = frames[0]["t"] if frames else t0
    (out / "frames.json").write_text(json.dumps({"t0": T0, "frames": frames}))
    (out / "events.json").write_text(json.dumps({"t0": T0, "marks": marks, "events": events}, ensure_ascii=False,
                                                indent=1))
    # one timeline per stem, 16 kHz, starting at the first video frame
    dur = max([f["t"] for f in frames] + [time.time()]) - T0 + 1
    shop = np.zeros(int(dur * SR) + SR, dtype=np.float32)
    for a in agent:
        x = np.frombuffer(base64.b64decode(a["b64"]), dtype=np.int16).astype(np.float32) / 32768
        x = resample(x, a["sr"], SR)
        i = int((a["at"] / 1000 - T0) * SR)
        if 0 <= i < len(shop):
            shop[i:i + len(x)] += x[: len(shop) - i]
    caller = np.zeros_like(shop)
    for e in events:
        if e["type"] == "say":
            x, sr = sf.read(CLIPS / e["url"].rsplit("/", 1)[1], dtype="float32")
            x = resample(x if x.ndim == 1 else x.mean(axis=1), sr, SR)
            i = int((e["at"] / 1000 - T0) * SR)
            caller[i:i + len(x)] += x[: len(caller) - i]
    sf.write(out / "shop.wav", shop, SR)
    sf.write(out / "caller.wav", caller, SR)
    said = [e["msg"] for e in events if e["type"] == "ws" and e["msg"].get("type") in ("transcript", "reply_start", "end")]
    for m in said:
        print("   ", m.get("type"), m.get("text", "") or m.get("order", {}).get("payment", ""))
    print(f"  [{scene['id']}] {len(frames)} frames, {dur:.1f} s")


async def main_async(a) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"])
        if a.shot:
            ctx, page = await open_page(browser, a.base, None)
            await page.evaluate("""renderCart({cart: [
                {sku: 'aashirvaad-atta', name: 'Aashirvaad Atta 5 kg', qty: 2, unit: 'kg', price: 52, amount: 104},
                {sku: 'amul-taaza', name: 'Amul Taaza Milk 1 L', qty: 1, unit: 'l', price: 56, amount: 56, via: 'memory'}],
                total: 160}); document.getElementById('question').textContent = 'Kaun sa washing powder?';""")
            await page.screenshot(path=str(OUT / "shot.png"))
            await ctx.close()
            print("wrote", OUT / "shot.png")
        else:
            for sc in SCENES:
                if not a.scenes or sc["id"] in a.scenes:
                    await capture(browser, a.base, sc)
        await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", nargs="*")
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--shot", action="store_true")
    ap.add_argument("--budget", type=float, default=3)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not a.shot:
        make_clips(a.budget)
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
