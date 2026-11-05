"""Dialogue manager: deterministic state machine around the cart.

The extractor (Evon or rules) PROPOSES a Frame; this module DECIDES. Hard rules enforced here:
  * an order is placed only after a spoken readback AND an explicit confirm_yes
  * every price / total comes from the catalog via this module, never from the LLM
  * order placement is idempotent (same cart -> same order id)
"""
from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from app.brain.catalog import Catalog, Product, load_catalog
from app.brain.extract_rules import NO, YES, Context, extract
from app.brain.frame import Action, Frame
from app.brain.qty import Qty, parse_qty
from app.brain.resolver import Resolver, Resolution
from app.brain.templates import render, speak_money, speak_qty
from app.brain.text import key

ROOT = Path(__file__).resolve().parents[2]
UNIT_SPOKEN = {"kg": "kilo", "l": "litre", "pkt": "packet", "pc": "piece"}


def _load_ledger() -> dict[str, float]:
    d = yaml.safe_load((ROOT / "data" / "customers.yaml").read_text())
    return {k: float(v.get("balance", 0)) for k, v in d.items()}


LEDGER: dict[str, float] = _load_ledger()  # udhaar khata (synthetic), shared across calls in one server process
import os
PUBLIC_DEMO = os.environ.get("PUBLIC_DEMO") == "1"  # never speak a stranger's words back in the public demo
TAU_HIGH = 0.85  # hand-set; replaced by risk-coverage thresholds after calibration
TAU_LOW = 0.40
HIGH_VALUE_HANDOFF = 5000.0
# Plausibility: quantities above these (base units) are confirmed before adding. Catches STT number errors
# such as "paav kilo haldi" heard as "paanch kilo haldi" (eval 5 Nov).
TYPICAL_MAX = {"spices": 1.0, "tea": 3, "coffee": 3, "ghee": 2, "dairy": 6, "toothpaste": 4, "shampoo": 3,
               "soap": 10, "drinks": 12, "snacks": 10, "noodles": 12, "biscuits": 12, "dishwash": 10, "household": 6,
               "detergent": 6, "salt": 5, "oil": 10, "milk": 10, "bread": 5, "eggs": 60, "besan": 5, "sooji": 5,
               "maida": 10, "dal": 10, "rice": 25, "atta": 30, "sugar": 20}
ORDINALS = {key(w): i for i, ws in enumerate([
    ["pehla", "pehle", "pahla", "pahle", "first", "upar wala", "pehle wala", "pehla wala"],
    ["doosra", "dusra", "doosre", "dusre", "second", "doosra wala", "dusra wala", "baad wala"],
    ["teesra", "tisra", "third"]]) for w in ws}
SIZE = {key(w): s for s, ws in {"large": ["bada", "badi", "bade", "large", "big", "family"],
                                 "small": ["chhota", "chhoti", "chhote", "chota", "small"]}.items() for w in ws}


def speak_name(p: Product) -> str:
    n = re.sub(r"\s+\d+(\.\d+)?\s*(g|kg|ml|l)\b.*$", "", p.name)
    n = re.sub(r"\s+\d+-pack.*$", "", n)
    if p.size:
        n += " bada" if p.size == "large" else " chhota"
    return n


def short_names(ps: list[Product]) -> list[str]:
    """Brand-only names when the options share a category but differ in brand ('Aashirvaad', 'Pillsbury')."""
    brands = [p.brand for p in ps]
    if all(brands) and len(set(brands)) == len(brands) and len({p.cat for p in ps}) == 1:
        return brands
    return [speak_name(p) for p in ps]


def _generic_word(p: Product) -> str:
    for g in p.generic:
        if g.isascii():
            return g
    return p.cat


@dataclass
class Line:
    id: int
    product: Product
    qty: float
    unit: str
    money: float | None = None  # "bees rupaye ka dhaniya": spoken as money, quantity derived from the price
    said: str | None = None  # the customer's own words for this item (learned only after the order is confirmed)
    via: str | None = None  # direct | asked | memory

    @property
    def amount(self) -> float:
        return round(self.money if self.money is not None else self.qty * self.product.price, 2)

    def spoken(self) -> str:
        if self.money is not None:
            return f"{speak_money(self.money)} ka {speak_name(self.product)}"
        return f"{speak_qty(self.qty, self.unit)} {speak_name(self.product)}"

    def as_dict(self) -> dict:
        return {"line": self.id, "sku": self.product.id, "name": self.product.name, "qty": self.qty,
                "unit": self.unit, "price": self.product.price, "amount": self.amount, "via": self.via,
                "said": self.said}


@dataclass
class Pending:
    kind: str  # brand_choice | size_choice | substitute_offer | cancel_confirm | qty | unit
    candidates: list[Product] = field(default_factory=list)
    qty: Qty | None = None
    phrase: str = ""
    question: str = ""
    attempts: int = 0
    original: Product | None = None  # for substitute_offer
    line_id: int | None = None  # for set_qty (correction whose number was lost)
    soft: bool = False  # follow-up to a price/stock question: if unanswered, just handle the turn normally
    replaced: str | None = None  # name of the line a replace removed
    said: str = ""  # the customer's original words for the item being clarified
    via: str = "asked"


@dataclass
class TurnResult:
    reply: str
    events: list[dict]
    frame: Frame | None
    trace: dict


class Session:
    def __init__(self, customer_id: str = "CUST-001", *, catalog: Catalog | None = None,
                 resolver: Resolver | None = None,
                 extractor: Callable[[str, Context], Frame] | None = None, shop: str = "Sharma General Store",
                 session_id: str | None = None, memory=None, on_learn: Callable | None = None):
        self.memory = memory  # CustomerMemory or None (memory off)
        self.on_learn = on_learn
        self.cat = catalog or load_catalog()
        self.resolver = resolver or Resolver(self.cat)
        self.extractor = extractor or extract
        self.shop = shop
        self.customer_id = customer_id
        self.session_id = session_id or f"{customer_id}-{int(time.time() * 1000)}"
        customers = yaml.safe_load((ROOT / "data" / "customers.yaml").read_text())
        self.customer = customers.get(customer_id, {"history": []})
        self.history_counts = Counter(l["sku"] for o in self.customer.get("history", []) for l in o["lines"])
        self.register = self.customer.get("register", "hinglish")
        self.stock = {p.id: p.stock for p in self.cat.products}  # per-session view; catalog stays immutable
        self.cart: list[Line] = []
        self.payment: str | None = None  # None | "udhaar"
        self.slow = False  # "dheere boliye": TTS speed 0.85
        self._next_line = 1
        self.last_line_id: int | None = None
        self.pending: Pending | None = None
        self.awaiting_confirm = False
        self.unresolved_streak = 0
        self.empty_streak = 0
        self.order: dict | None = None
        self.handed_off = False
        self.queue: list[Action] = []
        self.last_agent_prompt = ""
        self.placed_orders: dict[str, dict] = {}
        self._events: list[dict] = []
        self._trace: dict = {}

    # ------------------------------------------------------------------ helpers
    def r(self, template: str, **kw) -> str:
        return render(template, self.register, **kw)

    @property
    def total(self) -> float:
        return round(sum(l.amount for l in self.cart), 2)

    def cart_snapshot(self) -> list[dict]:
        return [l.as_dict() for l in self.cart]

    def _emit(self, kind: str, **kw):
        self._events.append({"type": kind, "session": self.session_id, **kw})

    def _line_by_id(self, lid: int | None) -> Line | None:
        return next((l for l in self.cart if l.id == lid), None)

    def _find_line(self, target: str | None) -> Line | None:
        if not self.cart:
            return None
        if not target or target.upper() == "LAST" or key(target) in {key("last wala"), key("woh"), key("wo")}:
            return self._line_by_id(self.last_line_id) or self.cart[-1]
        res = self.resolver.resolve_in_cart(target, [l.product for l in self.cart])
        self._trace.setdefault("cart_matches", []).append(self._res_trace(res))
        if res.top and (res.top.alias > 0 or res.top.category > 0):
            return next(l for l in self.cart if l.product.id == res.top.product.id)
        return None

    @staticmethod
    def _res_trace(res: Resolution) -> dict:
        return {"phrase": res.phrase, "p": round(res.p_correct, 3),
                "top": [(c.id, round(c.score, 3)) for c in res.candidates[:3]], **{k: round(v, 3) for k, v in res.features.items()}}

    def _to_base(self, p: Product, q: Qty | None) -> float | None:
        if q is None:
            return 1.0 if p.unit in ("pkt", "pc") else None
        v, u = q.normalized()
        if u == "inr":  # "bees rupaye ka dhaniya" -> quantity from the catalog price (code decides)
            if p.unit in ("kg", "l"):
                return round(v / p.price, 3)
            n = int(v // p.price)
            return float(n) if n >= 1 else None
        if p.unit in ("kg", "l"):
            if u in (p.unit, None):
                return v
            if u in ("pkt", "pc") and p.pack and p.pack[1] == p.unit:
                return v * p.pack[0]
            return None
        if u in (None, "pkt", "pc"):
            return v
        if u in ("kg", "l") and p.pack and p.pack[1] == u:
            n = v / p.pack[0]
            return round(n) if abs(n - round(n)) < 1e-6 and n >= 1 else None
        return None

    def _substitute_for(self, p: Product, qty: float) -> Product | None:
        pool = [c for c in self.cat.by_cat.get(p.cat, []) if c.id != p.id and self.stock[c.id] >= qty
                and c.unit == p.unit]
        if p.desc:
            same = [c for c in pool if set(map(key, c.desc)) & set(map(key, p.desc))]
            pool = same or pool
        if p.size:
            pool = [c for c in pool if c.size in (None, p.size)] or pool
        return max(pool, key=lambda c: c.pop, default=None)

    # ------------------------------------------------------------------ commit
    def _commit(self, p: Product, q: Qty | None, *, replaced: str | None = None, checked: bool = False,
                said: str | None = None, via: str = "direct") -> str:
        if q is None and self.memory is not None and p.unit in ("pkt", "pc"):  # "biscuit bhej dena" = the usual
            usual = self.memory.usual_qty(p.id)
            if usual and usual.get("unit") == p.unit and float(usual["qty"]) != 1.0:
                self.pending = Pending("qty_default", [p], Qty(float(usual["qty"]), None, 1.0), speak_name(p),
                                       replaced=replaced, said=said or "", via=via)
                return self._ask(self.r("usual_qty", name=speak_name(p), qty=speak_qty(float(usual["qty"]), p.unit)))
        qty = self._to_base(p, q)
        if qty is not None and not checked and qty > TYPICAL_MAX.get(p.cat, float("inf")):
            self.pending = Pending("qty_confirm", [p], q, speak_name(p), replaced=replaced, said=said or "", via=via)
            return self._ask(self.r("confirm_big_qty", item=f"{speak_qty(qty, p.unit)} {speak_name(p)}"))
        if qty is None:
            if q is None:
                usual = self.memory.usual_qty(p.id) if self.memory is not None else None
                if usual and usual.get("unit") == p.unit:  # "Pillsbury atta, pichhli baar jitna 10 kilo?"
                    self.pending = Pending("qty_default", [p], Qty(float(usual["qty"]), p.unit, 1.0), speak_name(p),
                                           replaced=replaced, said=said or "", via=via)
                    return self._ask(self.r("usual_qty", name=speak_name(p), qty=speak_qty(float(usual["qty"]), p.unit)))
                self.pending = Pending("qty", [p], None, speak_name(p), said=said or "", via=via)
                return self._ask(self.r("ask_qty", name=speak_name(p)))
            unit = {"kg": "kilo", "l": "litre"}.get(p.unit, "packet")
            self.pending = Pending("unit", [p], q, speak_name(p), said=said or "", via=via)
            return self._ask(self.r("ask_unit", name=speak_name(p), unit=unit))
        if self.stock[p.id] < qty:
            sub = self._substitute_for(p, qty)
            if sub is not None:
                diff_amt = round((sub.price - p.price) * qty)
                diff = ("Daam same hai." if diff_amt == 0 else
                        f"{speak_money(abs(diff_amt))} {'zyada' if diff_amt > 0 else 'kam'} hai.")
                self.pending = Pending("substitute_offer", [sub], q, speak_name(p), original=p, replaced=replaced)
                return self._ask(self.r("substitute", a=speak_name(p), b=speak_name(sub), diff=diff))
            self._emit("stockout", sku=p.id)
            return self.r("substitute_declined", a=speak_name(p))
        money = q.value if (q is not None and q.unit == "inr" and p.unit in ("kg", "l")) else None
        existing = next((l for l in self.cart if l.product.id == p.id), None)
        if money is not None:
            existing = None  # keep money lines separate so they are read back as "bees rupaye ka ..."
        if existing:
            existing.qty = round(existing.qty + qty, 3)
            line = existing
            if said and not existing.said:
                existing.said, existing.via = said, via
        else:
            line = Line(self._next_line, p, round(qty, 3), p.unit, money, said, via)
            self._next_line += 1
            self.cart.append(line)
        self.last_line_id = line.id
        self._emit("cart_updated", cart=self.cart_snapshot(), total=self.total)
        if replaced:
            return self.r("replaced", old=replaced, item=line.spoken())
        return f"ADDED::{Line(line.id, p, round(qty, 3), p.unit, money).spoken()}"

    def _ask(self, text: str) -> str:
        if self.pending:
            self.pending.question = text
        self._emit("question", text=text, pending=self.pending.kind if self.pending else None)
        return f"ASK::{text}"

    # ------------------------------------------------------------------ actions
    def _add(self, phrase: str | None, qty_text: str | None, *, replaced: str | None = None) -> str:
        q = parse_qty(qty_text) if qty_text else None
        if not phrase:
            if q and self.cart:
                return self._update(None, qty_text)
            return self._ask(self.r("not_understood_item", phrase=""))
        if self.memory is not None:  # this customer's own words, confirmed in an earlier order
            hit = self.memory.lookup(phrase)
            if hit is not None and hit["sku"] in self.cat.by_id:
                self._trace.setdefault("memory_hits", []).append({"phrase": phrase, "sku": hit["sku"], "said": hit["said"]})
                return self._commit(self.cat.get(hit["sku"]), q, replaced=replaced, said=phrase, via="memory")
        hint = None
        if q:
            v, u = q.normalized()
            if u in ("kg", "l"):
                hint = (v, u)
        res = self.resolver.resolve(phrase, pack_hint=hint, history=dict(self.history_counts),
                                    qty_certainty=q.certainty if q else 0.8)
        self._trace.setdefault("resolutions", []).append(self._res_trace(res))
        if not res.candidates and self.cart and q is None and len(phrase.split()) <= 4:
            # short unknown utterance after "Aur kuch?" is most likely a misheard "bas itna hi":
            # read back (the readback itself asks for an explicit yes, so nothing is ordered by mistake)
            return self._readback()
        if not res.candidates:
            self.unresolved_streak += 1
            if self.unresolved_streak >= 2:
                return self._handoff(f"item not in catalog twice: {phrase}")
            return self._ask(self.r("not_understood_item", phrase="yeh" if PUBLIC_DEMO else phrase))
        self.unresolved_streak = 0
        top = [c.product for c in res.candidates]
        if res.p_correct >= TAU_HIGH or len(top) == 1 and res.p_correct >= TAU_LOW:
            return self._commit(top[0], q, replaced=replaced, said=phrase, via="direct")
        a = top[0]
        if res.p_correct >= TAU_LOW and len(top) >= 2:
            b = top[1]
            if a.brand == b.brand and a.cat == b.cat and a.size and b.size and a.size != b.size:
                self.pending = Pending("size_choice", [a, b], q, phrase, replaced=replaced, said=phrase)
                return self._ask(self.r("ask_size", name=re.sub(r" (bada|chhota)$", "", speak_name(a))))
            self.pending = Pending("brand_choice", [a, b], q, phrase, replaced=replaced, said=phrase)
            na, nb = short_names([a, b])
            return self._ask(self.r("ask_two", a=na, b=nb))
        cands = res.candidates
        if any(c.desc >= 0.5 for c in cands):  # "sarson ka tel": only offer products matching the descriptor
            cands = [c for c in cands if c.desc >= 0.5]
        in_stock = [c.product for c in cands if self.stock[c.product.id] > 0]
        opts = [p for p in (in_stock or [c.product for c in cands]) if p.cat == a.cat][:3] or top[:3]
        names = short_names(opts)
        options = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " ya " + names[-1]
        self.pending = Pending("brand_choice", opts, q, phrase, replaced=replaced, said=phrase)
        return self._ask(self.r("ask_open", generic=_generic_word(a), options=options))

    def _update(self, target: str | None, qty_text: str | None) -> str:
        line = self._find_line(target)
        if line is None:
            if target and target.upper() != "LAST":
                return self._add(target, qty_text)
            return self.r("nothing_to_change", phrase=target or "")
        q = parse_qty(qty_text) if qty_text else None
        if q is None:  # correction with no usable number: ask, then SET (never add) the quantity
            self.pending = Pending("set_qty", [line.product], None, speak_name(line.product), line_id=line.id)
            return self._ask(self.r("ask_set_qty", name=speak_name(line.product)))
        qty = self._to_base(line.product, q)
        if qty is None:
            unit = {"kg": "kilo", "l": "litre"}.get(line.product.unit, "packet")
            self.pending = Pending("unit", [line.product], q, speak_name(line.product))
            self.cart.remove(line)
            return self._ask(self.r("ask_unit", name=speak_name(line.product), unit=unit))
        if self.stock[line.product.id] < qty:
            return self.r("substitute_declined", a=f"{speak_qty(qty, line.unit)} {speak_name(line.product)}")
        line.qty = round(qty, 3)
        self.last_line_id = line.id
        self._emit("cart_updated", cart=self.cart_snapshot(), total=self.total)
        return self.r("updated", item=line.spoken())

    def _remove(self, target: str | None) -> str:
        line = self._find_line(target)
        if line is None:
            return self.r("nothing_to_change", phrase=target or "")
        self.cart.remove(line)
        if self.last_line_id == line.id:
            self.last_line_id = self.cart[-1].id if self.cart else None
        self._emit("cart_updated", cart=self.cart_snapshot(), total=self.total)
        return self.r("removed", name=speak_name(line.product))

    def _replace(self, target: str | None, phrase: str | None, qty_text: str | None) -> str:
        line = self._find_line(target)
        if line is None and self.cart:  # "X ki jagah Y" with X misheard: X is almost always the last item
            line = self._line_by_id(self.last_line_id) or self.cart[-1]
        if line is None:
            return self._add(phrase, qty_text)
        self.cart.remove(line)
        self._emit("cart_updated", cart=self.cart_snapshot(), total=self.total)
        if not qty_text:
            qty_text = f"{line.qty:g} {line.unit}" if line.unit in ("kg", "l") else f"{line.qty:g}"
        out = self._add(phrase, qty_text, replaced=speak_name(line.product))
        return out

    def _choose(self, text: str) -> str:
        pend = self.pending
        assert pend is not None
        keys = key(text).split()
        kj = " ".join(keys)
        yes = kj in YES or (keys and keys[0] in YES and not any(k in NO for k in keys))
        no = bool(keys) and keys[0] in NO
        if pend.kind == "cancel_confirm":
            self.pending = None
            if yes:
                self.cart.clear()
                self.awaiting_confirm = False
                self._emit("cart_updated", cart=[], total=0)
                return self.r("cancelled")
            return self.r("not_cancelled")
        if pend.kind == "substitute_offer":
            self.pending = None
            if yes:
                return self._commit(pend.candidates[0], pend.qty, replaced=pend.replaced)
            return self.r("substitute_declined", a=speak_name(pend.original) if pend.original else pend.phrase)
        if pend.kind == "qty_confirm":
            self.pending = None
            q_new = parse_qty(text)
            if q_new is not None:  # "nahi, paav kilo" -> use the corrected quantity
                return self._commit(pend.candidates[0], q_new, replaced=pend.replaced, checked=True,
                                    said=pend.said, via=pend.via)
            if yes:
                return self._commit(pend.candidates[0], pend.qty, replaced=pend.replaced, checked=True,
                                    said=pend.said, via=pend.via)
            self.pending = Pending("qty", [pend.candidates[0]], None, pend.phrase, replaced=pend.replaced)
            return self._ask(self.r("ask_qty", name=speak_name(pend.candidates[0])))
        if pend.kind == "set_qty":
            q = parse_qty(text)
            if q is None:
                return self._retry(pend)
            self.pending = None
            line = self._line_by_id(pend.line_id)
            qty = self._to_base(pend.candidates[0], q) if line else None
            if line is None or qty is None:
                return self._retry(pend)
            line.qty = round(qty, 3)
            self.last_line_id = line.id
            self._emit("cart_updated", cart=self.cart_snapshot(), total=self.total)
            return self.r("updated", item=line.spoken())
        if pend.kind == "qty_default":  # "pichhli baar jitna 10 kilo?" -> haan / "nahi, paanch kilo"
            q_new = parse_qty(text)
            if q_new is not None:
                self.pending = None
                return self._commit(pend.candidates[0], q_new, replaced=pend.replaced, said=pend.said, via=pend.via)
            if yes:
                self.pending = None
                return self._commit(pend.candidates[0], pend.qty, replaced=pend.replaced, said=pend.said, via=pend.via)
            self.pending = Pending("qty", [pend.candidates[0]], None, pend.phrase, said=pend.said, via=pend.via)
            return self._ask(self.r("ask_qty", name=speak_name(pend.candidates[0])))
        if pend.kind in ("qty", "unit"):
            q = parse_qty(text)
            if q is None and pend.soft:  # caller moved on ("aur Pillsbury kitne ka?"): treat as a fresh turn
                self.pending = None
                f = self.extractor(text, Context(cart_phrases=[l.spoken() for l in self.cart]))
                outs = [self._dispatch(x) for x in f.actions[:1]]
                return outs[0] if outs else self.r("not_heard")
            if q is None:
                return self._retry(pend)
            self.pending = None
            return self._commit(pend.candidates[0], q, said=pend.said or None, via=pend.via)
        # brand_choice / size_choice
        chosen: Product | None = None
        cands = list(pend.candidates)
        for k in keys:
            if k in ORDINALS and ORDINALS[k] < len(cands):
                chosen = cands[ORDINALS[k]]
            if k in SIZE and any(c.size for c in cands):  # "Tata chhota wala": size narrows a brand question too
                sized = [c for c in cands if c.size in (SIZE[k], None)]
                if pend.kind == "size_choice" or len(sized) == 1:
                    chosen = next((c for c in sized if c.size == SIZE[k]), chosen)
                cands = sized or cands
        if chosen is None and yes and len(pend.candidates) == 1:
            chosen = pend.candidates[0]
        if chosen is None and pend.kind in ("brand_choice", "size_choice"):
            # answers often use just the brand's first word ("Tata wala", "Mother wala")
            firsts = [c for c in cands if c.brand and key(c.brand).split()[0] in keys]
            if len(firsts) == 1:
                chosen = firsts[0]
        if chosen is None:
            res = self.resolver.resolve(text, restrict=cands, k=3)
            self._trace.setdefault("choice_resolution", []).append(self._res_trace(res))
            if res.top and res.top.alias > 0 and (len(res.candidates) == 1 or res.features["margin"] > 0.05):
                chosen = res.top.product
        if chosen is None:
            q_in_answer = parse_qty(text)
            if no and not q_in_answer:
                self.pending = None
                return self.r("substitute_declined", a=pend.phrase)
            return self._retry(pend)
        self.pending = None
        q_ans = parse_qty(text)  # "Pillsbury wala, teen kilo" overrides; a bare "1" ("Ghadi wala" heard as "Ghadi 1") doesn't
        q = q_ans if (q_ans is not None and (q_ans.unit is not None or pend.qty is None)) else pend.qty
        return self._commit(chosen, q, replaced=pend.replaced, said=pend.said or None, via="asked")

    def _retry(self, pend: Pending) -> str:
        pend.attempts += 1
        if pend.attempts >= 2:
            self.pending = None
            return self._handoff(f"clarification failed twice: {pend.phrase}")
        return self._ask(pend.question.removeprefix("ASK::"))

    def _readback(self, template: str = "readback") -> str:
        if not self.cart:
            return self.r("empty_cart")
        if self.total > HIGH_VALUE_HANDOFF:
            return self._handoff(f"high-value order ₹{self.total}")
        self.awaiting_confirm = True
        lines = ", ".join(l.spoken() for l in self.cart)
        text = self.r(template, lines=lines, total=speak_money(self.total))
        self._emit("readback", cart=self.cart_snapshot(), total=self.total)
        return f"ASK::{text}"

    def _credit_check(self, amount: float) -> str | None:
        """Code decides credit: returns a refusal message, or None if udhaar is allowed."""
        limit = float(self.customer.get("credit_limit", 0) or 0)
        bal = LEDGER.get(self.customer_id, 0.0)
        if limit <= 0:
            return self.r("udhaar_none")
        if bal + amount > limit:
            return self.r("udhaar_over_limit", limit=speak_money(limit), balance=speak_money(bal))
        return None

    def _place(self) -> str:
        assert self.awaiting_confirm and self.cart, "hard rule: readback + confirm required"
        if self.payment == "udhaar":
            refusal = self._credit_check(self.total)  # re-checked at placement, not just when asked
            if refusal:
                self.payment = None
                return f"ASK::{refusal}"
        snap = self.cart_snapshot()
        idem = hashlib.sha1(f"{self.session_id}|{sorted((l['sku'], l['qty']) for l in snap)}".encode()).hexdigest()[:12]
        order = self.placed_orders.get(idem)
        if order is None:
            order = {"order_id": f"ORD-{idem}", "customer": self.customer_id, "lines": snap, "total": self.total,
                     "ts": time.time(), "register": self.register, "payment": self.payment or "cash/upi"}
            self.placed_orders[idem] = order
            for l in self.cart:
                self.stock[l.product.id] -= l.qty
            if self.payment == "udhaar":
                LEDGER[self.customer_id] = LEDGER.get(self.customer_id, 0.0) + order["total"]
                order["balance_after"] = LEDGER[self.customer_id]
                self._emit("khata_updated", customer=self.customer_id, balance=LEDGER[self.customer_id])
            self._emit("order_placed", order=order)
            if self.memory is not None:  # the readback + explicit "haan" makes this a verified label
                learned = self.memory.learn([{"sku": l.product.id, "qty": l.qty, "unit": l.unit, "said": l.said,
                                              "via": l.via, "money": l.money} for l in self.cart], ts=order["ts"])
                order["learned"] = learned
                if self.on_learn:
                    self.on_learn(self.memory)
                self._emit("memory_updated", customer=self.customer_id, learned=learned)
        self.order = order
        self.awaiting_confirm = False
        eta = "Aadhe ghante mein pahunch jayega."
        if order.get("payment") == "udhaar":
            return self.r("placed_udhaar", total=speak_money(order["total"]),
                          balance=speak_money(order.get("balance_after", LEDGER.get(self.customer_id, 0))), eta=eta)
        return self.r("placed", total=speak_money(order["total"]), eta=eta)

    def _handoff(self, reason: str) -> str:
        self.handed_off = True
        self._emit("handoff", reason=reason, cart=self.cart_snapshot(), transcript=self._trace.get("transcript"))
        return f"END::{self.r('handoff')}"

    def _repeat(self) -> str:
        hist = self.customer.get("history") or []
        if not hist:
            return self.r("no_history")
        for l in hist[0]["lines"]:
            p = self.cat.get(l["sku"])
            if self.stock[p.id] >= l["qty"]:
                self.cart.append(Line(self._next_line, p, float(l["qty"]), p.unit))
                self._next_line += 1
        self._emit("cart_updated", cart=self.cart_snapshot(), total=self.total)
        return self._readback("repeat_loaded")

    # ------------------------------------------------------------------ turn
    def start_outbound(self, due: list[dict]) -> str:
        """The shop calls first: pre-fill the cart with what the customer usually re-buys, then read it back.
        Everything after this is the normal dialogue ("haan" / "atta das kilo kar do" / "doodh hata do")."""
        for d in due:
            if d["sku"] not in self.cat.by_id:
                continue
            p = self.cat.get(d["sku"])
            qty = float(d["qty"])
            if self.stock[p.id] < qty:
                continue
            self.cart.append(Line(self._next_line, p, qty, p.unit, None, None, "memory"))
            self._next_line += 1
        if not self.cart:
            return self.greet()
        self.awaiting_confirm = True  # the offer IS a readback: nothing is ordered without an explicit yes
        self.last_line_id = self.cart[-1].id
        lines = ", ".join(l.spoken() for l in self.cart)
        text = self.r("outbound_offer", name=self.customer.get("name", ""), shop=self.shop, lines=lines,
                      total=speak_money(self.total))
        self._events = []
        self._emit("outbound_started", cart=self.cart_snapshot(), total=self.total)
        self._emit("cart_updated", cart=self.cart_snapshot(), total=self.total)
        self.last_agent_prompt = text
        return text

    def greet(self) -> str:
        text = self.r("greet", shop=self.shop)
        self.last_agent_prompt = text
        return text

    def handle(self, transcript: str) -> TurnResult:
        self._events, self._trace = [], {"transcript": transcript, "cart_before": self.cart_snapshot()}
        if self.handed_off or self.order:
            return TurnResult("", [], None, self._trace)
        if not transcript.strip():
            self.empty_streak += 1
            reply = self.r("one_at_a_time") if self.empty_streak >= 2 else self.r("not_heard")
            return self._finish(reply, None)
        self.empty_streak = 0
        ctx = Context(pending=self.pending.kind if self.pending else None, awaiting_confirm=self.awaiting_confirm,
                      cart_phrases=[l.spoken() for l in self.cart], last_agent_prompt=self.last_agent_prompt)
        frame = self.extractor(transcript, ctx)
        # register is sticky for Haryanvi: callers don't use dialect markers in every utterance
        if frame.speech_register == "haryanvi" or (self.register != "haryanvi" and len(transcript.split()) >= 2
                                                    and frame.speech_register != "en"):
            self.register = frame.speech_register
        return self._finish(self.apply(frame), frame)

    def apply(self, frame: Frame) -> str:
        was_awaiting = self.awaiting_confirm
        cart_before = [(l.product.id, l.qty) for l in self.cart]
        actions = list(frame.actions) + self.queue
        self.queue = []
        added: list[str] = []
        parts: list[str] = []
        for i, a in enumerate(actions):
            out = self._dispatch(a)
            if out.startswith("ADDED::"):
                added.append(out[7:])
                continue
            if out.startswith("ASK::") or out.startswith("END::"):
                self.queue = [x for x in actions[i + 1:]]
                if added:
                    parts.append(self.r("added_many", items=", ".join(added)).rsplit(".", 1)[0] + ".")
                parts.append(out.split("::", 1)[1])
                return " ".join(parts)
            parts.append(out)
        if self.awaiting_confirm and not self.order and actions and actions[-1].type == "udhaar":
            parts.append("Confirm karun?")
        if was_awaiting and not self.order and [(l.product.id, l.qty) for l in self.cart] != cart_before:
            return self._readback().split("::", 1)[1]  # corrections during readback -> read back again
        if added:
            parts.insert(0, self.r("added", item=added[0]) if len(added) == 1 else
                         self.r("added_many", items=", ".join(added)))
            parts = [p for p in parts if p]
        if not parts:
            return self.r("not_heard")
        # keep only one trailing "Aur kuch?" when several acks are combined
        text = " ".join(parts)
        return re.sub(r"(\s*Aur (kuch|ke chahiye)\?)(?=.*Aur (kuch|ke chahiye)\?)", "", text)

    def _dispatch(self, a: Action) -> str:
        t = a.type
        if self.pending and t == "choose":
            return self._choose(a.choice_text or "")
        if t == "choose":
            return self._add(a.choice_text, None)
        if self.pending and t not in ("ask_human", "cancel_all"):
            self.pending = None  # customer moved on; drop the unanswered question
        if t == "add":
            return self._add(a.product_phrase, a.qty_text)
        if t == "update":
            return self._update(a.target, a.qty_text)
        if t == "remove":
            return self._remove(a.target)
        if t == "replace":
            return self._replace(a.target, a.product_phrase, a.qty_text)
        if t == "done":
            return self._readback()
        if t == "confirm_yes":
            if self.awaiting_confirm and self.cart:
                return self._place()
            return self._readback()
        if t == "confirm_no":
            self.awaiting_confirm = False
            return f"ASK::{self.r('what_to_change')}"
        if t == "cancel_all":
            self.pending = Pending("cancel_confirm")
            return self._ask(self.r("cancel_confirm"))
        if t == "repeat_last_order":
            return self._repeat()
        if t == "ask_human":
            return self._handoff("customer asked for a person")
        if t == "store_question":
            return self.r("store_info")
        if t in ("price_query", "stock_query"):
            return self._query(t, a.product_phrase or "")
        if t == "udhaar":
            refusal = self._credit_check(self.total)
            if refusal:
                return f"ASK::{refusal}"
            self.payment = "udhaar"
            if self.awaiting_confirm and self.cart:
                return self.r("udhaar_noted")  # plain: a following confirm_yes in the same turn still runs
            return self.r("udhaar_noted") + " " + ("Aur kuch?" if self.cart else "Kya bhejna hai?")
        if t == "balance_query":
            return self.r("balance_info", balance=speak_money(LEDGER.get(self.customer_id, 0.0)))
        if t in ("slow_down", "repeat"):
            if t == "slow_down":
                self.slow = True
            return f"ASK::{self.last_agent_prompt}" if self.last_agent_prompt else self.r("not_heard")
        return ""

    def _query(self, kind: str, phrase: str) -> str:
        res = self.resolver.resolve(phrase, history=dict(self.history_counts))
        self._trace.setdefault("resolutions", []).append(self._res_trace(res))
        if not res.candidates:
            return self._ask(self.r("not_understood_item", phrase=phrase))
        top = [c.product for c in res.candidates]
        p = top[0]
        if kind == "stock_query" and self.stock[p.id] <= 0:
            sub = self._substitute_for(p, 1)
            if sub is None:
                return self.r("substitute_declined", a=speak_name(p))
            diff_amt = round(sub.price - p.price)
            diff = ("Daam same hai." if diff_amt == 0 else
                    f"{speak_money(abs(diff_amt))} {'zyada' if diff_amt > 0 else 'kam'} hai.")
            self.pending = Pending("substitute_offer", [sub], None, speak_name(p), original=p)
            return self._ask(self.r("substitute", a=speak_name(p), b=speak_name(sub), diff=diff))
        if res.p_correct >= TAU_HIGH or len(top) == 1:
            self.pending = Pending("qty", [p], None, speak_name(p), soft=True)
            if kind == "stock_query":
                return self._ask(self.r("stock_yes", name=speak_name(p)))
            return self._ask(self.r("price_info", name=speak_name(p), price=speak_money(p.price),
                                    unit=UNIT_SPOKEN.get(p.unit, p.unit)))
        a, b = top[0], top[1]
        na, nb = short_names([a, b])
        self.pending = Pending("brand_choice", [a, b], None, phrase)
        if kind == "price_query" and a.unit == b.unit:
            return self._ask(self.r("price_two", a=na, pa=speak_money(a.price), b=nb, pb=speak_money(b.price),
                                    unit=UNIT_SPOKEN.get(a.unit, a.unit)))
        return self._ask(self.r("ask_two", a=na, b=nb))

    def _finish(self, reply: str, frame: Frame | None) -> TurnResult:
        self.last_agent_prompt = reply
        self._trace.update({"frame": frame.model_dump(by_alias=True) if frame else None, "reply": reply,
                            "cart_after": self.cart_snapshot(), "pending": self.pending.kind if self.pending else None,
                            "awaiting_confirm": self.awaiting_confirm, "register": self.register})
        return TurnResult(reply, self._events, frame, self._trace)
