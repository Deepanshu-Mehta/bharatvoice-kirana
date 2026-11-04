"""Phone-channel simulation for the eval (plan §8.2).

clean       : 16 kHz TTS audio as generated
g711_10db   : + shop noise at 10 dB SNR, band-pass 300-3400 Hz, 8 kHz, G.711 mu-law encode/decode
g711_5db_loss: + 5 dB SNR and 2% packet loss (20 ms frames zeroed)
Output is always 16-bit PCM WAV (Prisma wants PCM; mu-law is decoded back).
"""
from __future__ import annotations

import io
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
NOISE_DIR = ROOT / "data" / "noise"
CONDITIONS = {
    "clean": None,
    "g711_10db": {"snr": 10.0, "loss": 0.0},
    "g711_5db_loss": {"snr": 5.0, "loss": 0.02},
}


def read(b: bytes) -> tuple[np.ndarray, int]:
    x, sr = sf.read(io.BytesIO(b), dtype="float32", always_2d=True)
    return x.mean(axis=1), sr


def write(x: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.clip(x, -1, 1), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def resample(x: np.ndarray, sr: int, target: int) -> np.ndarray:
    if sr == target:
        return x
    n = int(round(len(x) * target / sr))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype("float32")


def pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    white = rng.standard_normal(n)
    f = np.fft.rfft(white)
    f /= np.sqrt(np.maximum(np.arange(len(f)), 1))
    p = np.fft.irfft(f, n)
    return (p / (np.abs(p).max() + 1e-9)).astype("float32")


def shop_noise(n: int, sr: int, rng: np.random.Generator, babble_clips: list[np.ndarray] | None = None) -> np.ndarray:
    """Recorded ambience from data/noise/*.wav if present, else synthetic: babble (overlapping speech) + street hum."""
    files = sorted(NOISE_DIR.glob("*.wav"))
    if files:
        x, nsr = sf.read(files[rng.integers(len(files))], dtype="float32", always_2d=True)
        x = resample(x.mean(axis=1), nsr, sr)
        if len(x) < n:
            x = np.tile(x, n // len(x) + 1)
        start = rng.integers(0, len(x) - n + 1)
        return x[start:start + n]
    out = 0.5 * pink_noise(n, rng)
    for clip in (babble_clips or [])[:4]:
        c = resample(clip, 16000, sr) if sr != 16000 else clip
        c = np.tile(c, n // max(len(c), 1) + 1)[:n]
        out += 0.6 * np.roll(c, rng.integers(0, n))
    return out


def mix_snr(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    ps = np.mean(speech ** 2) + 1e-12
    pn = np.mean(noise ** 2) + 1e-12
    return speech + noise * np.sqrt(ps / (pn * 10 ** (snr_db / 10)))


def g711(x: np.ndarray, sr: int) -> np.ndarray:
    """Band-pass 300-3400 Hz, 8 kHz, mu-law encode then decode (ffmpeg)."""
    enc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", "-af", "highpass=f=300,lowpass=f=3400",
         "-ar", "8000", "-ac", "1", "-c:a", "pcm_mulaw", "-f", "wav", "pipe:1"],
        input=write(x, sr), capture_output=True, check=True).stdout
    dec = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", "-c:a", "pcm_s16le", "-f", "wav",
                          "pipe:1"], input=enc, capture_output=True, check=True).stdout
    y, _ = read(dec)
    return y


def packet_loss(x: np.ndarray, sr: int, rate: float, rng: np.random.Generator) -> np.ndarray:
    frame = int(0.02 * sr)
    y = x.copy()
    for i in range(0, len(y), frame):
        if rng.random() < rate:
            y[i:i + frame] = 0.0
    return y


def apply(wav: bytes, condition: str, seed: int = 0, babble: list[bytes] | None = None) -> bytes:
    spec = CONDITIONS[condition]
    if spec is None:
        return wav
    rng = np.random.default_rng(seed)
    x, sr = read(wav)
    x = resample(x, sr, 16000)
    clips = [resample(*read(b), 16000) for b in (babble or [])]
    x = mix_snr(x, shop_noise(len(x), 16000, rng, clips), spec["snr"])
    x = x / max(1.0, float(np.abs(x).max()) / 0.95)
    y = g711(x, 16000)
    if spec["loss"]:
        y = packet_loss(y, 8000, spec["loss"], rng)
    return write(y, 8000)
