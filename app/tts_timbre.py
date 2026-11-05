"""Timbre TTS for agent replies. Cached forever by (text, voice, model), so common phrases cost credits once."""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.gnani_client import GnaniClient, get_client

ROOT = Path(__file__).resolve().parent.parent
# Voice lab (infra/voice_lab.py, /voices): a male, conversational v3.0 voice fits the shop's male first-person
# replies ("bol raha hoon"), speaks all 10 languages with one persona, and supports <pauses>. Override via env.
VOICE = os.environ.get("AGENT_VOICE", "Devansh")
MODEL = os.environ.get("AGENT_TTS_MODEL", "timbre-v3.0")
LANG = os.environ.get("AGENT_TTS_LANG", "auto")  # mixed Devanagari + Latin script -> Timbre's Hinglish profile
SPOKEN = os.environ.get("AGENT_SPOKEN_SCRIPT", "1") == "1"
CLONE_FILE = ROOT / "data" / "voices" / "shopkeeper_embedding.json"  # U6: created with consent only


def speaker_embedding() -> dict | None:
    if os.environ.get("AGENT_CLONED_VOICE") == "1" and CLONE_FILE.exists():
        return json.loads(CLONE_FILE.read_text())
    return None


def spoken_text(text: str) -> str:
    """What Timbre actually speaks: mixed script (+ a pause before the closing question on v3.0)."""
    if not SPOKEN:
        return text
    from app.brain.spoken import add_pause_before_question, to_spoken
    t = to_spoken(text)
    return add_pause_before_question(t) if MODEL == "timbre-v3.0" else t


def synth(text: str, client: GnaniClient | None = None, speed: float = 1.0) -> bytes:
    client = client or get_client()
    text = spoken_text(text)
    emb = speaker_embedding()
    if emb is not None:
        return client.tts(text, "", language=LANG, speaker_embedding=emb, speed=speed)
    return client.tts(text, VOICE, model=MODEL, language=LANG, speed=speed)


def synth_stream(text: str, client: GnaniClient | None = None, speed: float = 1.0):
    """Yield raw PCM16 16 kHz chunks as Timbre streams them (SSE); cached phrases come back in one chunk."""
    client = client or get_client()
    text = spoken_text(text)
    emb = speaker_embedding()
    if emb is not None:
        yield from client.tts_stream(text, "", language=LANG, speaker_embedding=emb, speed=speed)
    else:
        yield from client.tts_stream(text, VOICE, model=MODEL, language=LANG, speed=speed)
