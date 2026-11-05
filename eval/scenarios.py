"""Generate reactive eval scenarios (plan §8.1) -> data/scenarios/<register>-NNN.json

Each scenario: customer turns + answers keyed by the agent's question type + gold final order.
Hinglish/Hindi phrasing is machine-drafted and must be spot-checked; Haryanvi scenarios are marked draft=true
until a native speaker reviews them.

  uv run python -m eval.scenarios            # writes 30 per register (90)
"""
from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "scenarios"

# (sku, roman phrase that names it unambiguously, devanagari phrase, ambiguous generic phrase, answer to brand question)
ITEMS = [
    ("aashirvaad-atta", "Aashirvaad atta", "आशीर्वाद आटा", "atta", "Aashirvaad wala"),
    ("pillsbury-atta", "Pillsbury atta", "पिल्सबरी आटा", "atta", "Pillsbury wala"),
    ("fortune-atta", "Fortune atta", "फॉर्च्यून आटा", "atta", "Fortune wala"),
    ("amul-taaza", "Amul Taaza doodh", "अमूल ताज़ा दूध", "doodh", "Amul Taaza"),
    ("mother-dairy-toned", "Mother Dairy toned doodh", "मदर डेयरी दूध", "doodh", "Mother Dairy wala"),
    ("fortune-mustard-oil", "Fortune sarson ka tel", "फॉर्च्यून सरसों का तेल", "sarson ka tel", "Fortune wala"),
    ("cheeni-loose", "cheeni", "चीनी", None, None),
    ("toor-dal-loose", "arhar dal", "अरहर दाल", None, None),
    ("moong-dal", "moong dal", "मूंग दाल", None, None),
    ("chana-dal", "chana dal", "चना दाल", None, None),
    ("haldi", "haldi", "हल्दी", None, None),
    ("jeera", "jeera", "जीरा", None, None),
    ("eggs", "ande", "अंडे", None, None),
    ("parle-g", "Parle-G biscuit", "पारले जी बिस्किट", None, None),
    ("good-day", "Good Day biscuit", "गुड डे बिस्किट", None, None),
    ("maggi-70", "chhota Maggi", "छोटा मैगी", "Maggi", "chhota wala"),
    ("surf-excel-1kg", "Surf Excel", "सर्फ एक्सेल", "surf", "Surf Excel"),
    ("ghadi-1kg", "Ghadi detergent", "घड़ी डिटर्जेंट", "surf", "Ghadi wala"),
    ("lux", "Lux sabun", "लक्स साबुन", None, None),
    ("lifebuoy", "Lifebuoy sabun", "लाइफबॉय साबुन", None, None),
    ("tata-salt", "Tata namak", "टाटा नमक", None, None),
    ("colgate-100", "Colgate", "कोलगेट", None, None),
    ("britannia-bread", "Britannia bread", "ब्रिटानिया ब्रेड", "bread", "Britannia wala"),
    ("amul-butter-100", "Amul butter", "अमूल बटर", None, None),
]
BY_SKU = {i[0]: i for i in ITEMS}
UNIT = {"aashirvaad-atta": "kg", "pillsbury-atta": "kg", "fortune-atta": "kg", "amul-taaza": "l",
        "mother-dairy-toned": "l", "fortune-mustard-oil": "l", "cheeni-loose": "kg", "toor-dal-loose": "kg",
        "moong-dal": "kg", "chana-dal": "kg", "haldi": "kg", "jeera": "kg", "eggs": "pc"}
# spoken quantity -> (value in base unit, roman, devanagari)
QTY_KG = [(1, "ek kilo", "एक किलो"), (2, "do kilo", "दो किलो"), (5, "paanch kilo", "पाँच किलो"),
          (0.5, "aadha kilo", "आधा किलो"), (1.5, "dedh kilo", "डेढ़ किलो"), (2.5, "dhai kilo", "ढाई किलो"),
          (0.25, "paav kilo", "पाव किलो"), (3, "teen kilo", "तीन किलो")]
QTY_SPICE = [(0.25, "dhai sau gram", "ढाई सौ ग्राम"), (0.1, "sau gram", "सौ ग्राम"), (0.5, "aadha kilo", "आधा किलो"),
             (0.25, "paav kilo", "पाव किलो")]
QTY_L = [(1, "ek litre", "एक लीटर"), (2, "do litre", "दो लीटर"), (0.5, "aadha litre", "आधा लीटर")]
QTY_PC = [(1, "ek packet", "एक पैकेट"), (2, "do packet", "दो पैकेट"), (3, "teen packet", "तीन पैकेट"),
          (4, "chaar packet", "चार पैकेट")]
QTY_EGGS = [(12, "ek darjan", "एक दर्जन"), (6, "aadha darjan", "आधा दर्जन")]
OPEN = {"hinglish": ["Bhaiya, ", "Haan bhaiya, ", "", "Suniye, "], "hindi": ["भैया, ", "नमस्ते, ", ""],
        "haryanvi": ["Ram Ram bhai, ", "Bhai, ", "Ram Ram, "]}
CLOSE = {"hinglish": [" bhej dena", " de dena", " chahiye", ""], "hindi": [" भेज दीजिए", " चाहिए", " दे दीजिए"],
         "haryanvi": [" chahiye", " de diyo", " bhej diyo"]}
JOIN = {"hinglish": " aur ", "hindi": " और ", "haryanvi": " aur "}
DONE = {"hinglish": "bas itna hi", "hindi": "बस इतना ही", "haryanvi": "bas itna e"}
YES = {"hinglish": "haan, confirm kar do", "hindi": "हाँ, कर दीजिए", "haryanvi": "haan, pakka kar do"}
VOICES = {"hinglish": [("Kaveri", "hi-en"), ("Shlok", "hi-en"), ("Devika", "hi-en"), ("Girish", "hi-en")],
          "hindi": [("Nalini", "hi-IN"), ("Deepak", "hi-IN"), ("Bhavna", "hi-IN"), ("Hemraj", "hi-IN")],
          "haryanvi": [("Vikrant", "hi-en"), ("Roopesh", "hi-en"), ("Ambuja", "hi-en"), ("Omkar", "hi-en")]}


def qty_options(sku: str):
    if sku in ("haldi", "jeera"):
        return QTY_SPICE
    if sku == "eggs":
        return QTY_EGGS
    u = UNIT.get(sku, "pkt")
    return {"kg": QTY_KG, "l": QTY_L}.get(u, QTY_PC)


def unit_of(sku: str) -> str:
    from app.brain.catalog import load_catalog
    return load_catalog().get(sku).unit


def phrase(reg: str, qty_txt: tuple, name: tuple) -> str:
    q = qty_txt[2] if reg == "hindi" else qty_txt[1]
    n = name[1] if reg == "hindi" else name[0]
    return f"{q} {n}"


def make(reg: str, idx: int, rng: random.Random) -> dict:
    kind = ["simple", "multi", "brand_clash", "correction", "fraction", "remove", "replace", "stockout",
            "descriptor", "multi"][idx % 10]
    tags: list[str] = [kind]
    answers: dict[str, list[str]] = {"brand_choice": [], "size_choice": [], "substitute_offer": ["haan chalega"],
                                     "confirm": [YES[reg]], "cancel_confirm": ["haan"], "done": [DONE[reg]]}
    gold: dict[str, float] = {}
    turns: list[str] = []
    n_items = 1 if kind in ("simple", "fraction") else 3 if kind == "multi" else 2
    clear_items = [i for i in ITEMS if i[0] not in ("fortune-atta",)]
    chosen = rng.sample(clear_items, n_items)
    if kind == "brand_clash":
        chosen[0] = rng.choice([i for i in ITEMS if i[3] and i[0] not in ("maggi-70",)])
    if kind == "descriptor":
        chosen[0] = BY_SKU["fortune-mustard-oil"]
    if kind == "fraction":
        chosen = [rng.choice([BY_SKU[s] for s in ("haldi", "jeera", "cheeni-loose", "aashirvaad-atta", "moong-dal")])]
        tags.append("fraction_qty")
    for j in range(1, len(chosen)):  # never repeat an item (or a same-generic sibling) in one order
        if chosen[j][0] == chosen[0][0] or (chosen[0][3] and chosen[j][3] == chosen[0][3]):
            chosen[j] = next(i for i in clear_items if i[0] not in {c[0] for c in chosen} and i[3] != chosen[0][3])
    parts = []
    for it in chosen:
        opts = qty_options(it[0])
        q = rng.choice([o for o in opts if o[0] not in (1, 2)] if kind == "fraction" else opts)
        spoken_name = (it[3], it[3]) if kind in ("brand_clash", "descriptor") and it is chosen[0] and it[3] else (it[1], it[2])
        if kind in ("brand_clash", "descriptor") and it is chosen[0]:
            answers["brand_choice"].append(it[4])
            tags.append("needs_clarification")
        parts.append(phrase(reg, q, spoken_name))
        gold[it[0]] = gold.get(it[0], 0) + q[0]
    turns.append(rng.choice(OPEN[reg]) + JOIN[reg].join(parts) + rng.choice(CLOSE[reg]))
    if kind == "correction":
        it = chosen[0]
        opts = [o for o in qty_options(it[0]) if abs(o[0] - gold[it[0]]) > 1e-9]
        q = rng.choice(opts)
        gold[it[0]] = q[0]
        name = it[2] if reg == "hindi" else it[1].split()[0]
        turns.append({"hinglish": f"nahi nahi, {name} {q[1]} kar do", "hindi": f"नहीं नहीं, {name} {q[2]} कर दीजिए",
                      "haryanvi": f"na na, {name} {q[1]} kar do"}[reg])
    if kind == "remove":
        it = chosen[-1]
        gold.pop(it[0])
        name = it[2] if reg == "hindi" else it[1]
        turns.append({"hinglish": f"{name} rehne do", "hindi": f"{name} हटा दीजिए", "haryanvi": f"{name} konya chahiye"}[reg])
        if reg == "haryanvi":
            tags.append("haryanvi_negation")
    if kind == "replace":
        old = chosen[-1]
        new = rng.choice([i for i in clear_items if i[0] not in gold and unit_of(i[0]) == unit_of(old[0])
                          and i[0] not in ("maggi-70",)] or [BY_SKU["cheeni-loose"]])
        qty_old = gold.pop(old[0])
        q_old_spoken = next((o for o in qty_options(old[0]) if abs(o[0] - qty_old) < 1e-9), None)
        if q_old_spoken:
            answers["qty"] = [q_old_spoken[2] if reg == "hindi" else q_old_spoken[1]]
        gold[new[0]] = qty_old if unit_of(new[0]) == unit_of(old[0]) else 1
        o_n = old[2] if reg == "hindi" else old[1]
        n_n = new[2] if reg == "hindi" else new[1]
        turns.append({"hinglish": f"{o_n} ki jagah {n_n} de do", "hindi": f"{o_n} की जगह {n_n} दे दीजिए",
                      "haryanvi": f"{o_n} ki jagah {n_n} de diyo"}[reg])
    if kind == "stockout":
        turns.insert(0, {"hinglish": "ek litre Patanjali sarson ka tel", "hindi": "एक लीटर पतंजलि सरसों का तेल",
                         "haryanvi": "ek litre Patanjali sarson ka tel chahiye"}[reg])
        gold["fortune-mustard-oil"] = gold.get("fortune-mustard-oil", 0) + 1
        tags.append("stockout")
    for sku in list(gold):
        if unit_of(sku) in ("pkt", "pc") and sku != "eggs":
            gold[sku] = round(gold[sku])
    voice = rng.choice(VOICES[reg])
    return {"id": f"{reg}-{idx + 1:03d}", "register": reg, "customer": "CUST-003", "voice": voice[0],
            "tts_language": voice[1], "tags": tags, "turns": turns, "answers": answers,
            "gold": sorted([[s, round(q, 3), unit_of(s)] for s, q in gold.items()]),
            "draft": reg == "haryanvi"}


def main(n_per_register: int = 30, seed: int = 7) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    count = 0
    for reg in ("hinglish", "hindi", "haryanvi"):
        for i in range(n_per_register):
            sc = make(reg, i, rng)
            (OUT / f"{sc['id']}.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1))
            count += 1
    # one repeat-order scenario per register (uses CUST-001 history)
    for reg, txt in (("hinglish", "pichli baar wala saaman bhej do"), ("hindi", "पिछली बार वाला सामान भेज दीजिए"),
                     ("haryanvi", "pichli baar aala saaman bhej diyo")):
        sc = {"id": f"{reg}-repeat", "register": reg, "customer": "CUST-001", "voice": VOICES[reg][0][0],
              "tts_language": VOICES[reg][0][1], "tags": ["repeat_order"], "turns": [txt],
              "answers": {"confirm": [YES[reg]], "done": [DONE[reg]]},
              "gold": [["amul-taaza", 2, "l"], ["good-day", 2, "pkt"]],
              "draft": reg == "haryanvi"}
        (OUT / f"{sc['id']}.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1))
        count += 1
    print(f"wrote {count} scenarios to {OUT}")


if __name__ == "__main__":
    main()
