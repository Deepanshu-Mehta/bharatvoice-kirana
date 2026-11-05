"""Customer memory: what the shopkeeper knows about each regular, learned ONLY from confirmed orders.

A confirmed order (spoken readback + explicit "haan") is a verified label of what the customer's words meant.
From it we learn, per customer and with no retraining:
  * phrases  - "kapde dhone aala surf" -> ghadi-1kg  (learned when the agent had to ask; never asked again)
  * usual    - sku -> usual quantity                (offered back: "Pillsbury atta, pichhli baar jitna 10 kilo?")
  * orders   - purchase history                     (purchase rhythm -> "due this week" outbound calls)
Everything is per customer, visible to the shopkeeper, and erasable.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from app.brain.text import key

ROOT = Path(__file__).resolve().parents[2]
MAX_PHRASE_WORDS = 6


@dataclass
class CustomerMemory:
    customer: str
    phrases: dict[str, dict] = field(default_factory=dict)  # key(said) -> {sku, said, count, last}
    usual: dict[str, dict] = field(default_factory=dict)  # sku -> {qty, unit, count}
    orders: list[dict] = field(default_factory=list)  # [{ts, lines: [{sku, qty, unit}]}]

    # ------------------------------------------------------------------ read
    def lookup(self, phrase: str) -> dict | None:
        """A phrase this customer used before for a confirmed product (exact, or a close re-hearing of it)."""
        k = key(phrase)
        if not k:
            return None
        if k in self.phrases:
            return self.phrases[k]
        best, score = None, 0.0
        for pk, v in self.phrases.items():
            if abs(len(pk.split()) - len(k.split())) > 1:
                continue
            s = fuzz.ratio(pk, k)
            if s > score:
                best, score = v, s
        return best if score >= 88 else None

    def usual_qty(self, sku: str) -> dict | None:
        return self.usual.get(sku)

    def keyterms(self, limit: int = 30) -> list[str]:
        """This customer's own words, for Prisma biasing (Latin-script phrases only)."""
        items = sorted(self.phrases.values(), key=lambda v: (-v["count"], -v.get("last", 0)))
        return [v["said"] for v in items if v["said"].isascii()][:limit]

    def summary(self) -> dict:
        return {"phrases": [{"said": v["said"], "sku": v["sku"], "count": v["count"]} for v in self.phrases.values()],
                "usual": self.usual, "orders": len(self.orders)}

    # ------------------------------------------------------------------ learn (confirmed orders only)
    def learn(self, lines: list[dict], ts: float | None = None) -> list[dict]:
        """lines: [{sku, qty, unit, said, via}] from a CONFIRMED order. Returns what was newly learned."""
        ts = ts or time.time()
        learned = []
        for l in lines:
            said, via = (l.get("said") or "").strip(), l.get("via")
            if said and via in ("asked", "memory") and len(said.split()) <= MAX_PHRASE_WORDS:
                k = key(said)
                prev = self.phrases.get(k)
                if prev is None or prev["sku"] != l["sku"]:
                    learned.append({"said": said, "sku": l["sku"]})
                    self.phrases[k] = {"sku": l["sku"], "said": said, "count": 1, "last": ts}
                else:
                    prev["count"] += 1
                    prev["last"] = ts
            if l.get("money") is None:
                u = self.usual.get(l["sku"])
                self.usual[l["sku"]] = {"qty": l["qty"], "unit": l["unit"], "count": (u["count"] + 1) if u else 1}
        self.orders.append({"ts": ts, "lines": [{"sku": l["sku"], "qty": l["qty"], "unit": l["unit"]} for l in lines]})
        return learned

    def forget(self) -> None:
        self.phrases.clear()
        self.usual.clear()

    # ------------------------------------------------------------------ rhythm (for outbound "you're due" calls)
    def due(self, now: float | None = None, horizon_days: float = 3.0) -> list[dict]:
        """Items whose usual re-purchase interval says they run out within `horizon_days`."""
        now = now or time.time()
        by_sku: dict[str, list[tuple[float, float]]] = {}
        for o in self.orders:
            for l in o["lines"]:
                by_sku.setdefault(l["sku"], []).append((o["ts"], l["qty"]))
        out = []
        for sku, buys in by_sku.items():
            if len(buys) < 2:
                continue
            buys.sort()
            gaps = [b[0] - a[0] for a, b in zip(buys, buys[1:]) if b[0] > a[0]]
            if not gaps:
                continue
            interval = sorted(gaps)[len(gaps) // 2]  # median gap
            last_ts, last_qty = buys[-1]
            due_at = last_ts + interval
            days_left = (due_at - now) / 86400
            if days_left <= horizon_days:
                out.append({"sku": sku, "qty": last_qty, "unit": self.usual.get(sku, {}).get("unit"),
                            "interval_days": round(interval / 86400, 1), "days_left": round(days_left, 1)})
        return sorted(out, key=lambda d: d["days_left"])

    def to_json(self) -> dict:
        return {"customer": self.customer, "phrases": self.phrases, "usual": self.usual, "orders": self.orders}


class MemoryStore:
    """JSON-per-customer store. Seeded with purchase history from data/customers.yaml (synthetic)."""

    def __init__(self, directory: Path | None = None, persist: bool = True):
        self.dir = Path(directory or os.environ.get("MEMORY_DIR", ROOT / "state" / "memory"))
        self.persist = persist
        self._mem: dict[str, CustomerMemory] = {}
        self._lock = threading.Lock()

    def _seed(self, cid: str) -> CustomerMemory:
        m = CustomerMemory(cid)
        cust = (yaml.safe_load((ROOT / "data" / "customers.yaml").read_text()) or {}).get(cid, {})
        for o in reversed(cust.get("history", [])):  # oldest first
            ts = time.mktime(time.strptime(str(o["date"]), "%Y-%m-%d"))
            m.orders.append({"ts": ts, "lines": [dict(l) for l in o["lines"]]})
            for l in o["lines"]:
                u = m.usual.get(l["sku"])
                m.usual[l["sku"]] = {"qty": l["qty"], "unit": l["unit"], "count": (u["count"] + 1) if u else 1}
        return m

    def get(self, cid: str) -> CustomerMemory:
        with self._lock:
            if cid not in self._mem:
                p = self.dir / f"{cid}.json"
                if self.persist and p.exists():
                    d = json.loads(p.read_text())
                    self._mem[cid] = CustomerMemory(cid, d.get("phrases", {}), d.get("usual", {}), d.get("orders", []))
                else:
                    self._mem[cid] = self._seed(cid)
            return self._mem[cid]

    def save(self, mem: CustomerMemory) -> None:
        if not self.persist:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{mem.customer}.json").write_text(json.dumps(mem.to_json(), ensure_ascii=False, indent=1))

    def reset(self, cid: str) -> CustomerMemory:
        with self._lock:
            self._mem[cid] = self._seed(cid)
        self.save(self._mem[cid])
        return self._mem[cid]
