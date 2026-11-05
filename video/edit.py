"""Cut the recorded calls into the 60-second demo (1080x1920, 30 fps, H.264 + AAC).

Cuts are anchored to call events, not fixed seconds, so a re-recorded take still cuts in the same places:
  say<i>   when caller line i started playing      said<i>  when it ended
  reply<i> when the shop's i-th reply became audible end      when the order was placed (stamp)
  dial / pickup                                    the tap that started the call
Only pauses are removed. Words, replies and timings shown on screen are the live system's.

  uv run python -m video.edit            # -> video/out/bharatvoice_demo.mp4
  uv run python -m video.edit --stills   # contact sheet of key moments, no render
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "video" / "out" / "raw"
OUT = ROOT / "video" / "out"
OVL = OUT / "overlays"
W, H, FPS, SR = 1080, 1920, 30, 16000
TOTAL = 60.0  # hard cap; the render ends 4.5 s after the last call (see main)

S, M, T = "Sharma ji", "Munshi ji (AI)", "Tau ji"

# ------------------------------------------------------------------ the edit
# (beat, scene, (anchor, offset), (anchor, offset)). Each caller turn is cut as: the caller's line + 0.4 s, then
# from just before the transcript arrives (resume<i>). The recorder itself slows the page's voice detector (it screenshots
# at full resolution ~12x a second), so the raw wait is longer than a real caller's; what stays is ~1 s, as on a live call.
EDL = [
    (0, "first", ("say0", -0.35), ("said0", 0.4)),     # three items in one breath
    (0, "first", ("resume0", 0), ("reply1", 2.3)),  # parchi writes itself
    (1, "first", ("reply1", 4.73), ("reply1", 8.65)),  # "Kaun sa washing powder?"
    (1, "first", ("say1", -0.2), ("said1", 0.4)),
    (1, "first", ("resume1", 0), ("reply2", 1.5)),  # "Ek packet Surf Excel"
    (2, "first", ("say2", -0.2), ("said2", 0.4)),      # correction
    (2, "first", ("resume2", 0), ("reply3", 2.4)),  # red pen
    (3, "first", ("say3", -0.2), ("said3", 0.4)),     # "Bas itna hi"
    (3, "first", ("reply4", 6.33), ("reply4", 7.8)),   # "Kul teen sau chhappan rupaye"
    (3, "first", ("reply4", 8.63), ("reply4", 9.5)),   # "Confirm kar doon?"
    (3, "first", ("say4", -0.15), ("said4", 0.4)),     # "Haan"
    (3, "first", ("resume4", 0), ("reply5", 1.25)),  # stamp
    (4, "again", ("reply0", -0.15), ("reply0", 0.98)),  # "Namaste Sharma ji!"
    (4, "again", ("say0", -0.2), ("said0", 0.4)),
    (4, "again", ("resume0", 0), ("reply1", 2.75)),  # memory: no question
    (5, "outbound", ("pickup", -1.9), ("pickup", 0.3)),  # the shop calls Tau ji
    (5, "outbound", ("reply0", -0.35), ("reply0", 1.0)),  # "Ram Ram Tau ji!"
    (5, "outbound", ("reply0", 3.85), ("reply0", 9.85)),  # "...khatam hone aala hoga. Wahi bhej du?"
    (5, "outbound", ("say0", -0.2), ("said0", 0.4)),
    (5, "outbound", ("resume0", 0), ("reply1", 2.5)),  # udhaar stamp
]
TITLES = [  # (beat, title, subtitle)
    (0, "This kirana's phone is answered by AI", "Hinglish, on a noisy phone line. Watch the parchi write itself."),
    (1, "Not sure which one? It asks.", ""),
    (2, "Changed your mind? Red pen.", ""),
    (3, "Nothing is ordered without your “haan”", ""),
    (4, "Next call: it remembers you", "To Sharma ji, “kapde dhone wala powder” means Surf Excel. No question this time."),
    (5, "Running low? The shop calls you.", "In Haryanvi, straight into the khata."),
]
LABEL = (0, "Real call through the live app. The caller's voice is Gnani Timbre.")
CAPTIONS = [  # (scene, start anchor, end anchor, speaker, text)
    ("first", ("say0", 0), ("said0", 0.3), S, "Bhaiya, do kilo Aashirvaad atta, ek litre Amul Taaza doodh, aur kapde dhone wala powder bhej do."),
    ("first", ("reply1", 0), ("reply1", 2.3), M, "Theek hai, do kilo Aashirvaad atta…"),
    ("first", ("reply1", 4.8), ("reply1", 8.65), M, "Kaun sa washing powder? Surf Excel, Tide ya Ghadi?"),
    ("first", ("say1", 0), ("said1", 0.3), S, "Surf Excel wala."),
    ("first", ("reply2", 0), ("reply2", 1.5), M, "Ek packet Surf Excel."),
    ("first", ("say2", 0), ("said2", 0.3), S, "Aur haan, doodh… nahi nahi, doodh do litre kar do."),
    ("first", ("reply3", 0), ("reply3", 2.4), M, "Do litre Amul Taaza doodh kar diya."),
    ("first", ("say3", 0), ("reply4", -0.05), S, "Bas itna hi."),
    ("first", ("reply4", 6.33), ("reply4", 7.8), M, "Kul teen sau chhappan rupaye."),
    ("first", ("reply4", 8.63), ("reply4", 9.5), M, "Confirm kar doon?"),
    ("first", ("say4", 0), ("said4", 0.4), S, "Haan, confirm kar do."),
    ("first", ("reply5", 0), ("reply5", 1.25), M, "Order confirm!"),
    ("again", ("reply0", 0), ("reply0", 0.98), M, "Namaste Sharma ji!"),
    ("again", ("say0", 0), ("said0", 0.3), S, "Wahi kapde dhone wala powder bhej do."),
    ("again", ("reply1", 0), ("reply1", 2.75), M, "Achha ji. Ek packet Surf Excel."),
    ("outbound", ("reply0", 0), ("reply0", 1.0), M, "Ram Ram Tau ji!"),
    ("outbound", ("reply0", 3.96), ("reply0", 8.7), M, "Thara ek litre Amul Taaza doodh, chaar packet Parle-G khatam hone aala hoga."),
    ("outbound", ("reply0", 9.1), ("reply0", 9.85), M, "Wahi bhej du?"),
    ("outbound", ("say0", 0), ("said0", 0.3), T, "Haan, bhej de. Khaate mein likh diyo."),
    ("outbound", ("reply1", 0), ("reply1", 1.3), M, "Theek se, udhaar mein likh dunga."),
    ("outbound", ("reply1", 1.7), ("reply1", 2.5), M, "Order pakka!"),
]
ZOOMS = [  # (scene, from anchor, zoom, centre x, centre y): push in until the segment ends
    ("first", ("reply3", -0.9), 1.16, 540, 715),     # the red-pen correction
    ("first", ("end", -0.1), 1.14, 760, 960),       # पक्का
    ("again", ("reply1", -0.4), 1.14, 540, 640),     # "yaad tha"
    ("outbound", ("end", -0.1), 1.14, 760, 960),    # उधार
]
RING = ("outbound", ("pickup", -1.9), ("pickup", -0.02))
THUDS = [("first", ("end", 0.05)), ("outbound", ("end", 0.05))]


# ------------------------------------------------------------------ footage
@dataclass
class Scene:
    name: str
    anchors: dict
    times: list
    files: list
    mix: np.ndarray


def load_scene(name: str) -> Scene:
    d = RAW / name
    fr = json.loads((d / "frames.json").read_text())
    ev = json.loads((d / "events.json").read_text())
    t0 = fr["t0"]
    shop, _ = sf.read(d / "shop.wav", dtype="float32")
    caller, _ = sf.read(d / "caller.wav", dtype="float32")
    a = {k: v - t0 for k, v in ev["marks"].items()}
    says = [e for e in ev["events"] if e["type"] == "say"]
    saids = [e for e in ev["events"] if e["type"] == "said"]
    for i, e in enumerate(says):
        a[f"say{i}"] = e["at"] / 1000 - t0
    for i, e in enumerate(saids):
        a[f"said{i}"] = e["wall"] / 1000 - t0
    replies = [e["wall"] / 1000 - t0 for e in ev["events"] if e["type"] == "ws" and e["msg"].get("type") == "reply_start"]
    heard = [e["wall"] / 1000 - t0 for e in ev["events"] if e["type"] == "ws" and e["msg"].get("type") == "transcript"]
    for i, t in enumerate(heard):
        a[f"heard{i}"] = t
    for i, t in enumerate(replies):  # when the reply actually became audible
        j = int(max(0, t - 0.05) * SR)
        loud = np.nonzero(np.abs(shop[j:j + 4 * SR]) > 0.02)[0]
        a[f"reply{i}"] = (j + loud[0]) / SR if len(loud) else t
    # where to resume after the caller's line: 0.35 s before the transcript, or 0.75 s before the reply is
    # heard, whichever is later (keeps the visible wait near a live call's, see the EDL note)
    for i, h in enumerate(heard):
        nxt = [a[k] for k in a if k.startswith("reply") and a[k] > h]
        a[f"resume{i}"] = max(h - 0.35, min(nxt) - 0.75) if nxt else h - 0.35
    for e in ev["events"]:
        if e["type"] == "ws" and e["msg"].get("type") == "end":
            a["end"] = e["wall"] / 1000 - t0
    mix = level(caller, 0.07) + level(shop, 0.08)
    return Scene(name, a, [f["t"] - t0 for f in fr["frames"]], [f["file"] for f in fr["frames"]], mix)


def level(x: np.ndarray, rms: float) -> np.ndarray:
    active = x[np.abs(x) > 0.01]
    return x * (rms / max(1e-6, float(np.sqrt(np.mean(active ** 2))))) if len(active) else x


def at(sc: Scene, anchor) -> float:
    return sc.anchors[anchor[0]] + anchor[1]


class Timeline:
    """Output time <-> (scene, source time) through the EDL."""

    def __init__(self, scenes: dict):
        self.scenes, self.segs, self.beats, t = scenes, [], [], 0.0
        for beat, name, a, b in EDL:
            s0, s1 = at(scenes[name], a), at(scenes[name], b)
            assert s1 > s0, (name, a, b)
            if self.segs and self.segs[-1][0] == name and s0 <= self.segs[-1][2]:  # no gap to cut: join
                n, p0, p1, o = self.segs[-1]
                t += max(s1, p1) - p1
                self.segs[-1] = (n, p0, max(s1, p1), o)
                continue
            self.segs.append((name, s0, s1, t))
            self.beats.append(beat)
            t += s1 - s0
        self.footage = t

    def source(self, t: float):
        for i, (name, s0, s1, o) in enumerate(self.segs):
            if o <= t < o + (s1 - s0):
                return i, name, s0 + (t - o)
        return None

    def to_out(self, name: str, s0: float, s1: float):
        """Pieces of the source span [s0, s1) that survive the cut, in output time."""
        out = []
        for i, (n, a, b, o) in enumerate(self.segs):
            if n == name and s1 > a and s0 < b:
                out.append((i, o + max(s0, a) - a, o + min(s1, b) - a))
        return out

    def beat_span(self, beat: int):
        idx = [i for i, b in enumerate(self.beats) if b == beat]
        i, j = idx[0], idx[-1]
        return self.segs[i][3], self.segs[j][3] + self.segs[j][2] - self.segs[j][1]


# ------------------------------------------------------------------ overlays (HTML -> PNG, real text shaping)
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Mukta:wght@400;500;600;700&family=Kalam&family=Tiro+Devanagari+Hindi&display=swap');
html, body { margin: 0; background: transparent; width: 1080px; height: 1920px; }
.o { position: absolute; left: 64px; width: 952px; font-family: Mukta, sans-serif; }
.title { top: 86px; }
.title h1 { margin: 0; font: 700 70px/1.06 Mukta; color: #fdfeff; letter-spacing: -.5px; }
.title p { margin: 18px 0 0; font: 500 37px/1.25 Mukta; color: #f4b400; }
.label { top: 360px; font: 500 25px/1.3 Mukta; color: #aeb8ec; }
.cap { bottom: 64px; }
.cap b { display: block; font: 600 29px/1.2 Mukta; margin-bottom: 8px; }
.cap b.shop { color: #f4b400; } .cap b.caller { color: #aeb8ec; }
.cap span { display: block; font: 500 47px/1.22 Mukta; color: #fdfeff; }
.end { inset: 0; width: auto; left: 0; background: #1e2e78; padding: 210px 76px 0; box-sizing: border-box; }
.end .mark { font: 700 112px/1 Mukta; color: #fdfeff; letter-spacing: -1.5px; }
.end .mark small { display: block; font: 400 46px/1.6 'Tiro Devanagari Hindi'; color: #aeb8ec; letter-spacing: 0; }
.end .lede { margin: 36px 0 64px; font: 500 50px/1.18 Mukta; color: #fdfeff; }
.end .lede em { font: 400 54px Kalam; color: #f4b400; font-style: normal; }
.end h2 { margin: 0 0 18px; font: 600 33px Mukta; color: #aeb8ec; }
.end ul { margin: 0; padding: 0; list-style: none; }
.end li { font: 500 40px/1.25 Mukta; color: #fdfeff; margin: 0 0 26px; padding-left: 30px; border-left: 6px solid #f4b400; }
.end li span { display: block; font: 400 31px/1.3 Mukta; color: #c9d2ff; }
.end .cta { margin-top: 70px; font: 600 46px/1.2 Mukta; color: #1e2e78; background: #f4b400; display: inline-block;
            padding: 22px 36px; border-radius: 999px; }
.end .fine { position: absolute; left: 76px; right: 76px; bottom: 96px; font: 400 27px/1.4 Mukta; color: #aeb8ec; }
.end .fine b { color: #fdfeff; font-weight: 500; }
"""
END_HTML = """<div class="o end" id="x">
  <div class="mark">BharatVoice<small>भारतवॉइस</small></div>
  <p class="lede">An AI <em>munshi</em> for the phone of every kirana: orders in Hinglish, Hindi and Haryanvi, written exactly right.</p>
  <h2>Built on Gnani.ai</h2>
  <ul>
    <li>Prisma<span>hears the caller on a noisy phone line</span></li>
    <li>Timbre<span>gives Munshi ji his voice</span></li>
    <li>Evon v3.3<span>understands items described, not named</span></li>
  </ul>
  <div class="cta">Call the shop yourself: link in the post</div>
  <p class="fine">Real calls through the live app, pauses trimmed. Callers voiced by Gnani Timbre.<br>
  <b>#GnaniAI #GreatIndianAIInternshipChallenge</b></p>
</div>"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;")


async def render_overlays() -> dict:
    from playwright.async_api import async_playwright
    OVL.mkdir(parents=True, exist_ok=True)
    jobs = {}
    for k, (_, t, sub) in enumerate(TITLES):
        jobs[f"title{k}"] = f'<div class="o title" id="x"><h1>{_esc(t)}</h1>{f"<p>{_esc(sub)}</p>" if sub else ""}</div>'
    jobs["label"] = f'<div class="o label" id="x">{_esc(LABEL[1])}</div>'
    for k, (_, _, _, who, text) in enumerate(CAPTIONS):
        cls = "shop" if who == M else "caller"
        jobs[f"cap{k}"] = f'<div class="o cap" id="x"><b class="{cls}">{_esc(who)}</b><span>{_esc(text)}</span></div>'
    jobs["end"] = END_HTML
    boxes = {}
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        p = await b.new_page(viewport={"width": W, "height": H})
        for name, html in jobs.items():
            await p.set_content(f"<style>{CSS}</style>{html}", wait_until="networkidle")
            await p.evaluate("document.fonts.ready.then(() => 1)")
            el = p.locator("#x")
            box = await el.bounding_box()
            await el.screenshot(path=str(OVL / f"{name}.png"), omit_background=True)
            boxes[name] = [round(box["x"]), round(box["y"])]
        await b.close()
    (OVL / "boxes.json").write_text(json.dumps(boxes))
    return boxes


# ------------------------------------------------------------------ sound
def ring(dur: float) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    tone = 0.5 * np.sin(2 * np.pi * 440 * t) + 0.5 * np.sin(2 * np.pi * 480 * t)
    p = t % 1.0  # a short double ring, repeating every second, so it is still ringing at the pickup
    cadence = (p < 0.35) | ((p > 0.5) & (p < 0.85))
    env = np.convolve(cadence.astype(float), np.ones(160) / 160, mode="same")
    return (0.09 * tone * env).astype(np.float32)


def thud() -> np.ndarray:
    t = np.arange(int(0.22 * SR)) / SR
    body = np.sin(2 * np.pi * (95 - 120 * t) * t) * np.exp(-t * 22)
    click = np.random.default_rng(3).normal(0, 1, len(t)) * np.exp(-t * 160)
    return (0.45 * body + 0.08 * click).astype(np.float32)


def build_audio(tl: Timeline) -> np.ndarray:
    pieces = []
    fade = int(0.012 * SR)
    for name, s0, s1, _ in tl.segs:
        x = tl.scenes[name].mix[int(s0 * SR):int(s1 * SR)].copy()
        x[:fade] *= np.linspace(0, 1, fade)
        x[-fade:] *= np.linspace(1, 0, fade)
        pieces.append(x)
    a = np.concatenate(pieces)
    a = np.concatenate([a, np.zeros(int((TOTAL - len(a) / SR + 1) * SR), dtype=np.float32)])
    name, r0, r1 = RING
    sc = tl.scenes[name]
    for _, o0, o1 in tl.to_out(name, at(sc, r0), at(sc, r1)):
        x = ring(o1 - o0)
        a[int(o0 * SR):int(o0 * SR) + len(x)] += x
    for name, anc in THUDS:
        t = at(tl.scenes[name], anc)
        for _, o0, _ in tl.to_out(name, t, t + 0.01):
            x = thud()
            a[int(o0 * SR):int(o0 * SR) + len(x)] += x
    # the last word rings into the end card, then fades
    e = int(tl.footage * SR)
    a[e:] *= np.exp(-np.arange(len(a) - e) / (0.35 * SR))
    return np.clip(a[: int(TOTAL * SR)], -1, 1)


# ------------------------------------------------------------------ picture
def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


class Picture:
    def __init__(self, tl: Timeline, boxes: dict):
        self.tl, self.boxes, self._cache = tl, boxes, {}
        self.ovl = {k: Image.open(OVL / f"{k}.png").convert("RGBA") for k in boxes}
        self.titles = [(*tl.beat_span(b), f"title{k}") for k, (b, _, _) in enumerate(TITLES)]
        self.label = (*tl.beat_span(LABEL[0]), "label")
        self.caps = []
        for k, (name, a, b, _, _) in enumerate(CAPTIONS):
            sc = tl.scenes[name]
            for _, o0, o1 in tl.to_out(name, at(sc, a), at(sc, b)):
                self.caps.append((o0, o1, f"cap{k}"))
        self.zooms = []
        for name, anc, z, cx, cy in ZOOMS:
            t = at(tl.scenes[name], anc)
            for i, o0, _ in tl.to_out(name, t, t + 0.01):
                self.zooms.append((i, o0, z, cx, cy))

    def frame(self, sc_name: str, t: float) -> Image.Image:
        sc = self.tl.scenes[sc_name]
        i = max(0, bisect.bisect_right(sc.times, t) - 1)
        f = RAW / sc_name / "frames" / sc.files[i]
        if f not in self._cache:
            if len(self._cache) > 4:
                self._cache.pop(next(iter(self._cache)))
            self._cache[f] = Image.open(f).convert("RGB")
        return self._cache[f]

    def zoom_at(self, seg: int, t: float, seg_t0: float, seg_t1: float):
        z, cx, cy = 1.0 + 0.03 * ease((t - seg_t0) / max(0.1, seg_t1 - seg_t0)), 540.0, 860.0  # slow push-in
        for i, o0, zt, zx, zy in self.zooms:
            if i == seg and t >= o0:
                k = ease((t - o0) / 0.45)
                z, cx, cy = z + (zt - z) * k, cx + (zx - cx) * k, cy + (zy - cy) * k
        return z, cx, cy

    def render(self, t: float) -> Image.Image:
        if t >= self.tl.footage:  # end card, faded in over the last frame
            img = self._frame_out(self.tl.footage - 1 / FPS).copy()
            card = self.ovl["end"]
            k = ease((t - self.tl.footage) / 0.35)
            base = Image.new("RGBA", (W, H))
            base.paste(img.convert("RGBA"))
            card = card.copy()
            card.putalpha(card.getchannel("A").point(lambda v: int(v * k)))
            base.alpha_composite(card, tuple(self.boxes["end"]))
            return base.convert("RGB")
        img = self._frame_out(t).convert("RGBA")
        for t0, t1, key in self.titles + [self.label] + self.caps:
            if t0 <= t < t1:
                k = min(ease((t - t0) / 0.18) if t0 > 0.01 else 1.0, ease((t1 - t) / 0.12))
                o = self.ovl[key]
                if k < 1:
                    o = o.copy()
                    o.putalpha(o.getchannel("A").point(lambda v: int(v * k)))
                x, y = self.boxes[key]
                dy = int((1 - k) * 14) if key.startswith("title") else 0
                img.alpha_composite(o, (x, y + dy))
        # progress line along the top
        from PIL import ImageDraw
        ImageDraw.Draw(img).rectangle([0, 0, int(W * t / TOTAL), 7], fill=(244, 180, 0, 255))
        return img.convert("RGB")

    def _frame_out(self, t: float) -> Image.Image:
        seg, name, src = self.tl.source(t) or (len(self.tl.segs) - 1, self.tl.segs[-1][0], self.tl.segs[-1][2] - 0.01)
        _, s0, s1, o = self.tl.segs[seg]
        img = self.frame(name, src)
        z, cx, cy = self.zoom_at(seg, t, o, o + s1 - s0)
        if z <= 1.001:
            return img
        w, h = W / z, H / z
        x0 = min(max(0, cx - w / 2), W - w)
        y0 = min(max(0, cy - h / 2), H - h)
        return img.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + w, y0 + h))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stills", action="store_true")
    ap.add_argument("--skip-overlays", action="store_true")
    a = ap.parse_args()
    scenes = {n: load_scene(n) for n in ("first", "again", "outbound")}
    tl = Timeline(scenes)
    global TOTAL
    TOTAL = min(60.0, round(tl.footage + 4.5, 2))  # end card 4.5 s, never past 60 s
    print(f"footage {tl.footage:.2f} s + end card {TOTAL - tl.footage:.2f} s")
    for i, (n, s0, s1, o) in enumerate(tl.segs):
        print(f"  seg {i:2d} {n:9s} {s0:6.2f}-{s1:6.2f} -> {o:5.2f}-{o + s1 - s0:5.2f}")
    assert TOTAL - tl.footage >= 3.0, "end card needs at least 3 s: trim the edit"
    boxes = json.loads((OVL / "boxes.json").read_text()) if a.skip_overlays else asyncio.run(render_overlays())
    pic = Picture(tl, boxes)
    if a.stills:
        ts = [0.0, 6.0, 11.5, 22.5, 31.2, 38.0, 41.0, 54.5, 58.0]
        ims = [pic.render(t).resize((270, 480)) for t in ts]
        sheet = Image.new("RGB", (270 * 5, 480 * 2), (0, 0, 0))
        for k, im in enumerate(ims):
            sheet.paste(im, ((k % 5) * 270, (k // 5) * 480))
        sheet.save(OUT / "stills.jpg", quality=88)
        print("wrote", OUT / "stills.jpg")
        return
    wav = OUT / "demo_audio.wav"
    sf.write(wav, build_audio(tl), SR)
    out = OUT / "bharatvoice_demo.mp4"
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-profile:v", "high", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-movflags", "+faststart", "-t", str(TOTAL), str(out)],
        stdin=subprocess.PIPE)
    n = int(TOTAL * FPS)
    for k in range(n):
        ff.stdin.write(pic.render(k / FPS).tobytes())
        if k % 300 == 0:
            print(f"  frame {k}/{n}")
    ff.stdin.close()
    ff.wait()
    print("wrote", out)


if __name__ == "__main__":
    main()
