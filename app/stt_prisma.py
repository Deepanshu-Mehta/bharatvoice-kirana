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
    second_opinion: bool = os.environ.get("STT_SECOND_OPINION", "0") == "1"  # plan T4
    extra_keyterms: list[str] = field(default_factory=list)

    def name(self) -> str:
        return (f"{self.model}|{self.language_code}|{self.format}|{self.biasing}|sub={int(self.substitution)}"
                f"|2nd={int(self.second_opinion)}")


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


def parse_score(transcript: str, session=None) -> tuple[float, bool]:
    """How well the agent can understand this transcript: (score, weak). Uses the same extractor + resolver."""
    from app.brain.dm import TAU_LOW
    from app.brain.extract_rules import Context, extract
    from app.brain.resolver import Resolver
    global _res
    try:
        _res
    except NameError:
        _res = Resolver()
    words = transcript.split()
    if not words:
        return 0.0, False
    ctx = Context(pending=session.pending.kind if session is not None and session.pending else None,
                  awaiting_confirm=bool(session is not None and session.awaiting_confirm))
    f = extract(transcript, ctx)
    if not f.actions:
        return 0.0, len(words) >= 2
    score, weak = 0.0, False
    for a in f.actions:
        if a.type == "add" and a.product_phrase:
            r = _res.resolve(a.product_phrase, k=3)
            p = r.p_correct if r.candidates else 0.0
            score += p
            weak |= p < TAU_LOW
        else:
            score += 1.0
    return score / len(f.actions), weak


def transcribe(audio: bytes, session=None, cfg: STTConfig | None = None, client: GnaniClient | None = None,
               filename: str = "utt.wav") -> dict:
    cfg = cfg or STTConfig()
    client = client or get_client()
    ctx = build_context(session, cfg)
    subs = load_substitutions() if cfg.substitution else None
    r = client.stt(audio, filename, model=cfg.model, language_code=cfg.language_code, format=cfg.format,
                   substitution_map=subs, **ctx)
    r["_config"] = cfg.name()
    r["_context"] = {k: (v if k != "keyterms" else len(v)) for k, v in ctx.items()}
    if cfg.second_opinion and cfg.model == "prisma-v3.0":
        s1, weak = parse_score(r.get("transcript", ""), session)
        if weak:  # only hard turns pay for a second call (one-request limit: sequential, ~0.2-0.3 s)
            r2 = client.stt(audio, filename, model="prisma-v3.0", language_code="indic-auto", format="transliterate",
                            substitution_map=subs, **ctx)
            s2, _ = parse_score(r2.get("transcript", ""), session)
            r["_second_opinion"] = {"primary": round(s1, 3), "translit": round(s2, 3), "chosen": "primary"}
            if s2 > s1 + 0.05:
                r["_second_opinion"]["chosen"] = "translit"
                r["_primary_transcript"] = r.get("transcript", "")
                r["transcript"] = r2.get("transcript", "")
    return r
