"""Turn frame: the single structured output of the extractor (Evon or rules) for one customer turn.

The LLM proposes this frame; deterministic code (dm.py) decides what actually happens.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

ActionType = Literal[
    "add", "update", "remove", "replace", "choose", "done",
    "confirm_yes", "confirm_no", "cancel_all", "repeat_last_order", "ask_human", "store_question",
    "price_query", "stock_query", "udhaar", "balance_query", "slow_down", "repeat",
]
Register = Literal["hindi", "hinglish", "haryanvi", "en"]


class Action(BaseModel):
    type: ActionType
    product_phrase: str | None = Field(None, description="raw product words as spoken, e.g. 'aashirvaad atta'")
    qty_text: str | None = Field(None, description="raw quantity words as spoken, e.g. 'paanch kilo'")
    target: str | None = Field(None, description="cart item being corrected, or 'LAST'")
    choice_text: str | None = Field(None, description="answer to the agent's question, e.g. 'pillsbury wala'")
    text: str | None = Field(None, description="free text for store_question")


class Frame(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)
    speech_register: Register = Field("hinglish", alias="register")
    actions: list[Action] = Field(default_factory=list)
    source: Literal["evon", "rules"] = "rules"


# ---- strict per-action schema for the LLM (required fields per type); converted to the flat Frame afterwards
class _Add(BaseModel):
    type: Literal["add"]
    product_phrase: str
    qty_text: str | None = None


class _Update(BaseModel):
    type: Literal["update"]
    target: str
    qty_text: str


class _Remove(BaseModel):
    type: Literal["remove"]
    target: str


class _Replace(BaseModel):
    type: Literal["replace"]
    target: str
    product_phrase: str
    qty_text: str | None = None


class _Choose(BaseModel):
    type: Literal["choose"]
    choice_text: str


class _Query(BaseModel):
    type: Literal["price_query", "stock_query"]
    product_phrase: str


class _StoreQ(BaseModel):
    type: Literal["store_question"]
    text: str


class _Simple(BaseModel):
    type: Literal["done", "confirm_yes", "confirm_no", "cancel_all", "repeat_last_order", "ask_human",
                  "udhaar", "balance_query", "slow_down", "repeat"]


StrictAction = Annotated[Union[_Add, _Update, _Remove, _Replace, _Choose, _Query, _StoreQ, _Simple],
                         Field(discriminator="type")]


class StrictFrame(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    speech_register: Register = Field(alias="register")
    actions: list[StrictAction]

    def to_frame(self, source: str = "evon") -> "Frame":
        return Frame(speech_register=self.speech_register, source=source,
                     actions=[Action(**a.model_dump()) for a in self.actions])


def _anyof(node):
    """pydantic emits oneOf+discriminator; grammar backends handle plain anyOf most reliably."""
    if isinstance(node, dict):
        node.pop("discriminator", None)
        if "oneOf" in node:
            node["anyOf"] = node.pop("oneOf")
        for v in node.values():
            _anyof(v)
    elif isinstance(node, list):
        for v in node:
            _anyof(v)
    return node


def frame_json_schema() -> dict:
    """Strict JSON schema handed to vLLM structured outputs."""
    return _anyof(StrictFrame.model_json_schema(by_alias=True))
