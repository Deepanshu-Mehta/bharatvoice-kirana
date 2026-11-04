"""Rule-based extractor: transcript -> Frame. Same output as the Evon extractor.

Used as (1) the runtime fallback when Evon fails, (2) the 'rules only' ablation baseline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.brain.frame import Action, Frame
from app.brain.qty import find_quantities
from app.brain.text import key, to_roman

ROOT = Path(__file__).resolve().parents[2]


def _k(s: str) -> str:
    return key(s)


def _kset(*xs: str) -> set[str]:
    return {_k(x) for x in xs}


YES = _kset("haan", "han", "ha", "haanji", "hanji", "ji", "ji haan", "theek", "thik", "theek hai", "thik hai", "confirm",
            "kar do", "kardo", "karo", "ok", "okay", "done", "bilkul", "sahi", "sahi hai", "chalega", "chalo", "yes",
            "haan ji", "haan kar do", "haan confirm", "haan bhej do", "bhej do", "ho jayega", "pakka")
NO = _kset("nahi", "nahin", "na", "no", "mat", "ruko", "ruk", "wait", "galat", "nahi nahi", "rehne do", "konya")
DONE = _kset("bas", "bas itna", "bas itna hi", "itna hi", "aur kuch nahi", "nahi bas", "ho gaya", "bas ho gaya",
             "that's all", "thats all", "nothing else", "kuch nahi", "aur nahi", "itna kaafi", "bas yahi",
             "itna hai", "bas itna hai", "itna", "bas hai", "bas ji", "itna e", "bas itna e", "itna ek", "bas itna ek")
REMOVE_WORDS = _kset("hata", "hatao", "hata do", "nikal", "nikaal", "nikal do", "rehne do", "rahne do", "chhodo",
                     "chhod do", "cancel", "mat bhejo", "mat dena", "nahi chahiye", "nai chahiye", "galti se", "remove")
UPDATE_WORDS = _kset("kar do", "kardo", "karo", "kar dena", "kar dijiye", "kijiye", "badal do", "change")
REPLACE_WORDS = _kset("jagah", "badle", "instead", "bajaye")
REPAIR_WORDS = _kset("nahi", "nahin", "matlab", "sorry", "galti", "nahi nahi", "no")
HUMAN = ["bhaiya se baat", "bhai se baat", "insaan", "aadmi se baat", "kisi se baat", "malik se", "owner se",
         "human", "real person", "dukaan wale se", "bhaiya ko do", "seth ji se"]
CANCEL_ALL = ["sab cancel", "poora cancel", "pura cancel", "order cancel", "sab hata do", "saara cancel",
              "sara cancel", "kuch nahi chahiye", "cancel kar do sab"]
REPEAT = ["pichli baar", "pichhli baar", "pichle baar", "last time", "last baar", "wahi saaman", "wahi samaan",
          "monthly list", "har baar wala", "har mahine wala", "same as last", "jo pichli"]
STORE_Q = ["kitne baje", "khula", "band hota", "band hai", "delivery kab", "kitni der", "kitna time", "home delivery",
           "paytm", "upi", "address", "kahan ho", "online payment", "kab tak aayega", "kab aayega"]
FILLER = _kset("bhaiya", "bhai", "ji", "please", "plz", "ek", "aur", "bhi", "chahiye", "chaiye", "dena", "de", "do",
               "dedo", "de do", "bhej", "bhejo", "bhej do", "bhejna", "dijiye", "lena", "hai", "hain", "mujhe", "humko",
               "wala", "wali", "wale", "ka", "ki", "ke", "sath", "mein", "me", "kar", "karo", "kardo", "dena",
               "deejiye", "toh", "to", "wo", "woh", "jo", "haan", "acha", "accha", "theek", "achha", "bas", "hi",
               "uski", "uske", "iski", "iske", "jagah", "badle", "instead", "bajaye", "nahi", "nahin", "matlab",
               "sorry", "galti", "se", "bhi", "laga", "lagao", "rakh", "rakhna", "add", "plus", "ram", "tau",
               "suniye", "suno", "diyo", "de diyo", "dijiye", "deejiye", "dena ji", "namaste", "bhej diyo")
PRICE_Q = ["kitne ka", "kitne ki", "kitne ke", "kitne mein", "kya rate", "rate kya", "kya daam", "daam kya", "kya bhav",
           "bhav kya", "price kya", "kya price", "kitne ka hai", "kitne ki hai", "kitne rupaye ka", "kitne rupaye ki"]
STOCK_Q = ["hai kya", "hain kya", "milega", "milegi", "mil jayega", "mil jaayega", "available", "rakhte ho", "hoga kya",
           "aata hai kya", "se kya"]
UDHAAR = ["udhaar", "udhar", "khate mein", "khaate mein", "khata mein", "baad mein de dunga", "baad mein dunga",
          "hisaab mein likh", "khate me", "udhaar mein"]
BALANCE_Q = ["kitna baaki", "kitne baaki", "baaki kitna", "kitna udhaar", "mera hisaab", "kitna dena hai", "mera khaata",
             "mera khata", "hisaab batao", "kitna bakaya", "thare kitne", "kitne baaki se"]
SLOW = ["dheere", "dhire", "aaram se", "slowly", "slow"]
REPEAT_REQ = ["phir se boliye", "phir se bolo", "fir se boliye", "fir se bolo", "dobara boliye", "dobara bolo", "kya bola",
              "kya kaha", "repeat", "samajh nahi aaya", "ek baar aur"]
QUERY_FILLER = _kset("kitne", "kitna", "rate", "daam", "bhav", "price", "kya", "hai", "hain", "milega", "milegi",
                     "mil", "jayega", "jaayega", "available", "rakhte", "ho", "hoga", "aapke", "paas", "se")
QUERY_FILLER -= {key("atta")}  # never drop the product word itself ("aata hai kya" vs "atta")
SPLIT_RE = re.compile(r"\s*(?:,|\baur\b|\bor\b|\band\b|\bphir\b|\bsaath mein\b|\bsath me\b|\bplus\b)\s*")
KONYA_RE = re.compile(r"^ko(i)?n(i)?y?an?$")  # konya / koina / koniya / कौनियाँ (koniyan) after key()


@lru_cache(maxsize=1)
def haryanvi_lexicon() -> dict:
    p = ROOT / "data" / "haryanvi_lexicon.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def detect_register(transcript: str) -> str:
    lex = haryanvi_lexicon()
    r = " " + to_roman(transcript) + " "
    strong = sum(1 for m in lex.get("strong_markers", []) if f" {m} " in r)
    weak = sum(1 for m in lex.get("weak_markers", []) if f" {m} " in r)
    if strong >= 1 or weak >= 3:
        return "haryanvi"
    if re.search(r"[A-Za-z]", transcript) and re.search(r"[ऀ-ॿ]", transcript):
        return "hinglish"  # code-mixed script output from Prisma codemix
    if re.search(r"[ऀ-ॿ]", transcript):
        return "hindi"
    return "hinglish"


def _normalise_konya(roman: str) -> str:
    """Haryanvi negation 'konya' as Prisma writes it (कौन या / कोइना / कोनिया), matched on phonetic keys."""
    lex = haryanvi_lexicon()
    variants = sorted({key(v) for v in ["konya", *lex.get("konya_stt_variants", [])]}, key=len, reverse=True)
    k = " " + key(roman) + " "
    hit = False
    toks = k.split()
    for i, t in enumerate(toks):  # any konya-shaped token before "chahiye" (Prisma spells it many ways)
        if KONYA_RE.match(t) and i + 1 < len(toks) and toks[i + 1] == key("chahiye"):
            toks[i] = "nahi"
            hit = True
    k = " " + " ".join(toks) + " "
    for v in variants:
        if f" {v} {key('chahiye')} " in k or (len(v.split()) == 1 and f" {v} " in k and v != key("kaun")):
            k = k.replace(f" {v} ", " nahi ")
            hit = True
    return k.strip() if hit else roman


def _normalise_haryanvi(roman: str) -> str:
    for src, dst in (haryanvi_lexicon().get("normalise") or {}).items():
        roman = re.sub(rf"\b{re.escape(src)}\b", dst, roman)
    return roman


def _has_any(keys: list[str], phrases: set[str]) -> bool:
    joined = " " + " ".join(keys) + " "
    return any(f" {p} " in joined for p in phrases)


def _contains(roman: str, phrases: list[str]) -> bool:
    """Phrase match on phonetic keys, so Devanagari (पिछली बार) and Roman (pichli baar) both hit."""
    k = " " + key(roman) + " "
    return any(f" {key(p)} " in k or p in roman for p in phrases)


@dataclass
class Context:
    pending: str | None = None  # brand_choice | size_choice | substitute_offer | cancel_confirm | unit_choice
    awaiting_confirm: bool = False
    cart_phrases: list[str] = field(default_factory=list)
    last_agent_prompt: str = ""


@lru_cache(maxsize=1)
def _alias_ngrams() -> set[str]:
    """Multi-word catalog names ('good day', 'mother dairy') whose tokens must survive filler removal."""
    from app.brain.catalog import load_catalog
    out = set()
    for p in load_catalog().products:
        for k in p.alias_keys + p.desc_keys + p.generic_keys:
            if " " in k:
                out.add(k)
    return out


def _protected(tokens_key: list[str]) -> set[int]:
    prot: set[int] = set()
    ngrams = _alias_ngrams()
    for n in (2, 3):
        for i in range(len(tokens_key) - n + 1):
            if " ".join(tokens_key[i:i + n]) in ngrams:
                prot.update(range(i, i + n))
    return prot


def _product_words(tokens_roman: list[str], tokens_key: list[str], skip: set[int]) -> str:
    prot = _protected(tokens_key)
    out = [r for i, (r, k) in enumerate(zip(tokens_roman, tokens_key)) if i not in skip and (
        i in prot or (k not in FILLER and k not in REMOVE_WORDS and k not in UPDATE_WORDS))]
    return " ".join(out).strip()


def _segment_action(seg_roman: str) -> Action | None:
    toks_r = seg_roman.split()
    toks_k = [_k(t) for t in toks_r]
    if not toks_k:
        return None
    qs = find_quantities(toks_k)
    q_idx = {i for q in qs for i in range(q.start, q.end)}
    qty_text = " ".join(toks_r[qs[-1].start:qs[-1].end]) if qs else None  # self-repair: last quantity wins

    joined = " " + " ".join(toks_k) + " "
    # replace: "fortune ki jagah saffola"
    for w in REPLACE_WORDS:
        if f" {w} " in joined:
            i = toks_k.index(w)
            left = _product_words(toks_r[:i], toks_k[:i], {j for j in q_idx if j < i})
            right_skip = {j - i - 1 for j in q_idx if j > i}
            right = _product_words(toks_r[i + 1:], toks_k[i + 1:], right_skip)
            if right:  # target words lost to STT ("की नहीं की जगह हल्दी") -> the last item
                return Action(type="replace", target=left or "LAST", product_phrase=right, qty_text=qty_text)
    is_remove = _has_any(toks_k, REMOVE_WORDS) or (" konya " in joined)
    phrase = _product_words(toks_r, toks_k, q_idx)
    if is_remove and not qs:
        return Action(type="remove", target=phrase or "LAST")
    starts_with_no = toks_k[0] in NO
    is_update = _has_any(toks_k, UPDATE_WORDS) or starts_with_no
    if qs and is_update:
        return Action(type="update", target=phrase or "LAST", qty_text=qty_text)
    if is_update and starts_with_no and phrase:  # "nahi nahi, moong ... kar do" with the number lost by STT
        return Action(type="update", target=phrase, qty_text=None)
    if phrase or qs:
        return Action(type="add", product_phrase=phrase or None, qty_text=qty_text)
    return None


def _split_on_quantities(seg: str) -> list[str]:
    """'do litre fortune tel teen packet ghadi' -> two segments; keeps self-repair ('do kilo nahi teen kilo')."""
    toks = seg.split()
    keys = [_k(t) for t in toks]
    qs = find_quantities(keys)
    if len(qs) < 2:
        return [seg]
    cuts = []
    for prev, q in zip(qs, qs[1:]):
        between = keys[prev.end:q.start]
        content = [k for k in between if k not in FILLER]
        if content and not any(k in REPAIR_WORDS for k in between):  # product words between two quantities
            cuts.append(q.start)
    if not cuts:
        return [seg]
    parts, last = [], 0
    for c in cuts:
        parts.append(" ".join(toks[last:c]))
        last = c
    parts.append(" ".join(toks[last:]))
    return [p for p in parts if p.strip()]


def extract(transcript: str, ctx: Context | None = None) -> Frame:
    ctx = ctx or Context()
    register = detect_register(transcript)
    roman = to_roman(transcript)
    roman = _normalise_konya(roman)
    if register == "haryanvi":
        roman = _normalise_haryanvi(roman)
    keys = [_k(t) for t in roman.split()]
    kj = " ".join(keys)
    f = Frame(speech_register=register, source="rules")

    if not keys:
        return f
    if _contains(roman, HUMAN):
        f.actions.append(Action(type="ask_human"))
        return f
    if _contains(roman, CANCEL_ALL):
        f.actions.append(Action(type="cancel_all"))
        return f
    if _contains(roman, REPEAT):
        f.actions.append(Action(type="repeat_last_order"))
        return f
    if _contains(roman, STORE_Q):
        f.actions.append(Action(type="store_question", text=transcript))
        return f
    if _contains(roman, SLOW):
        f.actions.append(Action(type="slow_down"))
        return f
    if _contains(roman, REPEAT_REQ) and not ctx.pending:
        f.actions.append(Action(type="repeat"))
        return f
    if _contains(roman, BALANCE_Q):
        f.actions.append(Action(type="balance_query"))
        return f
    if _contains(roman, UDHAAR):
        f.actions.append(Action(type="udhaar"))
        if ctx.awaiting_confirm and keys and keys[0] in YES:
            f.actions.append(Action(type="confirm_yes"))
        return f
    qs_all = find_quantities(keys)
    if not ctx.pending and (_contains(roman, PRICE_Q) or (_contains(roman, STOCK_Q) and not qs_all)):
        toks = roman.split()
        tk = [_k(t) for t in toks]
        phrase = " ".join(t for t, k in zip(toks, tk) if k not in QUERY_FILLER and k not in FILLER)
        if phrase:
            f.actions.append(Action(type="price_query" if _contains(roman, PRICE_Q) else "stock_query",
                                    product_phrase=phrase))
            return f

    has_qty = bool(find_quantities(keys))
    short = len(keys) <= 4
    if ctx.pending:
        f.actions.append(Action(type="choose", choice_text=roman))
        return f
    if ctx.awaiting_confirm and short and not has_qty:
        if kj in YES or keys[0] in YES and not any(k in NO for k in keys):
            f.actions.append(Action(type="confirm_yes"))
            return f
        if keys[0] in NO and len(keys) <= 2:
            f.actions.append(Action(type="confirm_no"))
            return f
    itna_done = short and key("itna") in keys and not ctx.pending  # "bas itna e" -> "पास इतना एक" (the 'ek' is not a qty)
    if kj in DONE or itna_done or (short and not has_qty and any(f" {d} " in f" {kj} " for d in DONE)):
        f.actions.append(Action(type="done"))
        return f

    for seg in SPLIT_RE.split(roman):
        for sub in _split_on_quantities(seg):
            a = _segment_action(sub)
            if a:
                f.actions.append(a)
    if not f.actions and keys[0] in YES:  # bare "haan": DM reads back (and only places after a readback)
        f.actions.append(Action(type="confirm_yes"))
    return f
