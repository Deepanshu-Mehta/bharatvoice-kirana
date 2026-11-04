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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.brain.dm import Session
from app.brain.extract_rules import extract as rules_extract
from app.gnani_client import CreditGuardError, GnaniAPIError, get_client
from app.stt_prisma import STTConfig, to_wav16k, transcribe
from app.tts_timbre import synth_stream

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


async def speak(ws: WebSocket, text: str, timings: dict) -> None:
    """Stream the reply: reply_start (text) -> binary PCM16 16 kHz chunks -> reply_end."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def produce():
        try:
            for ch in synth_stream(text):
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
                await speak(ws, result.reply, timings)
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
