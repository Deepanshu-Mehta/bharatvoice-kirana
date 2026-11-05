"""Text normalisation shared by the parser and resolver.

deva_to_roman: Devanagari -> lowercase Roman with Hindi schwa deletion (सरसों -> sarson, लीटर -> leetar).
key():         phonetic key that collapses spelling variants (Aashirvaad / ashirwad / आशीर्वाद -> asirvad).
skeleton():    consonant skeleton for coarse matching.
"""
from __future__ import annotations

import re
import unicodedata

_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
}
_NUKTA = {"क": "k", "ख": "kh", "ग": "g", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f", "य": "y"}
_PRECOMPOSED_NUKTA = {"क़": "क", "ख़": "ख", "ग़": "ग", "ज़": "ज", "ड़": "ड", "ढ़": "ढ", "फ़": "फ", "य़": "य"}
_VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai",
           "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e"}
_MATRAS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
           "ौ": "au", "ॉ": "o", "ॅ": "e"}
_VIRAMA, _NUKTA_SIGN = "्", "़"
_NASAL = {"ं": "n", "ँ": "n"}
_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_DEVA_RE = re.compile(r"[ऀ-ॿ]+")


def _deva_word(word: str) -> str:
    for pre, base in _PRECOMPOSED_NUKTA.items():
        word = word.replace(pre, base + _NUKTA_SIGN)
    # tokens: [text, kind] kind in {"C" consonant+schwa, "CV" consonant+vowel, "C0" bare consonant, "V", "N"}
    toks: list[list[str]] = []
    i = 0
    while i < len(word):
        ch = word[i]
        if ch in _CONS:
            base = _CONS[ch]
            j = i + 1
            if j < len(word) and word[j] == _NUKTA_SIGN:
                base = _NUKTA.get(ch, base)
                j += 1
            if j < len(word) and word[j] in _MATRAS:
                toks.append([base + _MATRAS[word[j]], "CV"])
                j += 1
            elif j < len(word) and word[j] == _VIRAMA:
                toks.append([base, "C0"])
                j += 1
            else:
                toks.append([base + "a", "C"])
            i = j
        elif ch in _VOWELS:
            toks.append([_VOWELS[ch], "V"])
            i += 1
        elif ch in _NASAL:
            toks.append([_NASAL[ch], "N"])
            i += 1
        elif ch == "ः":
            i += 1  # visarga: drop (अतः -> ata)
        else:
            i += 1
    # schwa deletion: final, then medial V C [a] C V (right to left)
    def has_vowel(t: list[str]) -> bool:
        return t[1] in ("CV", "V") or (t[1] == "C" and t[0].endswith("a"))

    cons_like = [k for k, t in enumerate(toks) if t[1] != "N"]
    if len(cons_like) > 1 and toks[cons_like[-1]][1] == "C":
        toks[cons_like[-1]][0] = toks[cons_like[-1]][0][:-1]
    for k in range(len(toks) - 2, 0, -1):
        t = toks[k]
        if t[1] != "C" or not t[0].endswith("a"):
            continue
        prev, nxt = toks[k - 1], toks[k + 1]
        if has_vowel(prev) and ((nxt[1] in ("CV", "C") and has_vowel(nxt)) or nxt[1] == "C0"):
            t[0] = t[0][:-1]
    return "".join(t[0] for t in toks)


def deva_to_roman(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(_DIGITS)
    return _DEVA_RE.sub(lambda m: _deva_word(m.group(0)), text)


_PUNCT_RE = re.compile(r"[^\w\s.]", re.UNICODE)


def to_roman(text: str) -> str:
    """Any mix of Devanagari / Roman -> lowercase Roman words, punctuation stripped (decimal points kept)."""
    t = deva_to_roman(text.replace("₹", " rupaye ")).lower()
    t = t.replace("&", " and ").replace("-", " ").replace("।", " ").replace("'", "")
    t = _PUNCT_RE.sub(" ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)  # keep 0.5, drop sentence dots
    t = re.sub(r"\b(\w{2,}?)([wv]aa?l(?:aa?|ii?|ee|e))\b", r"\1 \2", t)  # रिनवाला
    return re.sub(r"\s+", " ", t).strip()


_KEY_RULES = [
    (r"ph", "f"), (r"w", "v"), (r"z", "j"), (r"q", "k"), (r"x", "ks"), (r"ck", "k"),
    (r"chh", "ch"), (r"sh", "s"), (r"c(?=[eiy])", "s"), (r"c(?!h)", "k"),
    (r"aa", "a"), (r"ee", "i"), (r"ii", "i"), (r"oo", "u"), (r"uu", "u"), (r"ai", "e"), (r"au", "o"),
    (r"ei", "e"),  # mein / में (romanised "men"), dein / दें ("den"): Prisma writes Hindi words in Devanagari
    (r"iye\b", "ie"),  # chahiye / chahie / चाहिए -> chahie ; dijiye / दीजिए -> dijie
    (r"ay\b", "e"), (r"ey\b", "e"), (r"y\b", "i"),
]


def key_word(w: str) -> str:
    if w.isdigit() or re.fullmatch(r"\d+\.\d+", w):
        return w
    for pat, rep in _KEY_RULES:
        w = re.sub(pat, rep, w)
    w = re.sub(r"(.)\1+", r"\1", w)  # collapse doubles
    return w


def key(text: str) -> str:
    return " ".join(key_word(w) for w in to_roman(text).split())


def skeleton(text: str) -> str:
    out = []
    for w in key(text).split():
        w = re.sub(r"(?<=[kgcjtdpbsr])h", "", w)
        out.append(w[0] + re.sub(r"[aeiou]", "", w[1:]) if w else w)
    return " ".join(out)


# ---------------------------------------------------------------- Hindi number words
HINDI_NUMBERS_0_99 = (
    "shunya ek do teen chaar paanch chhah saat aath nau das gyaarah baarah terah chaudah pandrah solah satrah "
    "athaarah unnees bees ikkees baaees teyees chaubees pachchees chhabbees sattaaees atthaaees untees tees "
    "ikattees battees taintees chauntees paintees chhattees saintees adtees untaalees chaalees iktaalees "
    "bayaalees taintaalees chavaalees paintaalees chhiyaalees saintaalees adtaalees unchaas pachaas ikyaavan "
    "baavan tirpan chauvan pachpan chhappan sattaavan atthaavan unsath saath iksath baasath tirsath chausath "
    "painsath chhiyaasath sadsath adsath unhattar sattar ikhattar bahattar tihattar chauhattar pachhattar "
    "chhihattar satahattar athhattar unaasi assi ikyaasi bayaasi tiraasi chauraasi pachaasi chhiyaasi sataasi "
    "athaasi navaasi nabbe ikyaanave baanave tiraanave chauraanave pachaanave chhiyaanave sattaanave atthaanave "
    "ninyaanave"
).split()
assert len(HINDI_NUMBERS_0_99) == 100


def number_to_hindi(n: int) -> str:
    """Integer -> spoken Hindi in Roman script, the way prices are said (250 -> 'do sau pachaas')."""
    if n < 100:
        return HINDI_NUMBERS_0_99[n]
    parts = []
    if n >= 100000:
        parts.append(number_to_hindi(n // 100000) + " laakh")
        n %= 100000
    if n >= 1000:
        parts.append(number_to_hindi(n // 1000) + " hazaar")
        n %= 1000
    if n >= 100:
        parts.append(HINDI_NUMBERS_0_99[n // 100] + " sau")
        n %= 100
    if n:
        parts.append(HINDI_NUMBERS_0_99[n])
    return " ".join(parts)


def rupees_to_hindi(amount: float) -> str:
    rs = int(round(amount))
    return f"{number_to_hindi(rs)} rupaye"
