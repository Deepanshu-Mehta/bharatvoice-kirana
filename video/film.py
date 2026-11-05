"""BharatVoice launch film: 1920x1080, <= 60 s, from the recorded real calls.

Layout: the live app's parchi on the left (the hero); on the right a call bar with live voice levels, a rubber
stamp naming each beat, and the conversation as it happens. Motion is an HTML/CSS page (video/film.html) stepped
frame by frame in headless Chromium, so Devanagari is shaped properly and type moves smoothly.

Opening: no title card. Frame one is a live call already in motion (the research favours product motion in the
first two seconds; title-card opens did worst).

  uv run python -m video.capture ...          # footage (clean callers) + video/out/raw/first_noisy (noisy take)
  uv run python -m video.film --stills        # contact sheet
  uv run python -m video.film                 # -> video/out/bharatvoice_film.mp4
Music: "Product Video" by SoundSurfer (Pixabay Content License; the file stays local, video/assets/ is gitignored).
Chosen over a future-bass track by measurement: 18% vs 26% of its power in the 300-3400 Hz speech range and a
steady level (the other jumped >= 5 dB 22 times). It is offset so its groove re-entry (65.46 s) lands
on the second stamp and its own ending lands on the closing card; stamps snap to its beat (88 BPM). The music is
dipped 5 dB around 1-4 kHz and ducked under every word (margins are printed at render time).
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from video.edit import FPS, RAW, SR, Timeline, at, ease, load_scene, ring, thud

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "video" / "out"
W, H = 1920, 1080
CAM_W, CAM_H, CAM_X0, CAM_CY = 886, 976, 0.0, 970.0   # crop of the 1080x1920 app frames
S0 = CAM_W / 1080
END_LEN = 4.8
COLD = 1.3   # cold open: the finished parchi first, then the call that wrote it
MUSIC = ROOT / "video" / "assets" / "soundsurfer-product-video-590799.mp3"
REENTRY, BPM, BEAT0 = 65.46, 88.0, 0.670   # measured: groove re-entry, tempo and first beat of the track
MSR = 48000
S, M, T = "Sharma ji", "Munshi ji (AI)", "Tau ji"

EDL = [
    (-1, "first", ("end", 0.6), ("end", 0.6 + COLD)),     # cold open: the finished, stamped parchi (voice muted)
    (0, "first", ("say0", -0.1), ("said0", 0.4)),
    (0, "first", ("resume0", 0), ("reply1", 2.3)),
    (1, "first", ("reply1", 4.73), ("reply1", 8.65)),
    (1, "first", ("say1", -0.2), ("said1", 0.4)),
    (1, "first", ("resume1", 0), ("reply1", 0.0)),       # placeholder start, fixed below to the reply2 cut
    (2, "first", ("say2", -0.2), ("said2", 0.4)),
    (2, "first", ("resume2", 0), ("reply3", 2.4)),
    (3, "first", ("say3", -0.2), ("said3", 0.4)),
    (3, "first", ("reply4", 8.63), ("reply4", 9.5)),     # "Confirm kar doon?"
    (3, "first", ("say4", -0.15), ("said4", 0.4)),
    (3, "first", ("resume4", 0), ("reply5", 1.25)),      # पक्का
    (4, "first_noisy", ("say0", 3.0), ("said0", 0.4)),   # the same line on a noisy street line
    (4, "first_noisy", ("resume0", 0), ("resume0", 2.5)),  # hold until the lines are written
    (5, "again", ("reply0", -0.15), ("reply0", 0.98)),   # "Namaste Sharma ji!"
    (5, "again", ("say0", -0.2), ("said0", 0.4)),
    (5, "again", ("resume0", 0), ("reply1", 2.75)),      # remembered: no question
    (6, "outbound", ("pickup", -1.4), ("pickup", 0.3)),
    (6, "outbound", ("reply0", -0.35), ("reply0", 1.0)),  # "Ram Ram Tau ji!"
    (6, "outbound", ("reply0", 3.85), ("reply0", 8.6)),  # "...khatam hone aala hoga"
    (6, "outbound", ("say0", -0.2), ("said0", 0.4)),
    (6, "outbound", ("resume0", 0), ("reply1", 2.2)),    # उधार
]
EDL[5] = (1, "first", ("resume1", 0), ("reply2", -0.05))  # the parchi writes "Surf Excel" (reply trimmed)

STAMPS = {
    -1: ("एक कॉल", "one phone call → this order"),
    0: ("AI मुंशी", "answers the kirana's phone"),
    1: ("पूछता है", "not sure which one? it asks"),
    2: ("लाल पेन", "changed your mind? corrected"),
    3: ("आपकी हाँ", "nothing is ordered without your “haan”"),
    4: ("शोर में भी", "same order, noisy street line: still right"),
    5: ("याद रखता है", "remembers how you talk"),
    6: ("ख़ुद फ़ोन करता है", "calls before you run out, in Haryanvi"),
}
TILT = [-2, 1.5, -1, 2, -1.5, 1, -2]

CAPTIONS = [  # (beat, scene, start anchor, end anchor, speaker, text)
    (0, "first", ("say0", 0), ("said0", 0.3), S, "Bhaiya, do kilo Aashirvaad atta, ek litre Amul Taaza doodh, aur kapde dhone wala powder bhej do."),
    (0, "first", ("reply1", 0), ("reply1", 2.3), M, "Theek hai, do kilo Aashirvaad atta…"),
    (1, "first", ("reply1", 4.8), ("reply1", 8.65), M, "Kaun sa washing powder? Surf Excel, Tide ya Ghadi?"),
    (1, "first", ("say1", 0), ("said1", 0.3), S, "Surf Excel wala."),
    (2, "first", ("say2", 0), ("said2", 0.3), S, "Aur haan, doodh… nahi nahi, doodh do litre kar do."),
    (2, "first", ("reply3", 0), ("reply3", 2.4), M, "Do litre Amul Taaza doodh kar diya."),
    (3, "first", ("say3", 0), ("said3", 0.4), S, "Bas itna hi."),
    (3, "first", ("reply4", 8.63), ("reply4", 9.5), M, "…kul ₹356. Confirm kar doon?"),
    (3, "first", ("say4", 0), ("said4", 0.4), S, "Haan, confirm kar do."),
    (3, "first", ("reply5", 0), ("reply5", 1.25), M, "Order confirm!"),
    (4, "first_noisy", ("say0", 3.0), ("said0", 0.3), S, "…ek litre Amul Taaza doodh, aur kapde dhone wala powder bhej do."),
    (5, "again", ("reply0", 0), ("reply0", 0.98), M, "Namaste Sharma ji!"),
    (5, "again", ("say0", 0), ("said0", 0.3), S, "Wahi kapde dhone wala powder bhej do."),
    (5, "again", ("reply1", 0), ("reply1", 2.75), M, "Achha ji. Ek packet Surf Excel."),
    (6, "outbound", ("reply0", 0), ("reply0", 1.0), M, "Ram Ram Tau ji!"),
    (6, "outbound", ("reply0", 3.96), ("reply0", 8.75), M, "Thara ek litre Amul Taaza doodh, chaar packet Parle-G khatam hone aala hoga."),
    (6, "outbound", ("say0", 0), ("said0", 0.3), T, "Haan, bhej de. Khaate mein likh diyo."),
    (6, "outbound", ("reply1", 0), ("reply1", 1.3), M, "Theek se, udhaar mein likh dunga."),
    (6, "outbound", ("reply1", 1.7), ("reply1", 2.2), M, "Order pakka!"),
]
ZOOMS = [  # (scene, from anchor, zoom, centre x, centre y) in app-frame pixels
    ("first", ("reply3", -0.9), 1.18, 540, 715),
    ("first", ("end", -0.1), 1.16, 740, 980),
    ("first", ("end", 0.6), 1.16, 740, 980),         # cold open (same framing as the stamp shot)
    ("first_noisy", ("resume0", 0.2), 1.1, 540, 760),
    ("again", ("reply1", -0.4), 1.16, 540, 640),
    ("outbound", ("end", -0.1), 1.16, 740, 980),
]
RING = ("outbound", ("pickup", -1.4), ("pickup", -0.02))
CALLOUT = ("again", ("resume0", 0.6))   # "remembered" lands on the parchi line
CALLOUT_LINE_Y = 690                    # app-frame y just under the first parchi line
THUDS = [("first", ("end", 0.05)), ("outbound", ("end", 0.05))]


class Film:
    def __init__(self):
        self.scenes = {n: load_scene(n) for n in ("first", "first_noisy", "again", "outbound")}
        self.tl = Timeline(self.scenes, EDL)
        self.total = round(self.tl.footage + END_LEN, 2)
        assert self.total <= 60.0, f"film is {self.total:.1f} s: trim the EDL"
        tl = self.tl
        self.beats = {b: tl.beat_span(b) for b in STAMPS}
        self.music_start = REENTRY - self.beats[1][0]   # the groove re-enters as the second stamp lands
        # each stamp lands on the music's nearest beat (the first is already down on frame one)
        per = 60 / BPM
        def snap(x):
            k = round((x + self.music_start - BEAT0) / per)
            y = BEAT0 + k * per - self.music_start
            return y if abs(y - x) <= per / 2 + 1e-6 else x
        self.stamp_at = {b: (b0 if b == -1 else snap(b0)) for b, (b0, _) in self.beats.items()}
        self.caps = []
        for beat, name, a, b, who, text in CAPTIONS:
            sc = self.scenes[name]
            for _, o0, o1 in tl.to_out(name, at(sc, a), at(sc, b)):
                self.caps.append((beat, o0, o1, who, text))
        self.caps.sort(key=lambda c: c[1])
        name, anc = CALLOUT
        self.callout_on = tl.to_out(name, at(self.scenes[name], anc), at(self.scenes[name], anc) + 0.01)[0][1]
        self.zooms = []
        for name, anc, z, cx, cy in ZOOMS:
            t = at(self.scenes[name], anc)
            for i, o0, _ in tl.to_out(name, t, t + 0.01):
                self.zooms.append((i, o0, z, cx, cy))

    # ---------------------------------------------------------------- per-frame state
    def state(self, t: float) -> dict:
        tl = self.tl
        s = {"progress": min(1.0, t / self.total), "end": None}
        if t >= tl.footage:
            k = t - tl.footage
            s["end"] = {"phase": 1 if k < 1.9 else 2, "k": k if k < 1.9 else k - 1.9, "fade": k / 0.3}
            t = tl.footage - 1 / FPS
        seg, name, src = tl.source(t)
        _, s0, s1, o = tl.segs[seg]
        sc = self.scenes[name]
        # camera: slow push-in per shot, plus a push towards the moment that matters
        z, cx, cy = 1.0 + 0.03 * ease((t - o) / max(0.1, s1 - s0)), 540.0, CAM_CY
        for i, o0, zt, zx, zy in self.zooms:
            if i == seg and t >= o0:
                k = ease((t - o0) / 0.45)
                z, cx, cy = z + (zt - z) * k, cx + (zx - cx) * k, cy + (zy - cy) * k
        sc_ = S0 * z
        vw, vh = CAM_W / sc_, CAM_H / sc_
        x0 = min(max(0.0, cx - vw / 2), 1080 - vw)
        y0 = min(max(0.0, cy - vh / 2), 1920 - vh)
        i = max(0, int(np.searchsorted(sc.times, src, side="right")) - 1)
        s.update(img=(RAW / name / "frames" / sc.files[i]).as_uri(), left=-x0 * sc_, top=-y0 * sc_, scale=sc_)
        # call bar
        if name == "outbound":
            pk = sc.anchors["pickup"]
            if src < pk:
                s["call"] = {"t": "Sharma General Store", "s": "is calling Tau ji…", "ring": True, "ringPhase": src}
            else:
                s["call"] = {"t": "Tau ji", "s": f"Sharma General Store called  {self._clock(src - pk)}"}
        else:
            start = sc.anchors.get("dial", 0)
            sub = "Same call on a noisy street line" if name == "first_noisy" else \
                f"Calling Sharma General Store  {self._clock(src - start)}"
            s["call"] = {"t": "Sharma ji", "s": sub}
        s["wave"] = self._wave(sc, src)
        # beat stamp
        beat = tl.beats[seg]
        b0, b1 = self.beats[beat]
        d, e = STAMPS[beat]
        # the opening stamp has already landed on frame one (it is also the thumbnail)
        s["stamp"] = {"d": d, "e": e, "k": t - self.stamp_at[beat] + (1.0 if beat == -1 else 0.0), "out": (t - (b1 - 0.22)) / 0.22, "tilt": TILT[beat % len(TILT)]}
        # conversation: lines of this beat that have started; the newest grows word by word
        live = [c for c in self.caps if c[0] == beat and c[1] <= t]
        live = live[-3:]
        lines = []
        for j, (_, c0, c1, who, text) in enumerate(live):
            newest = j == len(live) - 1
            n = len(text.split())
            shown = n if not newest else int(np.ceil(n * min(1.0, (t - c0) / max(0.3, (c1 - c0) * 0.8))))
            lines.append({"who": who, "cls": "shop" if who == M else "caller", "text": text, "old": not newest,
                          "shown": shown, "alpha": 1.0 if newest else (0.6 if j == len(live) - 2 else 0.32)})
        s["lines"] = lines
        # memory callout, pinned under the remembered parchi line
        if beat == 5 and t >= self.callout_on:
            s["callout"] = {"x": 70 + s["left"] + 110 * sc_, "y": 52 + s["top"] + CALLOUT_LINE_Y * sc_,
                            "k": t - self.callout_on, "out": (t - (b1 - 0.22)) / 0.22}
        else:
            s["callout"] = None
        s["note"] = 1.0
        return s

    @staticmethod
    def _clock(x: float) -> str:
        x = max(0, int(x))
        return f"{x // 60}:{x % 60:02d}"

    @staticmethod
    def _wave(sc, src: float) -> list:
        bars = []
        for k in range(40):
            tt = src - (39 - k) * 0.045
            a, b = int(max(0, tt) * SR), int(max(0, tt + 0.045) * SR)
            c = float(np.sqrt(np.mean(sc.caller[a:b] ** 2))) if b > a else 0.0
            m = float(np.sqrt(np.mean(sc.shop[a:b] ** 2))) if b > a else 0.0
            bars.append({"l": max(c, m) * 7, "c": "#f4b400" if m >= c else "#aeb8ec"})
        return bars

    # ---------------------------------------------------------------- sound
    def voice_track(self) -> np.ndarray:
        tl, pieces, fade = self.tl, [], int(0.03 * SR)
        for i, (name, s0, s1, _) in enumerate(tl.segs):
            x = self.scenes[name].mix[int(s0 * SR):int(s1 * SR)].copy()
            if tl.beats[i] == -1:
                x[:] = 0  # cold open: music and the stamp only
            x[:fade] *= np.linspace(0, 1, fade)
            x[-fade:] *= np.linspace(1, 0, fade)
            pieces.append(x)
        a = np.concatenate(pieces)
        a = np.concatenate([a, np.zeros(int((self.total - len(a) / SR + 1) * SR), dtype=np.float32)])

        def put(x, t0):
            i = int(t0 * SR)
            a[i:i + len(x)] += x[: len(a) - i]
        name, r0, r1 = RING
        sc = self.scenes[name]
        for _, o0, o1 in tl.to_out(name, at(sc, r0), at(sc, r1)):
            put(ring(o1 - o0), o0)
        for name, anc in THUDS:
            tt = at(self.scenes[name], anc)
            for _, o0, _ in tl.to_out(name, tt, tt + 0.01):
                put(thud(), o0)
        for b, t0 in self.stamp_at.items():  # a stamp sound for each beat title, on the beat
            put(thud() * (0.8 if b == -1 else 0.35), t0 + 0.06)
        e = int(tl.footage * SR)
        a[e:] *= np.exp(-np.arange(len(a) - e) / (0.35 * SR))
        return np.clip(a[: int(self.total * SR)], -1, 1)


def soundtrack(film: Film) -> np.ndarray:
    """Voices + effects, with the music dipped in the speech band and ducked under every word. 48 kHz stereo."""
    from eval.channel import resample
    v = resample(film.voice_track(), SR, MSR)
    n = len(v)
    if not MUSIC.exists():
        return np.stack([v, v], 1)
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(film.music_start), "-t", str(film.total + 0.5),
                          "-i", str(MUSIC), "-af", "equalizer=f=2200:t=q:w=1.3:g=-5,highpass=f=40", "-ac", "2",
                          "-ar", str(MSR), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    m = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)[:n]
    m = np.pad(m, ((0, n - len(m)), (0, 0)))
    # voice activity: 20 ms RMS, held 350 ms so the music doesn't pump between words
    hop = MSR // 50
    env = np.array([np.sqrt(np.mean(v[i:i + hop] ** 2)) for i in range(0, n, hop)])
    active = env > 0.012
    hold = 18
    held = np.array([active[max(0, i - hold):i + 3].any() for i in range(len(active))])
    v_rms = float(np.sqrt(np.mean(v[np.repeat(active, hop)[:n]] ** 2)))
    m_rms = float(np.sqrt(np.mean(m ** 2))) + 1e-9
    g_open, g_duck = v_rms / m_rms * 10 ** (-9 / 20), v_rms / m_rms * 10 ** (-23 / 20)
    target = np.where(held, g_duck, g_open)
    g = np.empty_like(target)  # smooth: fast down (60 ms), slow up (400 ms)
    g[0] = target[0]
    for i in range(1, len(target)):
        a = 1 - np.exp(-1 / (3 if target[i] < g[i - 1] else 20))
        g[i] = g[i - 1] + (target[i] - g[i - 1]) * a
    gain = np.interp(np.arange(n), np.arange(len(g)) * hop, g)
    md = m * gain[:, None]
    # report: how far the voice sits above the music while someone is speaking
    sp = np.repeat(active, hop)[:n]
    margin = 20 * np.log10(v_rms / (float(np.sqrt(np.mean(md[sp] ** 2))) + 1e-9))
    print(f"  speech sits {margin:.1f} dB above the music while talking; music rises "
          f"{20 * np.log10(g_open / g_duck):.0f} dB in the gaps")
    return np.clip(np.stack([v, v], 1) + md, -1, 1)


async def render(film: Film, out: Path, stills: list[float] | None = None) -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        p = await b.new_page(viewport={"width": W, "height": H})
        await p.goto((ROOT / "video" / "film.html").as_uri(), wait_until="networkidle")
        await p.evaluate("ready()")
        if stills is not None:
            from PIL import Image
            import io
            ims = []
            for t in stills:
                await p.evaluate("s => apply(s)", film.state(t))
                ims.append(Image.open(io.BytesIO(await p.screenshot(type="jpeg", quality=85))).resize((640, 360)))
            sheet = Image.new("RGB", (640 * 3, 360 * ((len(ims) + 2) // 3)))
            for k, im in enumerate(ims):
                sheet.paste(im, ((k % 3) * 640, (k // 3) * 360))
            sheet.save(out, quality=88)
            await b.close()
            return
        mix = OUT / "film_mix.wav"
        sf.write(mix, soundtrack(film), MSR)
        music = MUSIC if MUSIC.exists() else None
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-c:v", "mjpeg", "-r", str(FPS), "-i", "-",
               "-i", str(mix), "-af", "loudnorm=I=-15:TP=-1.5:LRA=11", "-map", "0:v", "-map", "1:a"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-profile:v", "high",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
                "-t", str(film.total), str(out)]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        n = int(film.total * FPS)
        for k in range(n):
            await p.evaluate("s => apply(s)", film.state(k / FPS))
            ff.stdin.write(await p.screenshot(type="jpeg", quality=93))
            if k % 300 == 0:
                print(f"  frame {k}/{n}", flush=True)
        ff.stdin.close()
        ff.wait()
        await b.close()
        print("wrote", out, "(music: " + (music.name if music else "none") + ")")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stills", nargs="*", type=float)
    a = ap.parse_args()
    film = Film()
    print(f"footage {film.tl.footage:.2f} s + end {END_LEN} s = {film.total:.2f} s")
    for b, (b0, b1) in film.beats.items():
        print(f"  beat {b} {STAMPS[b][1]:45s} {b0:5.2f}-{b1:5.2f}")
    if a.stills is not None:
        ts = a.stills or [0.0, 1.5, 6.5, 11.0, 18.0, 25.0, 31.0, 36.0, 42.0, 50.0, film.tl.footage + 1.5,
                          film.tl.footage + 4.0]
        asyncio.run(render(film, OUT / "film_stills.jpg", ts))
        print("wrote", OUT / "film_stills.jpg")
    else:
        asyncio.run(render(film, OUT / "bharatvoice_film.mp4"))


if __name__ == "__main__":
    main()
