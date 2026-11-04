"""Single entry point for every Gnani API call: cache -> credit guard -> API -> ledger.

Nothing else in the codebase may call api.vachana.ai directly.

Guards (all raise CreditGuardError, never continue silently):
  * kill switch: a file named STOP_CREDITS in the project root blocks every call
  * daily caps: estimated credits (GNANI_DAILY_CAP) and raw call count (GNANI_DAILY_CALL_CAP), IST day
  * per-run budget: `with client.run("eval-1", budget=300): ...`
  * rate limit: at most GNANI_MAX_CALLS_PER_MIN calls in any 60 s window
  * loop detector: the same request failing more than 3 times in a row aborts
  * concurrency: Gnani allows ONE active request per account (429 "Only 1 active request is allowed"), so every
    request holds an exclusive file lock (.gnani.lock) shared by all our processes (server, eval, scripts)
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import io
import json
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import soundfile as sf
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://api.vachana.ai"
IST = ZoneInfo("Asia/Kolkata")

load_dotenv(ROOT / ".env")


class CreditGuardError(RuntimeError):
    pass


class GnaniAPIError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Gnani API {status}: {body[:500]}")
        self.status = status
        self.body = body


@dataclass
class Costs:
    stt_per_sec: float
    tts_per_char: float
    clone_per_call: float
    measured: bool

    @classmethod
    def load(cls, path: Path) -> "Costs":
        d = yaml.safe_load(path.read_text()) if path.exists() else {}
        return cls(
            stt_per_sec=float(d.get("stt_per_sec", 1.0)),
            tts_per_char=float(d.get("tts_per_char", 0.1)),
            clone_per_call=float(d.get("clone_per_call", 5.0)),
            measured=bool(d.get("measured", False)),
        )


def _sha1(*parts: bytes | str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p if isinstance(p, bytes) else p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def audio_seconds(data: bytes) -> float:
    try:
        info = sf.info(io.BytesIO(data))
        return float(info.frames) / float(info.samplerate)
    except Exception:
        return len(data) / 16000.0  # rough fallback (~128 kbps)


class GnaniClient:
    def __init__(
        self,
        api_key: str | None = None,
        root: Path = ROOT,
        cache_dir: Path | None = None,
        ledger_path: Path | None = None,
        daily_cap: float | None = None,
        daily_call_cap: int | None = None,
        max_calls_per_min: int | None = None,
        transport: httpx.BaseTransport | None = None,
        offline: bool = False,
        rate_mode: str = "throttle",  # throttle: wait for the window; raise: fail fast (tests)
    ):
        self.api_key = api_key or os.environ.get("GNANI_API_KEY", "")
        self.root = root
        self.cache_dir = cache_dir or root / "eval" / "cache"
        self.kill_switch = root / "STOP_CREDITS"
        self.costs = Costs.load(root / "app" / "credit_costs.yaml")
        self.daily_cap = daily_cap if daily_cap is not None else float(os.environ.get("GNANI_DAILY_CAP", 150))
        self.daily_call_cap = (
            daily_call_cap if daily_call_cap is not None else int(os.environ.get("GNANI_DAILY_CALL_CAP", 6000))
        )
        self.max_per_min = (
            max_calls_per_min if max_calls_per_min is not None else int(os.environ.get("GNANI_MAX_CALLS_PER_MIN", 120))
        )
        self.offline = offline or os.environ.get("GNANI_OFFLINE") == "1"  # cache-only mode
        self.rate_mode = rate_mode
        self._db = sqlite3.connect(ledger_path or root / "credits.db")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS ledger(
                ts REAL, day TEXT, api TEXT, model TEXT, units REAL, est_credits REAL,
                status INTEGER, run_id TEXT, req_key TEXT, latency_ms REAL)"""
        )
        self._db.commit()
        self._run_id: str | None = None
        self._run_budget: float | None = None
        self._fail_streak: dict[str, int] = {}
        self._http = httpx.Client(base_url=BASE_URL, timeout=60.0, transport=transport)
        self._lock_path = root / ".gnani.lock"

    # ------------------------------------------------------------------ guard
    @contextlib.contextmanager
    def run(self, run_id: str, budget: float) -> Iterator[None]:
        """Scope a batch job to a credit budget. Spending is summed from the ledger by run_id."""
        prev = (self._run_id, self._run_budget)
        self._run_id, self._run_budget = run_id, budget
        try:
            yield
        finally:
            self._run_id, self._run_budget = prev

    def _today(self) -> str:
        return datetime.now(IST).strftime("%Y-%m-%d")

    def spent_today(self) -> tuple[float, int]:
        row = self._db.execute(
            "SELECT COALESCE(SUM(est_credits),0), COUNT(*) FROM ledger WHERE day=?", (self._today(),)
        ).fetchone()
        return float(row[0]), int(row[1])

    def spent_in_run(self, run_id: str) -> float:
        row = self._db.execute("SELECT COALESCE(SUM(est_credits),0) FROM ledger WHERE run_id=?", (run_id,)).fetchone()
        return float(row[0])

    def _check(self, est: float, req_key: str) -> None:
        if self.offline:
            raise CreditGuardError("offline mode (GNANI_OFFLINE=1): cache miss, refusing to call the API")
        if self.kill_switch.exists():
            raise CreditGuardError(f"kill switch present: {self.kill_switch}")
        if not self.api_key:
            raise CreditGuardError("GNANI_API_KEY missing")
        if self._fail_streak.get(req_key, 0) >= 3:
            raise CreditGuardError(f"loop detector: request {req_key[:10]} failed 3 times in a row")
        spent, calls = self.spent_today()
        if spent + est > self.daily_cap:
            raise CreditGuardError(f"daily credit cap {self.daily_cap} would be exceeded (spent {spent:.1f}, next {est:.1f})")
        if calls + 1 > self.daily_call_cap:
            raise CreditGuardError(f"daily call cap {self.daily_call_cap} reached")
        for _ in range(120):
            n_recent = self._db.execute("SELECT COUNT(*) FROM ledger WHERE ts>?", (time.time() - 60,)).fetchone()[0]
            if n_recent < self.max_per_min:
                break
            if self.rate_mode == "raise":
                raise CreditGuardError(f"rate limit: {n_recent} calls in the last 60 s")
            time.sleep(0.5)  # throttle: spend rate stays bounded, batch jobs slow down instead of crashing
        else:
            raise CreditGuardError("rate limit: window did not free up in 60 s")
        if self._run_id is not None and self._run_budget is not None:
            if self.spent_in_run(self._run_id) + est > self._run_budget:
                raise CreditGuardError(f"run budget {self._run_budget} for '{self._run_id}' would be exceeded")

    def _log(self, api: str, model: str, units: float, est: float, status: int, req_key: str, latency_ms: float):
        self._db.execute(
            "INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?,?)",
            (time.time(), self._today(), api, model, units, est, status, self._run_id or "", req_key, latency_ms),
        )
        self._db.commit()

    @contextlib.contextmanager
    def _serial(self) -> Iterator[None]:
        with open(self._lock_path, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _post(self, api: str, model: str, units: float, est: float, req_key: str, **kw) -> httpx.Response:
        """POST with guard; 5xx: 2 retries; 429 (one-active-request limit): up to 8 patient retries."""
        headers = {"X-API-Key-ID": self.api_key, **kw.pop("headers", {})}
        url = kw.pop("url")
        last: httpx.Response | None = None
        n429 = 0
        attempt = 0
        while attempt < 3:
            self._check(est, req_key)
            t0 = time.perf_counter()
            try:
                with self._serial():
                    resp = self._http.post(url, headers=headers, **kw)
            except httpx.TransportError as e:
                self._log(api, model, units, 0.0, -1, req_key, (time.perf_counter() - t0) * 1000)
                self._fail_streak[req_key] = self._fail_streak.get(req_key, 0) + 1
                if attempt == 2:
                    raise GnaniAPIError(-1, str(e)) from e
                time.sleep((2**attempt) + random.random())
                attempt += 1
                continue
            latency = (time.perf_counter() - t0) * 1000
            ok = resp.status_code == 200
            # Unknown whether failures are billed; log est only for successes, but always count the call.
            self._log(api, model, units, est if ok else 0.0, resp.status_code, req_key, latency)
            if ok:
                self._fail_streak.pop(req_key, None)
                return resp
            self._fail_streak[req_key] = self._fail_streak.get(req_key, 0) + 1
            last = resp
            if resp.status_code == 402:
                raise CreditGuardError("Gnani says insufficient credit (402)")
            if resp.status_code == 429 and n429 < 8:  # someone else (e.g. the dashboard) holds the one slot
                n429 += 1
                self._fail_streak[req_key] = 0
                time.sleep(0.5 * n429 + random.random() * 0.5)
                continue
            if resp.status_code >= 500 and attempt < 2:
                time.sleep((2**attempt) + random.random())
                attempt += 1
                continue
            break
        assert last is not None
        raise GnaniAPIError(last.status_code, last.text)

    # ------------------------------------------------------------------ cache
    def _cache_get(self, kind: str, key: str, ext: str) -> bytes | None:
        p = self.cache_dir / kind / f"{key}.{ext}"
        return p.read_bytes() if p.exists() else None

    def _cache_put(self, kind: str, key: str, ext: str, data: bytes) -> None:
        p = self.cache_dir / kind / f"{key}.{ext}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    # ------------------------------------------------------------------ STT
    def stt(
        self,
        audio: bytes,
        filename: str = "audio.wav",
        *,
        model: str = "prisma-v3.0",
        language_code: str = "hi-IN,en-IN",
        format: str | None = None,
        keyterms: list[str] | None = None,
        topic: str | None = None,
        previous_text: str | None = None,
        substitution_map: list[dict] | None = None,
        bias_list: list[str] | None = None,
        bias_score: float | None = None,
    ) -> dict[str, Any]:
        """Prisma REST (POST /stt/v3). Audio must be <= 30 s (we cap at 20 s upstream)."""
        form: dict[str, str] = {"language_code": language_code}
        if model != "prisma-v2.5":
            form["model"] = model
        if format:
            form["format"] = format
        if keyterms:
            form["keyterms"] = json.dumps(keyterms[:200], ensure_ascii=False)
        if topic:
            form["topic"] = topic[:1600]
        if previous_text:
            form["previous_text"] = previous_text[-600:]
        if substitution_map:
            form["enable_substitution"] = "true"
            form["substitution_map"] = json.dumps(substitution_map[:10], ensure_ascii=False)
        if bias_list and bias_score:
            form["bias_list"] = json.dumps(bias_list[:100], ensure_ascii=False)
            form["bias_score"] = str(bias_score)
        ext = filename.rsplit(".", 1)[-1].lower()
        key = _sha1("stt", audio, ext, json.dumps(form, sort_keys=True, ensure_ascii=False))
        cached = self._cache_get("stt", key, "json")
        if cached is not None:
            out = json.loads(cached)
            out["_cached"] = True
            return out
        secs = audio_seconds(audio)
        if secs > 30.0:
            raise ValueError(f"audio is {secs:.1f}s; REST limit is 30 s")
        est = secs * self.costs.stt_per_sec
        resp = self._post(
            "stt", model, secs, est, key, url="/stt/v3", data=form, files={"audio_file": (filename, audio)}
        )
        out = resp.json()
        self._cache_put("stt", key, "json", json.dumps(out, ensure_ascii=False).encode())
        out["_cached"] = False
        return out

    # ------------------------------------------------------------------ TTS
    def _tts_body(self, text, voice, model, language, speed, sample_rate, speaker_embedding, container="wav"):
        body: dict[str, Any] = {
            "text": text, "language": language, "speed": speed,
            "audio_config": {"sample_rate": sample_rate, "num_channels": 1, "sample_width": 2,
                             "encoding": "linear_pcm", "container": container},
        }
        if speaker_embedding is not None:
            body["model"] = "vachana-vc-v1"
            body["speaker_embedding"] = speaker_embedding
            emb_id = _sha1(json.dumps(speaker_embedding, sort_keys=True))
        else:
            body["model"] = model
            body["voice"] = voice
            emb_id = ""
        keybody = {k: v for k, v in body.items() if k != "speaker_embedding"}
        keybody["audio_config"] = {**body["audio_config"], "container": "wav"}  # REST and SSE share the cache
        key = _sha1("tts", json.dumps(keybody, sort_keys=True, ensure_ascii=False), emb_id)
        return body, key

    def tts(
        self,
        text: str,
        voice: str,
        *,
        model: str = "timbre-v2.5",
        language: str = "hi-IN",
        speed: float = 1.0,
        sample_rate: int = 16000,
        speaker_embedding: dict | None = None,
    ) -> bytes:
        """Timbre REST (POST /api/v1/tts/inference). Returns WAV bytes. Cached forever."""
        body, key = self._tts_body(text, voice, model, language, speed, sample_rate, speaker_embedding)
        cached = self._cache_get("tts", key, "wav")
        if cached is not None:
            return cached
        est = len(text) * self.costs.tts_per_char
        resp = self._post("tts", body["model"], len(text), est, key, url="/api/v1/tts/inference", json=body)
        self._cache_put("tts", key, "wav", resp.content)
        return resp.content

    def tts_stream(
        self,
        text: str,
        voice: str,
        *,
        model: str = "timbre-v2.5",
        language: str = "hi-IN",
        speed: float = 1.0,
        sample_rate: int = 16000,
        speaker_embedding: dict | None = None,
    ) -> Iterator[bytes]:
        """Timbre SSE (POST /api/v1/tts/sse): yields raw PCM16 mono chunks as they are synthesised.
        Cache hit -> the whole clip at once. The assembled clip is cached as WAV (shared with tts())."""
        body, key = self._tts_body(text, voice, model, language, speed, sample_rate, speaker_embedding,
                                   container="raw")
        cached = self._cache_get("tts", key, "wav")
        if cached is not None:
            data, _ = sf.read(io.BytesIO(cached), dtype="int16")
            yield data.tobytes()
            return
        est = len(text) * self.costs.tts_per_char
        self._check(est, key)
        headers = {"X-API-Key-ID": self.api_key, "Accept": "text/event-stream"}
        pcm = bytearray()
        t0 = time.perf_counter()
        status = -1
        for n429 in range(9):
            with self._serial():
                with self._http.stream("POST", "/api/v1/tts/sse", json=body, headers=headers) as resp:
                    status = resp.status_code
                    if status == 429:
                        resp.read()
                    elif status != 200:
                        body_txt = resp.read().decode("utf-8", "ignore")
                        self._log("tts", body["model"], len(text), 0.0, status, key, (time.perf_counter() - t0) * 1000)
                        raise GnaniAPIError(status, body_txt)
                    else:
                        event = ""
                        for line in resp.iter_lines():
                            if line.startswith("event:"):
                                event = line[6:].strip()
                            elif line.startswith("data:") and event == "chunk":
                                d = json.loads(line[5:].strip())
                                if d.get("audio"):
                                    chunk = base64.b64decode(d["audio"])
                                    pcm.extend(chunk)
                                    yield chunk
                            elif line.startswith("data:") and event == "error":
                                raise GnaniAPIError(500, line[5:])
                        break
            time.sleep(0.5 * (n429 + 1))
        self._log("tts", body["model"], len(text), est if status == 200 else 0.0, status, key,
                  (time.perf_counter() - t0) * 1000)
        if status == 200 and pcm:
            buf = io.BytesIO()
            sf.write(buf, np.frombuffer(bytes(pcm), dtype=np.int16), sample_rate, format="WAV", subtype="PCM_16")
            self._cache_put("tts", key, "wav", buf.getvalue())

    # ------------------------------------------------------------------ voice clone
    def clone_embedding(self, audio: bytes, filename: str = "ref.wav") -> dict:
        """POST /api/v1/tts/voice-clone/embeddings -> speaker_embedding dict usable in tts()."""
        key = _sha1("clone", audio)
        cached = self._cache_get("clone", key, "json")
        if cached is not None:
            return json.loads(cached)
        secs = audio_seconds(audio)
        if not 5.0 <= secs <= 30.0:
            raise ValueError(f"reference audio is {secs:.1f}s; needs 5-30 s")
        resp = self._post("clone", "vachana-vc-v1", 1, self.costs.clone_per_call, key,
                          url="/api/v1/tts/voice-clone/embeddings", files={"audio_file": (filename, audio)})
        emb = resp.json()["data"]["voice_clone_embedding"]
        self._cache_put("clone", key, "json", json.dumps(emb).encode())
        return emb

    # ------------------------------------------------------------------ report
    def report(self) -> str:
        spent, calls = self.spent_today()
        rows = self._db.execute(
            "SELECT api, COUNT(*), SUM(units), SUM(est_credits), AVG(latency_ms) FROM ledger WHERE day=? GROUP BY api",
            (self._today(),),
        ).fetchall()
        lines = [f"today {self._today()}: {calls} calls, ~{spent:.1f} est. credits "
                 f"(cap {self.daily_cap}, call cap {self.daily_call_cap}, costs measured={self.costs.measured})"]
        for api, n, units, est, lat in rows:
            lines.append(f"  {api:6s} calls={n:4d} units={units or 0:8.1f} est={est or 0:7.1f} avg_latency={lat or 0:6.0f}ms")
        return "\n".join(lines)


_client: GnaniClient | None = None


def get_client() -> GnaniClient:
    global _client
    if _client is None:
        _client = GnaniClient()
    return _client


if __name__ == "__main__":
    print(get_client().report())
