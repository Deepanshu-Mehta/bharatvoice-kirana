"""Timbre TTS for agent replies. Cached forever by (text, voice, model), so common phrases cost credits once."""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.gnani_client import GnaniClient, get_client

ROOT = Path(__file__).resolve().parent.parent
VOICE = os.environ.get("AGENT_VOICE", "Poorvi")
MODEL = os.environ.get("AGENT_TTS_MODEL", "timbre-v2.5")
LANG = os.environ.get("AGENT_TTS_LANG", "hi-en")  # Roman-script Hinglish templates
CLONE_FILE = ROOT / "data" / "voices" / "shopkeeper_embedding.json"  # U6: created with consent only


def speaker_embedding() -> dict | None:
    if os.environ.get("AGENT_CLONED_VOICE") == "1" and CLONE_FILE.exists():
        return json.loads(CLONE_FILE.read_text())
    return None


def synth(text: str, client: GnaniClient | None = None) -> bytes:
    client = client or get_client()
    emb = speaker_embedding()
    if emb is not None:
        return client.tts(text, "", language=LANG, speaker_embedding=emb)
    return client.tts(text, VOICE, model=MODEL, language=LANG)


def synth_stream(text: str, client: GnaniClient | None = None):
    """Yield raw PCM16 16 kHz chunks as Timbre streams them (SSE); cached phrases come back in one chunk."""
    client = client or get_client()
    emb = speaker_embedding()
    if emb is not None:
        yield from client.tts_stream(text, "", language=LANG, speaker_embedding=emb)
    else:
        yield from client.tts_stream(text, VOICE, model=MODEL, language=LANG)
