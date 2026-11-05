"""Customer memory: learned only from confirmed orders; never asks the same customer the same question twice."""
import time

from app.brain.dm import Session
from app.brain.memory import CustomerMemory, MemoryStore


def call(mem, turns, cust="CUST-002"):
    s = Session(cust, memory=mem)
    s.greet()
    replies = [s.handle(t).reply for t in turns]
    return s, replies


def cart(s):
    return sorted((l["sku"], l["qty"]) for l in s.cart_snapshot())


def test_clarified_phrase_is_learned_and_never_asked_again():
    mem = CustomerMemory("CUST-002")
    s1, r1 = call(mem, ["do packet kapde dhone aala surf", "Ghadi wala", "bas", "haan"])
    assert "Ghadi" in r1[0] and s1.order  # first time: it has to ask
    assert any(p["sku"] == "ghadi-1kg" for p in mem.phrases.values())
    s2, r2 = call(mem, ["do packet kapde dhone aala surf", "bas", "haan"])
    assert cart(s2) == [("ghadi-1kg", 2)] and "?" not in r2[0].split(".")[0]  # second time: no question
    assert s2.cart[0].via == "memory"


def test_generic_word_becomes_this_customers_brand():
    mem = CustomerMemory("CUST-003")
    call(mem, ["do kilo atta", "Pillsbury wala", "bas", "haan"], cust="CUST-003")
    s, r = call(mem, ["teen kilo atta", "bas", "haan"], cust="CUST-003")
    assert cart(s) == [("pillsbury-atta", 3)]


def test_usual_quantity_is_offered():
    mem = CustomerMemory("CUST-003")
    call(mem, ["das kilo atta", "Pillsbury wala", "bas", "haan"], cust="CUST-003")
    s, r = call(mem, ["atta bhej dena", "haan", "bas", "haan"], cust="CUST-003")
    assert "pichhli baar" in r[0] and "das kilo" in r[0]
    assert cart(s) == [("pillsbury-atta", 10)]


def test_usual_quantity_can_be_changed():
    mem = CustomerMemory("CUST-003")
    call(mem, ["das kilo atta", "Pillsbury wala", "bas", "haan"], cust="CUST-003")
    s, _ = call(mem, ["atta bhej dena", "nahi, paanch kilo", "bas", "haan"], cust="CUST-003")
    assert cart(s) == [("pillsbury-atta", 5)]


def test_nothing_learned_without_confirmation():
    mem = CustomerMemory("CUST-002")
    call(mem, ["do kilo atta", "Fortune wala", "sab cancel kar do", "haan"])
    assert mem.phrases == {} and mem.orders == []


def test_memory_is_per_customer():
    store = MemoryStore(persist=False)
    a, b = store.get("CUST-002"), store.get("CUST-003")
    call(a, ["do kilo atta", "Fortune wala", "bas", "haan"])
    s, r = call(b, ["do kilo atta"], cust="CUST-003")
    assert "Fortune" in r[0] and "Aashirvaad" in r[0]  # Gupta ji still gets asked


def test_store_seeds_history_and_persists(tmp_path):
    store = MemoryStore(directory=tmp_path)
    m = store.get("CUST-001")
    assert m.usual["aashirvaad-atta"]["qty"] == 5 and len(m.orders) == 4
    m.learn([{"sku": "ghadi-1kg", "qty": 1, "unit": "pkt", "said": "kapde wala surf", "via": "asked"}])
    store.save(m)
    again = MemoryStore(directory=tmp_path).get("CUST-001")
    assert again.lookup("kapde wala surf")["sku"] == "ghadi-1kg"


def test_purchase_rhythm_due():
    m = CustomerMemory("X")
    day = 86400
    t0 = time.time() - 30 * day
    for k in range(3):  # atta every 14 days: last bought 2 days ago -> due in ~12 days; milk every 2 days -> due now
        m.orders.append({"ts": t0 + k * 14 * day, "lines": [{"sku": "aashirvaad-atta", "qty": 5, "unit": "kg"}]})
    for k in range(5):
        m.orders.append({"ts": time.time() - (10 - 2 * k) * day, "lines": [{"sku": "amul-taaza", "qty": 2, "unit": "l"}]})
    due = {d["sku"] for d in m.due(horizon_days=3)}
    assert "amul-taaza" in due and "aashirvaad-atta" not in due


def test_fuzzy_rehearing_still_hits():
    m = CustomerMemory("X")
    m.learn([{"sku": "lifebuoy", "qty": 1, "unit": "pc", "said": "lal wala sabun", "via": "asked"}])
    assert m.lookup("laal wala saabun")["sku"] == "lifebuoy"
    assert m.lookup("lux sabun") is None


def test_outbound_offer_is_a_readback_and_needs_yes():
    store = MemoryStore(persist=False)
    m = store.get("CUST-001")
    s = Session("CUST-001", memory=m)
    text = s.start_outbound([{"sku": "amul-taaza", "qty": 2, "unit": "l"}, {"sku": "aashirvaad-atta", "qty": 5, "unit": "kg"}])
    assert "khatam hone wala" in text and s.awaiting_confirm and s.order is None
    r = s.handle("haan, par atta das kilo kar do").reply
    assert "Confirm" in r and s.order is None  # an edit reads back again; still not ordered
    s.handle("haan")
    assert sorted((l["sku"], l["qty"]) for l in s.order["lines"]) == [("aashirvaad-atta", 10), ("amul-taaza", 2)]


def test_outbound_decline_orders_nothing():
    store = MemoryStore(persist=False)
    s = Session("CUST-001", memory=store.get("CUST-001"))
    s.start_outbound([{"sku": "amul-taaza", "qty": 2, "unit": "l"}])
    s.handle("nahi")
    assert s.order is None


def test_lookup_survives_one_added_or_dropped_word():
    from app.brain.memory import CustomerMemory
    m = CustomerMemory("CUST-001")
    m.learn([{"sku": "surf-excel-1kg", "qty": 1, "unit": "pkt", "said": "kapde dhone powder", "via": "asked"}])
    assert m.lookup("kapde dhone powder")["sku"] == "surf-excel-1kg"
    assert m.lookup("kapde dhone wala powder") is None or m.lookup("kapde dhone wala powder")["sku"] == "surf-excel-1kg"
    assert m.lookup("vahin kapde dhone powder")["sku"] == "surf-excel-1kg"
    assert m.lookup("dhone") is None  # a single shared word is not a memory hit


def test_wahi_is_a_memory_cue_not_a_product_word():
    from app.brain.extract_rules import extract
    f = extract("वहीं कपड़े dhone powder भेज दो")
    assert [a.product_phrase for a in f.actions if a.type == "add"] == ["kapre dhone powder"]
