"""Hybrid extractor: rules first (fast, exact on common phrasing), Evon for turns the rules cannot parse.

Escalate to Evon when the rules frame
  * has no actions for a non-trivial utterance, or
  * has an 'add' whose product phrase resolves to nothing, or is a long description that resolves only weakly, or
  * the utterance is long (>= 9 words) but yielded a single action (likely missed intents).
Every decision is recorded on the frame (source = rules | evon) so the eval reports the escalation rate.
"""
from __future__ import annotations

from app.brain.extract_rules import Context
from app.brain.extract_rules import extract as rules_extract
from app.brain.frame import Frame
from app.brain.resolver import Resolver
from app.brain.text import to_roman

_resolver: Resolver | None = None


def _unresolvable(phrase: str | None) -> bool:
    """No catalog match, or a long descriptive phrase that matches only weakly ('woh meethi safed cheez jo chai
    mein daalte hain' weakly hits tea via 'chai')."""
    global _resolver
    if not phrase:
        return False
    _resolver = _resolver or Resolver()
    res = _resolver.resolve(phrase, k=3)
    if not res.candidates:
        return True
    return len(phrase.split()) >= 4 and res.p_correct < 0.40


def needs_llm(transcript: str, f: Frame, ctx: Context) -> bool:
    words = to_roman(transcript).split()
    if len(words) < 2:
        return False
    if not f.actions:
        return True
    if ctx.pending:  # answers to our own questions are handled deterministically
        return False
    adds = [a for a in f.actions if a.type == "add"]
    if adds and any(_unresolvable(a.product_phrase) for a in adds):
        return True
    return len(words) >= 9 and len(f.actions) == 1


class HybridExtractor:
    def __init__(self, evon=None):
        if evon is None:
            from app.brain.extract_evon import EvonExtractor
            evon = EvonExtractor()
        self.evon = evon
        self.stats = {"turns": 0, "escalated": 0}

    def __call__(self, transcript: str, ctx: Context | None = None) -> Frame:
        ctx = ctx or Context()
        self.stats["turns"] += 1
        f = rules_extract(transcript, ctx)
        if needs_llm(transcript, f, ctx):
            self.stats["escalated"] += 1
            g = self.evon(transcript, ctx)
            if g.actions:
                return g
        return f
