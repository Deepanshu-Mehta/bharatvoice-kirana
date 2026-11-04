"""Evon v3.3 extractor: transcript -> Frame via one schema-guided JSON call (thinking OFF).

Served by vLLM (OpenAI-compatible) WITHOUT --reasoning-parser (see infra/serve_evon.sh), so the JSON lands in
`content`. We still read `reasoning_content` defensively and strip any <think> block.
On timeout / invalid JSON: retry once with the validation error, then fall back to the rules extractor.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from app.brain.catalog import load_catalog
from app.brain.extract_rules import Context, detect_register
from app.brain.extract_rules import extract as rules_extract
from app.brain.frame import Frame, StrictFrame, frame_json_schema

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
EVON_URL = os.environ.get("EVON_URL", "http://localhost:8000/v1")
EVON_MODEL = os.environ.get("EVON_MODEL", "gnani/gnani-evon-v3.3-30B-A3B")
TIMEOUT_S = float(os.environ.get("EVON_TIMEOUT_S", "4.0"))
EVON_API_KEY = os.environ.get("EVON_API_KEY", "")

FEW_SHOTS = [
    ("Bhaiya do kilo Aashirvaad atta aur ek litre Amul doodh bhej dena", {}, {
        "register": "hinglish", "actions": [
            {"type": "add", "product_phrase": "Aashirvaad atta", "qty_text": "do kilo"},
            {"type": "add", "product_phrase": "Amul doodh", "qty_text": "ek litre"}]}),
    ("नहीं नहीं, दूध 2 litre कर दो", {"cart": ["ek litre Amul Taaza Milk"]}, {
        "register": "hinglish", "actions": [{"type": "update", "target": "दूध", "qty_text": "2 litre"}]}),
    ("atta hata do, uski jagah ek kilo cheeni de do", {"cart": ["do kilo Aashirvaad Atta"]}, {
        "register": "hinglish", "actions": [
            {"type": "replace", "target": "atta", "product_phrase": "cheeni", "qty_text": "ek kilo"}]}),
    ("Pillsbury wala", {"pending": "brand_choice", "agent": "Aashirvaad wala ya Pillsbury wala?"}, {
        "register": "hinglish", "actions": [{"type": "choose", "choice_text": "Pillsbury wala"}]}),
    ("नहीं, तीन किलो", {"cart": ["do kilo Aashirvaad Atta"]}, {
        "register": "hindi", "actions": [{"type": "update", "target": "LAST", "qty_text": "तीन किलो"}]}),
    ("bas itna hi", {}, {"register": "hinglish", "actions": [{"type": "done"}]}),
    ("haan confirm kar do", {"awaiting_confirm": True}, {"register": "hinglish", "actions": [{"type": "confirm_yes"}]}),
    # Haryanvi (DRAFT phrasing; replace with native-reviewed examples)
    ("Ram Ram! do kilo atta chahiye, aur ek packet Parle-G", {}, {
        "register": "haryanvi", "actions": [
            {"type": "add", "product_phrase": "atta", "qty_text": "do kilo"},
            {"type": "add", "product_phrase": "Parle-G", "qty_text": "ek packet"}]}),
    ("atta konya chahiye", {"cart": ["do kilo Aashirvaad Atta"]}, {
        "register": "haryanvi", "actions": [{"type": "remove", "target": "atta"}]}),
    ("bhaiya se baat karao", {}, {"register": "hinglish", "actions": [{"type": "ask_human"}]}),
    ("ek packet woh namkeen jo aloo se banta hai, Haldiram wala", {}, {
        "register": "hinglish", "actions": [{"type": "add", "product_phrase": "Haldiram aloo bhujia", "qty_text": "ek packet"}]}),
]


def _system_prompt() -> str:
    cats = sorted({p.cat for p in load_catalog().products})
    return (
        "You are the language-understanding module of a kirana (grocery) phone-order agent in India. "
        "Callers speak Hindi, Hinglish (code-mixed) or Haryanvi. Transcripts may contain speech-recognition errors "
        "and mixed Devanagari/Latin script.\n"
        "Return ONLY a JSON object matching the schema. Do NOT compute prices, totals or stock. Copy the caller's words "
        "into product_phrase and qty_text exactly as spoken (keep 'doodh', do not translate it). Exception: if the "
        "caller DESCRIBES an item without naming it (e.g. 'woh meethi safed cheez jo chai mein daalte hain'), put the "
        "everyday product word in product_phrase (e.g. 'cheeni'); if they also say a brand, use the brand.\n"
        "Rules:\n"
        "- add: a new item. update: change quantity of an item already in the cart (target = its words, or LAST if "
        "unnamed). remove: drop an item (hata do, rehne do, nahi chahiye, Haryanvi 'konya chahiye'). replace: 'X ki "
        "jagah Y'.\n"
        "- If the agent just asked a question (pending), answer with choose(choice_text = caller's words).\n"
        "- done: caller has finished (bas, itna hi). confirm_yes / confirm_no only when the agent asked to confirm.\n"
        "- ask_human: caller wants a person. store_question: timings, delivery, payment. cancel_all: cancel everything.\n"
        "- repeat_last_order: 'pichli baar wala', 'wahi saaman'.\n"
        "- register: hindi (formal, no English), hinglish (code-mixed), haryanvi (dialect markers such as konya, mhara, "
        "thara, Ram Ram).\n"
        f"Catalog categories: {', '.join(cats)}."
    )


def _user_msg(transcript: str, ctx: dict) -> str:
    parts = []
    if ctx.get("agent"):
        parts.append(f"Agent just said: {ctx['agent']}")
    if ctx.get("pending"):
        parts.append(f"Pending question type: {ctx['pending']}")
    if ctx.get("awaiting_confirm"):
        parts.append("Agent is waiting for order confirmation.")
    if ctx.get("cart"):
        parts.append("Cart: " + "; ".join(ctx["cart"]))
    parts.append(f"Caller: {transcript}")
    return "\n".join(parts)


def build_messages(transcript: str, ctx: Context) -> list[dict]:
    msgs = [{"role": "system", "content": _system_prompt()}]
    for t, c, out in FEW_SHOTS:
        msgs.append({"role": "user", "content": _user_msg(t, c)})
        msgs.append({"role": "assistant", "content": json.dumps(out, ensure_ascii=False)})
    msgs.append({"role": "user", "content": _user_msg(transcript, {
        "agent": ctx.last_agent_prompt, "pending": ctx.pending, "awaiting_confirm": ctx.awaiting_confirm,
        "cart": ctx.cart_phrases})})
    return msgs


_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def _content(resp: dict) -> str:
    msg = resp["choices"][0]["message"]
    text = msg.get("content") or ""
    if not text.strip():  # vLLM's Nemotron parser can swap fields when thinking is off
        text = msg.get("reasoning_content") or msg.get("reasoning") or ""
    text = _THINK_RE.sub("", text).strip()
    m = re.search(r"\{.*\}", text, re.S)
    return m.group(0) if m else text


class EvonExtractor:
    def __init__(self, url: str = EVON_URL, model: str = EVON_MODEL, timeout: float = TIMEOUT_S,
                 client: httpx.Client | None = None):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        headers = {"Authorization": f"Bearer {EVON_API_KEY}"} if EVON_API_KEY else {}
        self.http = client or httpx.Client(timeout=timeout, headers=headers)
        self.stats = {"calls": 0, "fallbacks": 0, "retries": 0, "latency_ms": []}

    def _call(self, messages: list[dict]) -> tuple[str, float]:
        body = {
            "model": self.model, "messages": messages, "temperature": 0.0, "max_tokens": 300,
            "repetition_penalty": 1.1,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "turn_frame", "schema": frame_json_schema()}},
        }
        t0 = time.perf_counter()
        r = self.http.post(f"{self.url}/chat/completions", json=body, timeout=self.timeout)
        r.raise_for_status()
        return _content(r.json()), (time.perf_counter() - t0) * 1000

    def __call__(self, transcript: str, ctx: Context | None = None) -> Frame:
        ctx = ctx or Context()
        self.stats["calls"] += 1
        messages = build_messages(transcript, ctx)
        for attempt in range(2):
            try:
                raw, ms = self._call(messages)
                self.stats["latency_ms"].append(ms)
                f = StrictFrame.model_validate_json(raw).to_frame("evon")
                if f.speech_register != "haryanvi" and detect_register(transcript) == "haryanvi":
                    f.speech_register = "haryanvi"  # lexicon markers win (Evon has no Haryanvi training)
                return f
            except ValidationError as e:
                self.stats["retries"] += 1
                messages = messages + [{"role": "assistant", "content": raw},
                                       {"role": "user", "content": f"Invalid JSON for the schema: {e.errors()[:3]}. "
                                                                   "Return only the corrected JSON."}]
            except (httpx.HTTPError, KeyError, json.JSONDecodeError):
                break
        self.stats["fallbacks"] += 1
        return rules_extract(transcript, ctx)
