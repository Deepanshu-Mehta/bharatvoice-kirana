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

import hashlib
import subprocess
import uuid

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.brain.dm import LEDGER, Session
from app.brain.memory import MemoryStore
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

# ---------------------------------------------------------------- public demo guard rails
PUBLIC_DEMO = os.environ.get("PUBLIC_DEMO") == "1"
LIMITS = {"calls_per_ip_hour": 6, "turns_per_call": 25, "call_seconds": 360, "voicenotes_per_ip_hour": 3,
          "reminds_per_ip_hour": 6, "concurrent_calls": 3}
_hits: dict[tuple[str, str], list[float]] = {}
_active_calls = 0


def client_ip(headers, client) -> str:
    fwd = headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (client.host if client else "?")


def allow(ip: str, kind: str, per_hour: int) -> bool:
    if not PUBLIC_DEMO:
        return True
    now = time.time()
    key = (ip, kind)
    hits = [t for t in _hits.get(key, []) if now - t < 3600]
    if len(hits) >= per_hour:
        _hits[key] = hits
        return False
    hits.append(now)
    _hits[key] = hits
    return True
app.mount("/static", StaticFiles(directory=STATIC), name="static")

MEMORY = MemoryStore()  # per-customer memory, learned only from confirmed orders
_visitor_mem: dict[tuple[str, str], object] = {}  # public demo: per-visitor copies, in memory only, never shown to others


def memory_for(ip: str, cid: str):
    """Local: the shop's real memory. Public demo: a private per-visitor copy seeded from the synthetic history,
    so one visitor's second call remembers their first, and nobody else ever sees a stranger's words."""
    if not PUBLIC_DEMO:
        return MEMORY.get(cid)
    k = (hashlib.sha1(ip.encode()).hexdigest()[:12], cid)
    if k not in _visitor_mem:
        if len(_visitor_mem) > 2000:
            _visitor_mem.clear()
        _visitor_mem[k] = MemoryStore(persist=False).get(cid)
    return _visitor_mem[k]


def save_memory(mem) -> None:
    if not PUBLIC_DEMO:
        MEMORY.save(mem)
board_clients: set[WebSocket] = set()
board_log: list[dict] = []  # recent events replayed to a newly opened board
_evon = None


_hybrid = None


def get_extractor(name: str):
    global _evon, _hybrid
    if PUBLIC_DEMO:
        name = "rules"  # strangers never wake the Evon GPU
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
    if PUBLIC_DEMO and "transcript" in event:
        event = {**event, "transcript": ""}  # public board never shows strangers' words
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


@app.get("/api/config")
def config():
    return {"public_demo": PUBLIC_DEMO, "limits": LIMITS if PUBLIC_DEMO else None}


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
    global _active_calls
    await ws.accept()
    ip = client_ip(ws.headers, ws.client)
    if PUBLIC_DEMO and (_active_calls >= LIMITS["concurrent_calls"] or
                        not allow(ip, "call", LIMITS["calls_per_ip_hour"])):
        await ws.send_json({"type": "error", "text": "Demo is busy or you've hit the hourly limit. Please try again later."})
        await ws.close()
        return
    customer = ws.query_params.get("customer", "CUST-001")
    extractor_name = ws.query_params.get("extractor", os.environ.get("EXTRACTOR", "rules"))
    session = Session(customer, extractor=get_extractor(extractor_name), memory=memory_for(ip, customer),
                      on_learn=save_memory)
    cfg = STTConfig()
    trace_path = TRACES / f"{session.session_id}.jsonl"
    await broadcast({"type": "call_started", "session": session.session_id, "customer": customer,
                     "extractor": extractor_name})
    if ws.query_params.get("outbound") == "1":  # the shop calls the customer before they run out
        session._events = []
        greet = session.start_outbound(memory_for(ip, customer).due(horizon_days=7))
        for e in session._events:
            await broadcast(e)
        await ws.send_json({"type": "cart", "cart": session.cart_snapshot(), "total": session.total,
                            "pending": None, "register": session.register, "source": "memory",
                            "suggestions": suggestions(session), "payment": None,
                            "awaiting_confirm": session.awaiting_confirm})
    else:
        greet = session.greet()
    await speak(ws, greet, {})
    await ws.send_json({"type": "suggest", "items": suggestions(session)})
    turn = 0
    _active_calls += 1
    started = time.time()
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            turn += 1
            if PUBLIC_DEMO and (turn > LIMITS["turns_per_call"] or time.time() - started > LIMITS["call_seconds"]):
                await ws.send_json({"type": "error", "text": "Demo call limit reached. Start a new call to try again."})
                break
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
                if not PUBLIC_DEMO:  # the public demo stores no caller audio
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
                                "source": result.frame.source if result.frame else None,
                                "suggestions": suggestions(session), "payment": session.payment,
                                "awaiting_confirm": session.awaiting_confirm})
            if result.reply:
                await speak(ws, result.reply, timings, speed=0.85 if session.slow else 1.0)
            timings["turn_ms"] = round((time.perf_counter() - t_turn) * 1000)
            if "tts_first_ms" in timings:  # what the caller feels: end of speech -> first audio
                timings["to_first_audio_ms"] = (timings.get("stt_ms", 0) + timings.get("brain_ms", 0)
                                                + timings["tts_first_ms"])
            if not PUBLIC_DEMO:  # ...and no transcripts
                with trace_path.open("a") as f:
                    f.write(json.dumps({"turn": turn, "stt": stt_info, "timings": timings, **result.trace},
                                       ensure_ascii=False) + "\n")
            await ws.send_json({"type": "timings", **timings})
            if session.order or session.handed_off:
                await ws.send_json({"type": "end", "order": session.order, "handoff": session.handed_off})
    except WebSocketDisconnect:
        pass
    finally:
        _active_calls -= 1
        await broadcast({"type": "call_ended", "session": session.session_id})


# ---------------------------------------------------------------- suggested replies (tap instead of speaking)
STARTERS = {
    "hinglish": ["Do kilo atta aur ek litre Amul doodh", "Aashirvaad atta kitne ka hai?", "Pichli baar wala saaman bhej do"],
    "haryanvi": ["Ram Ram! do kilo cheeni aur ek darjan ande", "bees rupaye ka dhaniya", "mhara kitna baaki se?"],
    "hindi": ["दो किलो आशीर्वाद आटा भेज दीजिए", "पाव किलो जीरा और ढाई सौ ग्राम हल्दी", "अमूल गोल्ड है क्या?"],
}


def suggestions(s: Session) -> list[str]:
    from app.brain.dm import short_names
    if s.order or s.handed_off:
        return []
    reg = s.register if s.register in STARTERS else "hinglish"
    p = s.pending
    if p is not None:
        if p.kind == "brand_choice":
            return [f"{n} wala" for n in short_names(p.candidates)][:3]
        if p.kind == "size_choice":
            return ["chhota wala", "bada wala"]
        if p.kind == "substitute_offer":
            return ["haan chalega", "nahi, rehne do"]
        if p.kind in ("qty", "set_qty", "unit"):
            u = p.candidates[0].unit if p.candidates else "kg"
            return {"kg": ["ek kilo", "aadha kilo", "do kilo"], "l": ["ek litre", "aadha litre", "do litre"]}.get(
                u, ["ek packet", "do packet", "teen packet"])
        if p.kind in ("qty_confirm", "cancel_confirm"):
            return ["haan", "nahi"]
    if s.awaiting_confirm:
        return ["haan, confirm kar do", "udhaar mein likh do", "nahi nahi, ek kilo kar do"]
    if not s.cart:
        return STARTERS[reg]
    last = s.cart[-1].product
    generic = next((g for g in last.generic if g.isascii()), last.name.split()[0].lower())
    return ["bas itna hi", f"nahi nahi, {generic} teen kilo kar do" if last.unit == "kg" else "aur ek packet Parle-G",
            "thoda dheere boliye"]


# ---------------------------------------------------------------- udhaar khata
CUSTOMERS = yaml.safe_load((ROOT / "data" / "customers.yaml").read_text())
SHOP = "Sharma General Store"


@app.get("/api/khata")
def khata():
    return [{"customer": cid, "name": c.get("name", cid), "register": c.get("register", "hinglish"),
             "limit": c.get("credit_limit", 0), "balance": LEDGER.get(cid, 0.0)} for cid, c in CUSTOMERS.items()]


@app.post("/api/remind/{cid}")
async def remind(cid: str, request: Request):
    if not allow(client_ip(request.headers, request.client), "remind", LIMITS["reminds_per_ip_hour"]):
        raise HTTPException(429, "hourly demo limit reached")
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
async def voicenote(request: Request, file: UploadFile = File(...), customer: str = Form("CUST-001")):
    if not allow(client_ip(request.headers, request.client), "voicenote", LIMITS["voicenotes_per_ip_hour"]):
        raise HTTPException(429, "hourly demo limit reached")
    raw = await file.read()
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(413, "voice note too large")
    try:
        wav = await asyncio.to_thread(_decode_any, raw)
    except subprocess.CalledProcessError:
        raise HTTPException(400, "could not decode audio")
    session = Session(customer, extractor=get_extractor(os.environ.get("EXTRACTOR", "rules")),
                      memory=memory_for(client_ip(request.headers, request.client), customer), on_learn=save_memory)
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


# ---------------------------------------------------------------- /record: consented real-speaker recordings (local only)
import hashlib as _hashlib
import re as _re

REAL_DIR = ROOT / "data" / "real"
CONSENT_TEXT = ("I agree that my voice recordings may be used to evaluate the BharatVoice project for the Gnani.ai "
                "challenge. Recordings stay on the project laptop and are not published without my separate permission.")


def _no_public():
    if PUBLIC_DEMO:
        raise HTTPException(404, "not available in the public demo")


def scenario_prompt_id(sc_id: str, text: str) -> str:
    return f"{sc_id}__{_hashlib.sha1(text.encode()).hexdigest()[:8]}"


def prompts_for(set_name: str, max_scenarios: int = 8) -> list[dict]:
    if set_name == "bakeoff":
        return yaml.safe_load((ROOT / "data" / "prompts" / "bakeoff.yaml").read_text())["prompts"]
    if set_name.startswith("scenarios:"):  # e.g. scenarios:haryanvi -> every turn/answer of that register's scenarios
        reg = set_name.split(":", 1)[1]
        out = []
        for p in sorted((ROOT / "data" / "scenarios").glob(f"{reg}-*.json"))[:max_scenarios]:
            sc = json.loads(p.read_text())
            texts = list(sc["turns"]) + [x for v in sc["answers"].values() for x in v]
            for t in dict.fromkeys(texts):
                out.append({"id": scenario_prompt_id(sc["id"], t), "register": reg, "text": t, "scenario": sc["id"]})
        return out
    raise HTTPException(400, "unknown set")


@app.get("/record")
def record_page():
    _no_public()
    return FileResponse(STATIC / "record.html")


@app.get("/api/record/prompts")
def record_prompts(set: str = "bakeoff", speaker: str = "", max_scenarios: int = 8):
    _no_public()
    spk = _re.sub(r"[^A-Za-z0-9_-]", "", speaker)[:32]
    folder = REAL_DIR / set.split(":")[0] / spk if spk else None
    done = {p.stem for p in folder.glob("*.wav")} if folder and folder.exists() else set()
    return {"consent_text": CONSENT_TEXT,
            "prompts": [{**p, "done": p["id"] in done} for p in prompts_for(set, max_scenarios)]}


@app.post("/api/record")
async def record_save(file: UploadFile = File(...), speaker: str = Form(...), prompt_id: str = Form(...),
                      set: str = Form("bakeoff"), text_said: str = Form(""),
                      reg: str = Form("", alias="register"), consent: str = Form("")):
    _no_public()
    if consent != "yes":
        raise HTTPException(400, "consent required")
    spk = _re.sub(r"[^A-Za-z0-9_-]", "", speaker)[:32]
    pid = _re.sub(r"[^A-Za-z0-9_-]", "", prompt_id)[:64]
    if not spk or not pid:
        raise HTTPException(400, "bad speaker or prompt id")
    wav = await asyncio.to_thread(_decode_any, await file.read())
    import io as _io
    import soundfile as _sf
    secs = _sf.info(_io.BytesIO(wav)).duration
    if secs < 0.4 or secs > 30:
        raise HTTPException(400, f"clip is {secs:.1f}s; keep it between 0.4 and 30 s")
    folder = REAL_DIR / set.split(":")[0] / spk
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{pid}.wav").write_bytes(wav)
    with (folder / "manifest.jsonl").open("a") as f:
        f.write(json.dumps({"prompt_id": pid, "text_said": text_said, "register": reg, "set": set,
                            "seconds": round(secs, 2), "ts": time.time()}, ensure_ascii=False) + "\n")
    consent_log = REAL_DIR / "consent.jsonl"
    seen = consent_log.read_text() if consent_log.exists() else ""
    if f'"speaker": "{spk}"' not in seen:
        with consent_log.open("a") as f:
            f.write(json.dumps({"speaker": spk, "statement": CONSENT_TEXT, "ts": time.time()}) + "\n")
    return {"ok": True, "seconds": round(secs, 2)}


# ---------------------------------------------------------------- customer memory (what the shop has learned)
def _names() -> dict:
    return {cid: c.get("name", cid) for cid, c in CUSTOMERS.items()}


@app.get("/api/memory")
def memory_all(horizon_days: float = 7.0):
    cat = __import__("app.brain.catalog", fromlist=["load_catalog"]).load_catalog()
    out = []
    for cid in CUSTOMERS:
        m = MEMORY.get(cid)
        due = [{**d, "name": cat.get(d["sku"]).name} for d in m.due(horizon_days=horizon_days) if d["sku"] in cat.by_id]
        out.append({"customer": cid, "name": _names()[cid],
                    "learned": [] if PUBLIC_DEMO else  # strangers' words are never shown on the public counter
                    [{"said": v["said"], "sku": v["sku"], "name": cat.get(v["sku"]).name, "count": v["count"]}
                     for v in m.phrases.values() if v["sku"] in cat.by_id],
                    "orders": len(m.orders), "due": due})
    return out


@app.post("/api/memory/{cid}/forget")
async def memory_forget(cid: str):
    if cid not in CUSTOMERS:
        raise HTTPException(404, "unknown customer")
    if PUBLIC_DEMO:
        raise HTTPException(403, "the public demo keeps its memory")
    MEMORY.reset(cid)
    await broadcast({"type": "memory_updated", "customer": cid, "learned": []})
    return {"ok": True}


# ---------------------------------------------------------------- voice lab (local only): pick the most human shop voice
VOICELAB = ROOT / "state" / "voicelab"


@app.get("/voices")
def voices_page():
    _no_public()
    return FileResponse(STATIC / "voices.html")


@app.get("/api/voicelab")
def voicelab_results(round: int = 1):
    _no_public()
    p = VOICELAB / ("results.json" if round == 1 else f"results{round}.json")
    return json.loads(p.read_text()) if p.exists() else []


@app.get("/voicelab/{name}")
def voicelab_audio(name: str):
    _no_public()
    p = VOICELAB / _re.sub(r"[^A-Za-z0-9_.-]", "", name)
    if not p.exists() or p.suffix != ".wav":
        raise HTTPException(404, "no such clip")
    return FileResponse(p, media_type="audio/wav")
