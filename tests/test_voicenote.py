"""Voice-note chunking: long notes are split at quiet points into <= 20 s pieces (offline, no API)."""
import io

import numpy as np
import soundfile as sf

from app.server import CHUNK_S, _chunks


def make_note(seconds=47.0, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    x = 0.3 * np.sin(2 * np.pi * 220 * t).astype("float32")
    for gap in (17.0, 35.0):  # pauses between sentences
        x[int(gap * sr):int((gap + 0.5) * sr)] = 0.0
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue(), len(x) / sr


def test_chunks_are_short_and_cover_everything():
    wav, total = make_note()
    parts = _chunks(wav)
    lens = [sf.info(io.BytesIO(p)).duration for p in parts]
    assert all(l <= CHUNK_S + 1e-6 for l in lens) and len(parts) >= 3
    assert abs(sum(lens) - total) < 0.05


def test_chunks_cut_in_pauses():
    wav, _ = make_note()
    first = sf.info(io.BytesIO(_chunks(wav)[0])).duration
    assert 17.0 <= first <= 17.5  # cut inside the pause at 17 s, not mid-word at 20 s
