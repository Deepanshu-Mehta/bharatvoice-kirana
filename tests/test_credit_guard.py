"""Credit guard tests: use a mock transport, never touch the real API."""
import io
import json

import httpx
import numpy as np
import pytest
import soundfile as sf

from app.gnani_client import CreditGuardError, GnaniAPIError, GnaniClient


def wav(seconds: float = 2.0, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(seconds * sr), dtype=np.int16), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class Counter:
    def __init__(self, status=200, body=None):
        self.n = 0
        self.status = status
        self.body = body or {"success": True, "transcript": "do kilo atta"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.n += 1
        if request.url.path.endswith("/inference"):
            return httpx.Response(self.status, content=wav(0.5))
        return httpx.Response(self.status, json=self.body)


def make(tmp_path, handler, **kw):
    (tmp_path / "app").mkdir(exist_ok=True)
    return GnaniClient(api_key="test", root=tmp_path, cache_dir=tmp_path / "cache",
                       ledger_path=tmp_path / "ledger.db", transport=httpx.MockTransport(handler), **kw)


def test_cache_hit_makes_no_second_call(tmp_path):
    h = Counter()
    c = make(tmp_path, h)
    a = c.stt(wav(), keyterms=["Aashirvaad"])
    b = c.stt(wav(), keyterms=["Aashirvaad"])
    assert h.n == 1 and a["_cached"] is False and b["_cached"] is True
    c.stt(wav(), keyterms=["Pillsbury"])  # different context -> different key
    assert h.n == 2


def test_daily_credit_cap(tmp_path):
    h = Counter()
    c = make(tmp_path, h, daily_cap=5.0)  # placeholder cost 1 credit/s -> 2 s clip = 2 credits
    c.stt(wav(2.0), topic="a")
    c.stt(wav(2.0), topic="b")
    with pytest.raises(CreditGuardError, match="daily credit cap"):
        c.stt(wav(2.0), topic="c")
    assert h.n == 2


def test_daily_call_cap(tmp_path):
    c = make(tmp_path, Counter(), daily_call_cap=2)
    c.tts("ek", "Poorvi")
    c.tts("do", "Poorvi")
    with pytest.raises(CreditGuardError, match="call cap"):
        c.tts("teen", "Poorvi")


def test_kill_switch(tmp_path):
    h = Counter()
    c = make(tmp_path, h)
    (tmp_path / "STOP_CREDITS").touch()
    with pytest.raises(CreditGuardError, match="kill switch"):
        c.tts("namaste", "Poorvi")
    assert h.n == 0


def test_run_budget(tmp_path):
    c = make(tmp_path, Counter())
    with c.run("eval-x", budget=3.0):
        c.stt(wav(2.0), topic="1")
        with pytest.raises(CreditGuardError, match="run budget"):
            c.stt(wav(2.0), topic="2")
    c.stt(wav(2.0), topic="2")  # outside the run, only the daily cap applies


def test_rate_limit(tmp_path):
    c = make(tmp_path, Counter(), max_calls_per_min=3, rate_mode="raise")
    for i in range(3):
        c.tts(f"t{i}", "Poorvi")
    with pytest.raises(CreditGuardError, match="rate limit"):
        c.tts("t4", "Poorvi")


def test_loop_detector_and_no_silent_continue(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    h = Counter(status=500, body={"error": "boom"})
    c = make(tmp_path, h)
    with pytest.raises(GnaniAPIError):
        c.tts("x", "Poorvi")  # 3 attempts
    with pytest.raises(CreditGuardError, match="loop detector"):
        c.tts("x", "Poorvi")
    assert h.n == 3


def test_402_stops_immediately(tmp_path):
    h = Counter(status=402, body={"error": "Insufficient credit"})
    c = make(tmp_path, h)
    with pytest.raises(CreditGuardError, match="402"):
        c.stt(wav())
    assert h.n == 1


def test_offline_mode_serves_cache_only(tmp_path):
    h = Counter()
    c = make(tmp_path, h)
    c.stt(wav(), topic="cached")
    off = make(tmp_path, h, offline=True)
    assert off.stt(wav(), topic="cached")["_cached"] is True
    with pytest.raises(CreditGuardError, match="offline"):
        off.stt(wav(), topic="new")


def test_rejects_long_audio(tmp_path):
    c = make(tmp_path, Counter())
    with pytest.raises(ValueError, match="30 s"):
        c.stt(wav(31.0))


def test_form_fields(tmp_path):
    seen = {}

    def handler(request):
        seen["body"] = request.content.decode("utf-8", "ignore")
        return httpx.Response(200, json={"success": True, "transcript": ""})

    c = make(tmp_path, handler)
    c.stt(wav(), keyterms=["आशीर्वाद", "Good Day"], previous_text="x" * 700,
          substitution_map=[{"patterns": ["ashirwad"], "replacement": "Aashirvaad"}])
    body = seen["body"]
    assert "enable_substitution" in body and "true" in body
    assert json.dumps(["आशीर्वाद", "Good Day"], ensure_ascii=False) in body
    assert "x" * 601 not in body  # previous_text trimmed to 600


def test_429_one_active_request_is_retried_patiently(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] <= 4:
            return httpx.Response(429, json={"detail": {"error_code": "RATE_LIMITED"}})
        return httpx.Response(200, json={"success": True, "transcript": "ok"})

    c = make(tmp_path, handler)
    assert c.stt(wav())["transcript"] == "ok" and n["i"] == 5
