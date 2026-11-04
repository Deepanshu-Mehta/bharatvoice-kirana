"""Evon extractor behaviour against a mocked vLLM server (no GPU needed)."""
import json

import httpx

from app.brain.extract_evon import EvonExtractor, build_messages
from app.brain.extract_rules import Context

GOOD = {"register": "hinglish", "actions": [{"type": "add", "product_phrase": "Aashirvaad atta", "qty_text": "do kilo"}]}


def server(message: dict, status: int = 200):
    calls = []

    def handler(req: httpx.Request):
        calls.append(json.loads(req.content))
        return httpx.Response(status, json={"choices": [{"message": message}]})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_json_in_content():
    c, calls = server({"content": json.dumps(GOOD)})
    f = EvonExtractor(client=c)("do kilo aashirvaad atta")
    assert f.source == "evon" and f.actions[0].product_phrase == "Aashirvaad atta"
    body = calls[0]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["response_format"]["type"] == "json_schema"


def test_json_swapped_into_reasoning_field():
    c, _ = server({"content": "", "reasoning_content": json.dumps(GOOD)})
    assert EvonExtractor(client=c)("do kilo atta").source == "evon"


def test_think_block_stripped():
    c, _ = server({"content": "<think>hmm</think>" + json.dumps(GOOD)})
    assert EvonExtractor(client=c)("x").source == "evon"


def test_invalid_json_retries_then_falls_back_to_rules():
    c, calls = server({"content": '{"register": "hinglish", "actions": [{"type": "explode"}]}'})
    ex = EvonExtractor(client=c)
    f = ex("do kilo aashirvaad atta")
    assert len(calls) == 2 and f.source == "rules" and ex.stats["fallbacks"] == 1
    assert f.actions[0].type == "add"


def test_server_down_falls_back():
    c, _ = server({}, status=503)
    assert EvonExtractor(client=c)("ek kilo cheeni").source == "rules"


def test_haryanvi_markers_override_register():
    c, _ = server({"content": json.dumps({**GOOD, "register": "hindi"})})
    assert EvonExtractor(client=c)("Ram Ram, mhara order likh lo").speech_register == "haryanvi"


def test_prompt_contains_context():
    msgs = build_messages("Pillsbury wala", Context(pending="brand_choice", last_agent_prompt="Aashirvaad ya Pillsbury?",
                                                    cart_phrases=["do kilo cheeni"]))
    last = msgs[-1]["content"]
    assert "brand_choice" in last and "Aashirvaad ya Pillsbury?" in last and "do kilo cheeni" in last
