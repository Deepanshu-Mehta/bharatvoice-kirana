"""BharatVoice call server: browser mic -> Prisma -> brain -> Timbre -> browser, plus a live shopkeeper board.

  uv run uvicorn app.server:app --port 8080        then open http://localhost:8080

WebSocket /ws/call?customer=CUST-001&extractor=rules|evon
  client -> server: binary WAV (one VAD-segmented utterance)  |  {"type": "text", "text": "..."} (typed turn)
  server -> client: transcript / reply (+ base64 WAV) / cart / end / error
Every turn appends a JSON trace to traces/<session>.jsonl (the eval harness reads the same format).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path

import subprocess
import uuid

import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.brain.dm import LEDGER, Session
from app.brain.templates import render, speak_money
from app.brain.extract_rules import extract as rules_extract
from app.gnani_client import CreditGuardError, GnaniAPIError, get_client
from app.stt_prisma import STTConfig, to_wav16k, transcribe
from app.tts_timbre import synth, synth_stream

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
TRACES = ROOT / "traces"
TRACES.mkdir(exist_ok=True)

app = FastAPI(title="BharatVoice")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

board_clients: set[WebSocket] = set()
board_log: list[dict] = []  # recent events replayed to a newly opened board
_evon = None


_hybrid = None


def get_extractor(name: str):
    global _evon, _hybrid
    if name == "hybrid":
        if _hybrid is None:
            from app.brain.extract_hybrid import HybridExtractor
            _hybrid = HybridExtractor(get_extractor("evon"))
        return _hybrid
    if name == "evon":
        if _evon is None:
            from app.brain.extract_evon import EvonExtractor
            _evon = EvonExtractor()
        return _evon
    return rules_extract


async def broadcast(event: dict) -> None:
    board_log.append(event)
    del board_log[:-200]
    dead = []
    for ws in board_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        board_clients.discard(ws)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/board")
def board():
    return FileResponse(STATIC / "board.html")


@app.get("/api/ledger", response_class=PlainTextResponse)
def ledger():
    return get_client().report()


@app.websocket("/ws/board")
async def ws_board(ws: WebSocket):
    await ws.accept()
    board_clients.add(ws)
    for e in board_log:
        await ws.send_json(e)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        board_clients.discard(ws)


async def speak(ws: WebSocket, text: str, timings: dict, speed: float = 1.0) -> None:
    """Stream the reply: reply_start (text) -> binary PCM16 16 kHz chunks -> reply_end."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def produce():
        try:
            for ch in synth_stream(text, speed=speed):
                loop.call_soon_threadsafe(q.put_nowait, ch)
        except Exception as e:  # CreditGuardError / GnaniAPIError / network
            loop.call_soon_threadsafe(q.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    await ws.send_json({"type": "reply_start", "text": text, "sample_rate": 16000})
    t0 = time.perf_counter()
    loop.run_in_executor(None, produce)
    first = True
    while (item := await q.get()) is not None:
        if isinstance(item, Exception):
            await ws.send_json({"type": "error", "text": f"TTS: {item}"})
            continue
        if first:
            timings["tts_first_ms"] = round((time.perf_counter() - t0) * 1000)
            first = False
        await ws.send_bytes(item)
    timings["tts_ms"] = round((time.perf_counter() - t0) * 1000)
    await ws.send_json({"type": "reply_end"})


@app.websocket("/ws/call")
async def ws_call(ws: WebSocket):
    await ws.accept()
    customer = ws.query_params.get("customer", "CUST-001")
    extractor_name = ws.query_params.get("extractor", os.environ.get("EXTRACTOR", "rules"))
    session = Session(customer, extractor=get_extractor(extractor_name))
    cfg = STTConfig()
    trace_path = TRACES / f"{session.session_id}.jsonl"
    await broadcast({"type": "call_started", "session": session.session_id, "customer": customer,
                     "extractor": extractor_name})
    greet = session.greet()
    await speak(ws, greet, {})
    turn = 0
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            turn += 1
            timings: dict = {}
            t_turn = time.perf_counter()
            stt_info: dict = {}
            if msg.get("bytes"):
                wav, secs = await asyncio.to_thread(to_wav16k, msg["bytes"])
                timings["audio_s"] = round(secs, 2)
                if secs < 0.3:
                    continue
                t0 = time.perf_counter()
                try:
                    r = await asyncio.to_thread(transcribe, wav, session, cfg)
                except (CreditGuardError, GnaniAPIError) as e:
                    await ws.send_json({"type": "error", "text": f"STT: {e}"})
                    continue
                timings["stt_ms"] = round((time.perf_counter() - t0) * 1000)
                text = r.get("transcript", "")
                stt_info = {k: r.get(k) for k in ("model", "detected_language_code", "processing_time", "_config",
                                                   "_context", "_cached")}
                (TRACES / "audio").mkdir(exist_ok=True)
                (TRACES / "audio" / f"{session.session_id}-{turn:02d}.wav").write_bytes(wav)
            else:
                data = json.loads(msg.get("text") or "{}")
                text = data.get("text", "")
            await ws.send_json({"type": "transcript", "text": text})
            t0 = time.perf_counter()
            result = await asyncio.to_thread(session.handle, text)
            timings["brain_ms"] = round((time.perf_counter() - t0) * 1000)
            for e in result.events:
                await broadcast(e)
            await ws.send_json({"type": "cart", "cart": session.cart_snapshot(), "total": session.total,
                                "pending": session.pending.kind if session.pending else None,
                                "register": session.register,
                                "source": result.frame.source if result.frame else None})
            if result.reply:
                await speak(ws, result.reply, timings, speed=0.85 if session.slow else 1.0)
            timings["turn_ms"] = round((time.perf_counter() - t_turn) * 1000)
            if "tts_first_ms" in timings:  # what the caller feels: end of speech -> first audio
                timings["to_first_audio_ms"] = (timings.get("stt_ms", 0) + timings.get("brain_ms", 0)
                                                + timings["tts_first_ms"])
            with trace_path.open("a") as f:
                f.write(json.dumps({"turn": turn, "stt": stt_info, "timings": timings, **result.trace},
                                   ensure_ascii=False) + "\n")
            await ws.send_json({"type": "timings", **timings})
            if session.order or session.handed_off:
                await ws.send_json({"type": "end", "order": session.order, "handoff": session.handed_off})
    except WebSocketDisconnect:
        pass
    finally:
        await broadcast({"type": "call_ended", "session": session.session_id})


# ---------------------------------------------------------------- udhaar khata
CUSTOMERS = yaml.safe_load((ROOT / "data" / "customers.yaml").read_text())
SHOP = "Sharma General Store"


@app.get("/api/khata")
def khata():
    return [{"customer": cid, "name": c.get("name", cid), "register": c.get("register", "hinglish"),
             "limit": c.get("credit_limit", 0), "balance": LEDGER.get(cid, 0.0)} for cid, c in CUSTOMERS.items()]


@app.post("/api/remind/{cid}")
async def remind(cid: str):
    c = CUSTOMERS.get(cid)
    if c is None:
        raise HTTPException(404, "unknown customer")
    text = render("reminder", c.get("register", "hinglish"), name=c.get("name", ""), shop=SHOP,
                  balance=speak_money(LEDGER.get(cid, 0.0)))
    try:
        audio = await asyncio.to_thread(synth, text)
    except (CreditGuardError, GnaniAPIError) as e:
        raise HTTPException(503, f"TTS: {e}")
    return {"text": text, "audio": base64.b64encode(audio).decode()}


# ---------------------------------------------------------------- WhatsApp voice-note inbox
DRAFTS: dict[str, Session] = {}
CHUNK_S = 20.0


def _decode_any(raw: bytes) -> bytes:
    """Any container WhatsApp/phones produce (ogg/opus, m4a, mp3, wav) -> 16 kHz mono PCM16 WAV via ffmpeg."""
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ar", "16000", "-ac", "1",
                           "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"], input=raw, capture_output=True, check=True).stdout


def _chunks(wav: bytes) -> list[bytes]:
    """Split at the quietest point near every 20 s (Prisma REST takes <= 30 s per request)."""
    import io

    import numpy as np
    import soundfile as sf
    x, sr = sf.read(io.BytesIO(wav), dtype="float32")
    out, start, n = [], 0, len(x)
    while start < n:
        end = min(n, start + int(CHUNK_S * sr))
        if end < n:  # look back up to 4 s for the quietest 100 ms window
            lo = max(start + int(8 * sr), end - int(4 * sr))
            win = int(0.1 * sr)
            energies = [(float(np.mean(x[i:i + win] ** 2)), i) for i in range(lo, end - win, win)]
            if energies:
                end = min(energies)[1] + win // 2
        buf = io.BytesIO()
        sf.write(buf, x[start:end], sr, format="WAV", subtype="PCM_16")
        out.append(buf.getvalue())
        start = end
    return out


def _draft_view(did: str, s: Session, transcript: str) -> dict:
    pend = s.pending
    return {"draft_id": did, "customer": s.customer_id, "transcript": transcript, "cart": s.cart_snapshot(),
            "total": s.total, "question": pend.question.removeprefix("ASK::") if pend else None,
            "choices": [{"sku": p.id, "name": p.name} for p in (pend.candidates if pend else [])],
            "pending": pend.kind if pend else None, "order": s.order}


@app.post("/api/voicenote")
async def voicenote(file: UploadFile = File(...), customer: str = Form("CUST-001")):
    raw = await file.read()
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, "voice note too large")
    try:
        wav = await asyncio.to_thread(_decode_any, raw)
    except subprocess.CalledProcessError:
        raise HTTPException(400, "could not decode audio")
    session = Session(customer, extractor=get_extractor(os.environ.get("EXTRACTOR", "rules")))
    session.greet()
    cfg = STTConfig()
    texts: list[str] = []
    for chunk in _chunks(wav):
        session.last_agent_prompt = " ".join(texts)[-600:] or session.last_agent_prompt  # previous_text chaining
        try:
            r = await asyncio.to_thread(transcribe, chunk, session, cfg)
        except (CreditGuardError, GnaniAPIError) as e:
            raise HTTPException(503, f"STT: {e}")
        texts.append(r.get("transcript", ""))
    transcript = " ".join(t for t in texts if t)
    result = await asyncio.to_thread(session.handle, transcript)
    did = uuid.uuid4().hex[:8]
    DRAFTS[did] = session
    view = _draft_view(did, session, transcript)
    view["reply"] = result.reply
    await broadcast({"type": "voicenote_draft", **view})
    return view


@app.post("/api/voicenote/{did}/choose")
async def voicenote_choose(did: str, sku: str = Form(...)):
    s = DRAFTS.get(did)
    if s is None or s.pending is None:
        raise HTTPException(404, "no open question")
    pend = s.pending
    s.pending = None
    s._commit(s.cat.get(sku), pend.qty)
    view = _draft_view(did, s, "")
    await broadcast({"type": "voicenote_draft", **view})
    return view


@app.post("/api/voicenote/{did}/confirm")
async def voicenote_confirm(did: str):
    s = DRAFTS.get(did)
    if s is None or not s.cart:
        raise HTTPException(404, "no draft")
    s.awaiting_confirm = True  # the shopkeeper's tap is the explicit confirmation for a voice-note order
    s._events = []
    s._place()
    for e in s._events:
        await broadcast(e)
    return _draft_view(did, s, "")
