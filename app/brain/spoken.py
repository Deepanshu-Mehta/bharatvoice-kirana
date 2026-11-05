"""Spoken script for Timbre: Devanagari for Hindi/Haryanvi words, Latin for brands and true English words.

Why: our reply templates are Roman Hinglish. Timbre reads all-Latin text with an English profile, which sounds
anglicised ("AI-ish"); its docs detect Hinglish from MIXED script. In the voice lab (infra/voice_lab.py) the mixed
script also nearly halved phone-line transcription error of the shop's voice (CER 0.148 -> ~0.085).
The display text (parchi, transcript) stays Roman; only what Timbre speaks changes.
"""
from __future__ import annotations

import re

from app.brain.text import HINDI_NUMBERS_0_99

_NUM_DEVA = ("शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस "
             "इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस "
             "सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास "
             "इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ "
             "अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उन्यासी अस्सी इक्यासी "
             "बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे पचानवे छियानवे "
             "सत्तानवे अट्ठानवे निन्यानवे").split()
assert len(_NUM_DEVA) == 100

WORDS: dict[str, str] = dict(zip(HINDI_NUMBERS_0_99, _NUM_DEVA))
WORDS.update({
    # numbers, money, units, fractions
    "sau": "सौ", "hazaar": "हज़ार", "laakh": "लाख", "rupaye": "रुपये", "kilo": "किलो", "litre": "लीटर", "gram": "ग्राम",
    "packet": "पैकेट", "piece": "पीस", "darjan": "दर्जन", "aadha": "आधा", "aadhe": "आधे", "paav": "पाव", "sawa": "सवा",
    "saadhe": "साढ़े", "paune": "पौने", "dedh": "डेढ़", "dhai": "ढाई", "chhota": "छोटा", "bada": "बड़ा",
    # shop talk (templates)
    "aapka": "आपका", "aapke": "आपके", "aaya": "आया", "aayi": "आई", "ab": "अब", "abhi": "अभी", "anusaar": "अनुसार",
    "aur": "और", "awaaz": "आवाज़", "baaki": "बाकी", "baar": "बार", "baje": "बजे", "badalna": "बदलना", "bata": "बता",
    "bataiye": "बताइए", "bhaiya": "भैया", "bhej": "भेज", "bhejenge": "भेजेंगे", "bhejna": "भेजना", "bhejun": "भेजूँ",
    "bol": "बोल", "boliye": "बोलिए", "bolo": "बोलो", "chahiye": "चाहिए", "chal": "चल", "chalega": "चलेगा",
    "chalte": "चलते", "chuka": "चुका", "de": "दे", "dein": "दें", "dena": "देना", "denge": "देंगे", "deta": "देता",
    "dhanyavaad": "धन्यवाद", "dijiye": "दीजिए", "dila": "दिला", "diya": "दिया", "diye": "दिए", "dono": "दोनों",
    "doon": "दूँ", "dukaan": "दुकान", "dunga": "दूँगा", "gaya": "गया", "ghante": "घंटे", "haan": "हाँ", "hai": "है",
    "hain": "हैं", "hata": "हटा", "ho": "हो", "hoga": "होगा", "hone": "होने", "hoon": "हूँ", "hoti": "होती",
    "itna": "इतना", "jab": "जब", "jagah": "जगह", "jaunga": "जाऊँगा", "ji": "जी", "jitna": "जितना", "ka": "का",
    "kar": "कर", "karenge": "करेंगे", "karke": "करके", "karna": "करना", "karun": "करूँ", "kaun": "कौन", "ke": "के",
    "khaali": "खाली", "khaata": "खाता", "khaate": "खाते", "khatam": "ख़त्म", "khuli": "खुली", "ki": "की",
    "kijiye": "कीजिए", "kitna": "कितना", "kitne": "कितने", "ko": "को", "konsa": "कौनसा", "kripya": "कृपया",
    "kshama": "क्षमा", "kuch": "कुछ", "kul": "कुल", "kya": "क्या", "likh": "लिख", "likhta": "लिखता", "liya": "लिया",
    "maaf": "माफ़", "main": "मैं", "mein": "में", "mila": "मिला", "mile": "मिले", "naam": "नाम", "nahi": "नहीं",
    "namaste": "नमस्ते", "nivedan": "निवेदन", "pakka": "पक्का", "pehle": "पहले", "phir": "फिर", "pichhla": "पिछला",
    "pichhli": "पिछली", "pichla": "पिछला", "pichli": "पिछली", "poora": "पूरा", "raat": "रात", "radd": "रद्द",
    "raha": "रहा", "rahe": "रहे", "rahega": "रहेगा", "rehega": "रहेगा", "rehti": "रहती", "sa": "सा", "saaf": "साफ़",
    "saaman": "सामान", "saara": "सारा", "saath": "साथ", "sahayak": "सहायक", "samajh": "समझ", "se": "से",
    "seema": "सीमा", "subah": "सुबह", "suchi": "सूची", "suvidha": "सुविधा", "tak": "तक", "tarah": "तरह",
    "theek": "ठीक", "toh": "तो", "udhaar": "उधार", "uplabdh": "उपलब्ध", "vinamra": "विनम्र", "wahi": "वही",
    "wala": "वाला", "wale": "वाले", "woh": "वो", "ya": "या", "yaad": "याद", "yahi": "यही", "zyada": "ज़्यादा",
    "ram": "राम", "ne": "ने", "achha": "अच्छा", "pahunch": "पहुँच", "jayega": "जाएगा", "daam": "दाम", "kam": "कम",
    "yeh": "यह", "bas": "बस",
    # Haryanvi (DRAFT spellings, to be confirmed by native speakers with the rest of the lexicon)
    "aala": "आला", "du": "दूँ", "diyo": "दियो", "fer": "फेर", "ghana": "घणा", "had": "हद", "ib": "इब",
    "jyaga": "ज्यागा", "bhejange": "भेजांगे", "karange": "करांगे", "konya": "कोन्या", "rya": "र्या", "san": "सां",
    "su": "सूँ", "te": "तै", "thara": "थारा", "thare": "थारे",
    # generic words inside product names ("Aashirvaad Atta" -> "Aashirvaad आटा"); brands stay Latin
    "atta": "आटा", "cheeni": "चीनी", "ande": "अंडे", "haldi": "हल्दी", "jeera": "जीरा", "dal": "दाल", "chawal": "चावल",
    "milk": "दूध", "oil": "तेल", "mustard": "सरसों", "soap": "साबुन", "rajma": "राजमा", "kabuli": "काबुली",
    "chana": "चना", "maida": "मैदा", "sooji": "सूजी", "besan": "बेसन", "lal": "लाल", "mirch": "मिर्च",
    "dhaniya": "धनिया", "maachis": "माचिस", "agarbatti": "अगरबत्ती", "toor": "तूर", "moong": "मूंग",
    "masoor": "मसूर", "urad": "उड़द", "dahi": "दही", "paneer": "पनीर", "namak": "नमक", "tel": "तेल", "doodh": "दूध",
})
# English words that read naturally in Latin inside Hinglish speech: AI, assistant, call, cancel, cash, confirm,
# delivery, order, phone, total, UPI... are deliberately NOT in WORDS.

_WORD = re.compile(r"[A-Za-z']+")


_NUMVAL = {w: i for i, w in enumerate(HINDI_NUMBERS_0_99)}
_SCALE = {"sau": 100, "hazaar": 1000, "laakh": 100000}


def _money_to_digits(text: str) -> str:
    """'do sau solah rupaye' -> '₹216' (Timbre's text normaliser reads ₹ amounts naturally; also avoids the
    saath = 60 / saath = 'with' ambiguity)."""
    toks = text.split(" ")
    out, i = [], 0
    while i < len(toks):
        j, total, cur = i, 0, 0
        while j < len(toks) and (toks[j].lower() in _NUMVAL or toks[j].lower() in _SCALE):
            w = toks[j].lower()
            if w in _SCALE:
                total += (cur or 1) * _SCALE[w]
                cur = 0
            else:
                cur += _NUMVAL[w]
            j += 1
        if j > i and j < len(toks) and toks[j].lower().startswith("rupaye"):
            out.append(f"₹{total + cur}" + toks[j][len("rupaye"):])  # keep trailing punctuation
            i = j + 1
            continue
        out.append(toks[i])
        i += 1
    return " ".join(out)


def to_spoken(text: str) -> str:
    """Roman template text -> mixed-script text for Timbre (brands and English words stay Latin)."""
    text = _money_to_digits(text)
    words = dict(WORDS, saath="साथ")  # outside money, "saath" means "with"
    return _WORD.sub(lambda m: words.get(m.group(0).lower(), m.group(0)), text)


def add_pause_before_question(text: str) -> str:
    """Timbre v3.0: a short <pauses> before the closing question, as Gnani's docs recommend for prompts."""
    parts = re.split(r"(?<=[.!।])\s+", text.strip())
    if len(parts) > 1 and parts[-1].endswith("?"):
        parts[-1] = "<pauses> " + parts[-1]
    return " ".join(parts)
