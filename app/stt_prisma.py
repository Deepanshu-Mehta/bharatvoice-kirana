"""Prisma STT with dialogue-conditioned biasing (plan U1). Every call goes through the guarded client."""
from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from app.brain.catalog import load_catalog
from app.gnani_client import GnaniClient, get_client

ROOT = Path(__file__).resolve().parent.parent
TOPIC = ("Kirana grocery phone order in Hindi, Hinglish or Haryanvi: atta, dal, chawal, tel, doodh, cheeni, masale, "
         "biscuit, sabun; brand names; quantities in kilo, gram, litre, packet, darjan; aadha, paav, sawa, dhai.")
MAX_UTTERANCE_S = 20.0
# Short brand names collide with Hindi function words under biasing (Day-1 finding: "do packet" -> "Dove packet").
# They are excluded from the static keyterm padding and only added dynamically when actually in play.
SHORT_BRAND_MAX = 4


def _collision_prone(term: str) -> bool:
    return len(term.replace(" ", "").replace("-", "")) <= SHORT_BRAND_MAX


@dataclass
class STTConfig:
    model: str = os.environ.get("STT_MODEL", "prisma-v3.0")
    language_code: str = os.environ.get("STT_LANG", "hi-IN,en-IN")
    format: str | None = os.environ.get("STT_FORMAT", "codemix")  # keeps spoken numbers ("paav" stays a word)
    biasing: str = os.environ.get("STT_BIASING", "dynamic")  # none | static | dynamic
    substitution: bool = os.environ.get("STT_SUBSTITUTION", "1") == "1"
    extra_keyterms: list[str] = field(default_factory=list)

    def name(self) -> str:
        return f"{self.model}|{self.language_code}|{self.format}|{self.biasing}|sub={int(self.substitution)}"


def load_substitutions() -> list[dict]:
    """Seed rules + mined rules (eval/mine_aliases.py), merged per replacement, capped at Prisma's 10 rules."""
    rules: dict[str, list[str]] = {}
    files = [ROOT / "data" / "substitution_rules.json"]
    if os.environ.get("MINED_ALIASES", "1") == "1":
        files.append(ROOT / "data" / "substitution_rules_mined.json")
    for p in files:
        if p.exists():
            for r in json.loads(p.read_text()):
                pats = rules.setdefault(r["replacement"], [])
                pats += [x for x in r["patterns"] if x not in pats and x.lower() != r["replacement"].lower()]
    return [{"patterns": v[:20], "replacement": k} for k, v in rules.items() if v][:10]


def build_context(session, cfg: STTConfig) -> dict:
    """keyterms / previous_text / topic for the next utterance, from the dialogue state."""
    if cfg.biasing == "none" or cfg.model != "prisma-v3.0":
        return {}
    cat = load_catalog()
    terms: list[str] = []
    if cfg.biasing == "dynamic" and session is not None:
        if session.pending:
            for p in session.pending.candidates:
                terms += [t for t in ([p.brand] if p.brand else []) + p.aliases[:2] if t.isascii()]
        for line in session.cart:
            if line.product.brand:
                terms.append(line.product.brand)
    terms += cfg.extra_keyterms
    terms += [t for t in cat.top_terms() if not _collision_prone(t)]
    seen, out = set(), []
    for t in terms:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    ctx = {"keyterms": out[:200], "topic": TOPIC}
    if cfg.biasing == "dynamic" and session is not None and session.last_agent_prompt:
        ctx["previous_text"] = session.last_agent_prompt
    return ctx


def to_wav16k(audio: bytes) -> tuple[bytes, float]:
    """Decode any soundfile-readable audio, mono 16 kHz PCM16 WAV, trimmed to MAX_UTTERANCE_S."""
    data, sr = sf.read(io.BytesIO(audio), dtype="float32", always_2d=True)
    data = data.mean(axis=1)
    if sr != 16000:
        n = int(len(data) * 16000 / sr)
        data = np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data).astype("float32")
    data = data[: int(MAX_UTTERANCE_S * 16000)]
    buf = io.BytesIO()
    sf.write(buf, data, 16000, format="WAV", subtype="PCM_16")
    return buf.getvalue(), len(data) / 16000.0


def transcribe(audio: bytes, session=None, cfg: STTConfig | None = None, client: GnaniClient | None = None,
               filename: str = "utt.wav") -> dict:
    cfg = cfg or STTConfig()
    client = client or get_client()
    ctx = build_context(session, cfg)
    r = client.stt(audio, filename, model=cfg.model, language_code=cfg.language_code, format=cfg.format,
                   substitution_map=load_substitutions() if cfg.substitution else None, **ctx)
    r["_config"] = cfg.name()
    r["_context"] = {k: (v if k != "keyterms" else len(v)) for k, v in ctx.items()}
    return r
