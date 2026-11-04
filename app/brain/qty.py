"""Rule-based quantity / unit parser for spoken Hindi, Hinglish (and, after native review, Haryanvi).

Handles: digits ("2", "0.5", "२"), number words (ek .. ninyaanave, sau, hazaar), fractions
(aadha, paav, dedh, dhai), modifiers (sawa +0.25, saadhe +0.5, paune -0.25), "dhai sau gram",
"ek darjan", and the "do" ambiguity ("do kilo" = 2, "de do" = give).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.brain.text import HINDI_NUMBERS_0_99, key, key_word


def _keys(*spellings: str) -> set[str]:
    return {key_word(s) for s in spellings}


NUM_WORDS: dict[str, float] = {}
for _n, _w in enumerate(HINDI_NUMBERS_0_99):
    NUM_WORDS[key_word(_w)] = float(_n)
for _w, _n in {
    "ik": 1, "tin": 3, "char": 4, "chaar": 4, "panch": 5, "chah": 6, "chheh": 6, "sat": 7,
    "ath": 8, "das": 10, "dus": 10, "gyara": 11, "bara": 12, "pandra": 15, "bis": 20, "pachis": 25, "pachees": 25,
    "tis": 30, "chalis": 40, "pachas": 50, "sattar": 70, "assi": 80, "nabbe": 90,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "twentyfive": 25, "thirty": 30, "forty": 40, "fifty": 50,
}.items():
    NUM_WORDS[key_word(_w)] = float(_n)
NUM_WORDS.pop(key_word("shunya"), None)
NUM_WORDS.pop(key_word("chhe"), None)  # key "che" == key("chai"): "chai patti" must never mean 6
NUM_WORDS.pop(key_word("nau"), None)  # "nau" collides with "no"/"now" in Hinglish; accept digit 9 or "nau" only before a unit
NAU = key_word("nau")

FRACTIONS = {**{k: 0.5 for k in _keys("aadha", "adha", "aadhaa", "aadhi", "adhi", "half")},
             **{k: 0.25 for k in _keys("paav", "pav", "paao", "pao", "paw", "quarter")},
             **{k: 1.5 for k in _keys("dedh", "der", "dhedh", "derh")},
             **{k: 2.5 for k in _keys("dhai", "dhaai", "dhaee", "dhayi", "adhai", "arhai")}}
MODIFIERS = {**{k: 0.25 for k in _keys("sawa", "sava", "savaa")},
             **{k: 0.5 for k in _keys("saadhe", "sadhe", "saade", "sade", "sarhe", "saarhe")},
             **{k: -0.25 for k in _keys("paune", "pone", "paun")}}
MULTIPLIERS = {**{k: 100.0 for k in _keys("sau", "so", "saw", "hundred")},
               **{k: 1000.0 for k in _keys("hazaar", "hajar", "hazar", "thousand")}}

UNITS: dict[str, str] = {}
for _u, _sp in {
    "kg": ("kilo", "kg", "kgs", "kilogram", "kilos", "kilograms", "किलो", "केजी"),
    "g": ("gram", "grams", "gm", "gms", "g", "gr", "ग्राम"),
    "l": ("litre", "liter", "litres", "liters", "ltr", "litar", "l", "लीटर", "लिटर", "लिट्रे"),
    "ml": ("ml", "mili", "millilitre", "milliliter", "एमएल"),
    "pkt": ("packet", "packets", "pkt", "pkts", "pack", "paket", "pakit", "dabba", "dibba", "dabbe", "dibbe",
            "पैकेट", "पेकेट", "डब्बा", "डिब्बा", "डब्बे"),
    "pc": ("piece", "pieces", "pc", "pcs", "nag", "bottle", "bottles", "botal", "pouch", "bar", "tikki",
           "पीस", "बोतल", "नग", "टिक्की"),
    "dozen": ("darjan", "darjen", "dozen", "darzan", "दर्जन"),
}.items():
    for s in _sp:
        UNITS[key(s)] = _u

# words that make a following/preceding "do" a verb ("de do", "kar do", "bhej do")
VERB_STEMS = _keys("de", "kar", "bhej", "rakh", "hata", "la", "dikha", "bata", "bol", "likh", "jod", "chhod",
                   "mila", "laga", "bhijwa", "pahuncha", "pack", "tol", "nikal", "bana", "cancel", "confirm", "dal",
                   "daal", "le", "hatao", "batao", "करो", "दे", "कर", "भेज", "हटा", "रख")
DO = key_word("do")


@dataclass
class Qty:
    value: float
    unit: str | None  # kg | g | l | ml | pkt | pc | None (unspecified)
    certainty: float
    start: int = 0  # token span in the parsed token list
    end: int = 0

    def normalized(self) -> tuple[float, str | None]:
        """g->kg, ml->l, dozen->pc."""
        if self.unit == "g":
            return self.value / 1000.0, "kg"
        if self.unit == "ml":
            return self.value / 1000.0, "l"
        if self.unit == "dozen":
            return self.value * 12.0, "pc"
        return self.value, self.unit


def _num(tok: str) -> float | None:
    try:
        return float(tok)
    except ValueError:
        return NUM_WORDS.get(tok)


def find_quantities(tokens: list[str]) -> list[Qty]:
    """Scan key-normalised tokens; return every quantity span found (left to right)."""
    out: list[Qty] = []
    i, n = 0, len(tokens)
    while i < n:
        start = i
        tok = tokens[i]
        mod = 0.0
        val: float | None = None
        certainty = 1.0
        if tok in MODIFIERS:
            mod = MODIFIERS[tok]
            i += 1
            tok = tokens[i] if i < n else ""
            if tok in UNITS:  # "sawa kilo", "paune litre" -> 1 + mod
                val = 1.0
        if val is None:
            if tok in FRACTIONS:
                val = FRACTIONS[tok]
                certainty = 0.95
                i += 1
            elif tok in MULTIPLIERS and i + 1 < n and tokens[i + 1] in UNITS:  # "sau gram" = 100 g
                val = MULTIPLIERS[tok]
                i += 1
            else:
                v = _num(tok)
                if tok == NAU and i + 1 < n and tokens[i + 1] in UNITS:
                    v = 9.0
                if v is not None:
                    if tok == DO:
                        prev = tokens[i - 1] if i > 0 else ""
                        nxt = tokens[i + 1] if i + 1 < n else ""
                        if prev in VERB_STEMS or nxt == "" or nxt in VERB_STEMS:
                            i = start + 1  # verb "do", not two
                            continue
                        certainty = 1.0 if nxt in UNITS else 0.8
                    val = v
                    i += 1
            if val is not None and i < n and tokens[i] in MULTIPLIERS:
                val *= MULTIPLIERS[tokens[i]]
                i += 1
        elif i < n and tokens[i] in UNITS:
            pass  # modifier+unit, unit consumed below
        if val is None:
            i = start + 1
            continue
        if mod:
            val = val + mod
        unit = None
        if i < n and tokens[i] in UNITS:
            unit = UNITS[tokens[i]]
            i += 1
        elif certainty == 1.0:
            certainty = 0.9
        out.append(Qty(val, unit, certainty, start, i))
    return out


def tokenize(text: str) -> list[str]:
    return key(text).split()


def parse_qty(text: str) -> Qty | None:
    """Parse a quantity phrase like 'saadhe teen kilo'. Returns the LAST quantity (self-repair wins)."""
    toks = tokenize(text)
    if toks == [DO]:  # an isolated quantity field "do" means two (the verb never reaches here alone)
        return Qty(2.0, None, 0.8, 0, 1)
    qs = find_quantities(toks)
    return qs[-1] if qs else None
