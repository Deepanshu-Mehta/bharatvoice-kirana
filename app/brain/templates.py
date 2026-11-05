"""Register-aware reply templates. All transactional speech comes from here, never from the LLM.

Written in Roman script so one Timbre voice (Poorvi, language 'hi-en') can speak every register.
HARYANVI TEMPLATES ARE DRAFT until reviewed by native speakers (see data/haryanvi_lexicon.yaml).
"""
from __future__ import annotations

from app.brain.text import number_to_hindi, rupees_to_hindi

T: dict[str, dict[str, str]] = {
    # Persona: "Munshi ji", the shop's clerk who writes the parchi and the khata. Honest ("AI munshi"),
    # trusted, and greets regulars by name, which is what makes a shop on the phone sound human.
    "greet": {
        "hinglish": "Namaste ji! {shop} se, main dukaan ka AI munshi bol raha hoon. Boliye, kya bhejna hai?",
        "hindi": "Namaste ji! {shop} se, main dukaan ka AI munshi bol raha hoon. Bataiye, kya chahiye?",
        "haryanvi": "Ram Ram ji! {shop} te, main dukaan ka AI munshi bol rya su. Bolo, ke chahiye?",
    },
    "greet_known": {
        "hinglish": "Namaste {name}! {shop} se, main dukaan ka AI munshi bol raha hoon. Boliye, aaj kya bhejun?",
        "hindi": "Namaste {name}! {shop} se, main dukaan ka AI munshi bol raha hoon. Bataiye, aaj kya bhejna hai?",
        "haryanvi": "Ram Ram {name}! {shop} te, main dukaan ka AI munshi bol rya su. Bolo, aaj ke bhejun?",
    },
    "added": {
        "hinglish": "{item}, theek hai. Aur kuch?",
        "hindi": "{item}, likh liya. Aur kuch?",
        "haryanvi": "{item}, likh liya. Aur ke chahiye?",
    },
    "added_many": {
        "hinglish": "Theek hai, {items}. Aur kuch?",
        "hindi": "Theek hai, {items} likh liya. Aur kuch?",
        "haryanvi": "Theek se, {items}. Aur ke chahiye?",
    },
    "ask_two": {
        "hinglish": "{a} wala ya {b} wala?",
        "hindi": "{a} chahiye ya {b}?",
        "haryanvi": "{a} aala ya {b} aala?",
    },
    "ask_open": {
        "hinglish": "Kaun sa {generic} — {options}?",
        "hindi": "Kaun sa {generic} chahiye — {options}?",
        "haryanvi": "Konsa {generic} — {options}?",
    },
    "ask_size": {
        "hinglish": "{name} chhota packet ya bada?",
        "hindi": "{name} chhota packet chahiye ya bada?",
        "haryanvi": "{name} chhota packet ya bada?",
    },
    "ask_qty": {
        "hinglish": "{name} kitna bhejun?",
        "hindi": "{name} kitna chahiye?",
        "haryanvi": "{name} kitna bhejun?",
    },
    "ask_set_qty": {
        "hinglish": "{name} kitna kar doon?",
        "hindi": "{name} kitna kar doon?",
        "haryanvi": "{name} kitna kar doon?",
    },
    "confirm_big_qty": {
        "hinglish": "{item}? Itna zyada, pakka?",
        "hindi": "{item}? Itna zyada, pakka?",
        "haryanvi": "{item}? Itna ghana, pakka?",
    },
    "outbound_offer": {
        "hinglish": "Namaste {name}, {shop} se bol raha hoon. Aapka {lines} khatam hone wala hoga. Wahi bhej doon? Total {total}.",
        "hindi": "Namaste {name}, {shop} se bol rahe hain. Aapka {lines} khatam hone wala hoga. Kya wahi bhej dein? Kul {total}.",
        "haryanvi": "Ram Ram {name}, {shop} te bol rya su. Thara {lines} khatam hone aala hoga. Wahi bhej du? Total {total}.",
    },
    "usual_qty": {
        "hinglish": "{name}, wahi pichhli baar jitna, {qty}?",
        "hindi": "{name}, pichhli baar ki tarah {qty}?",
        "haryanvi": "{name}, wahi pichhli baar jitna, {qty}?",
    },
    "ask_unit": {
        "hinglish": "{name} kitne {unit}?",
        "hindi": "{name} kitne {unit} chahiye?",
        "haryanvi": "{name} kitne {unit}?",
    },
    "substitute": {
        "hinglish": "{a} khatam hai. {b} chalega? {diff}",
        "hindi": "{a} abhi khatam hai. {b} chalega? {diff}",
        "haryanvi": "{a} khatam ho rya se. {b} chal jyaga? {diff}",
    },
    "substitute_declined": {
        "hinglish": "Theek hai, {a} nahi bhejenge. Aur kuch?",
        "hindi": "Theek hai, {a} nahi bhejenge. Aur kuch?",
        "haryanvi": "Theek se, {a} konya bhejange. Aur ke chahiye?",
    },
    "not_understood_item": {
        "hinglish": "Maaf kijiye, {phrase} samajh nahi aaya. Product ka naam ek baar phir boliye?",
        "hindi": "Kshama kijiye, {phrase} samajh nahi aaya. Kripya naam phir se boliye.",
        "haryanvi": "Maaf karna, {phrase} samajh konya aaya. Ek baar fer bolo?",
    },
    "not_heard": {
        "hinglish": "Awaaz saaf nahi aayi, ek baar phir boliye?",
        "hindi": "Awaaz saaf nahi aayi, kripya phir se boliye.",
        "haryanvi": "Awaaz saaf konya aayi, ek baar fer bolo?",
    },
    "one_at_a_time": {
        "hinglish": "Ek-ek karke saaman boliye, main likhta jaunga.",
        "hindi": "Kripya ek-ek karke saaman bataiye.",
        "haryanvi": "Ek-ek karke saaman bolo, main likhta jaunga.",
    },
    "updated": {
        "hinglish": "{item} kar diya. Aur kuch?",
        "hindi": "{item} kar diya. Aur kuch?",
        "haryanvi": "{item} kar diya. Aur ke chahiye?",
    },
    "removed": {
        "hinglish": "{name} hata diya. Aur kuch?",
        "hindi": "{name} hata diya. Aur kuch?",
        "haryanvi": "{name} hata diya. Aur ke chahiye?",
    },
    "replaced": {
        "hinglish": "{old} ki jagah {item} kar diya. Aur kuch?",
        "hindi": "{old} ki jagah {item} kar diya. Aur kuch?",
        "haryanvi": "{old} ki jagah {item} kar diya. Aur ke chahiye?",
    },
    "nothing_to_change": {
        "hinglish": "Abhi list mein {phrase} nahi hai. Kya bhejna hai?",
        "hindi": "Abhi list mein {phrase} nahi hai. Kya chahiye?",
        "haryanvi": "List mein {phrase} konya se. Ke chahiye?",
    },
    "readback": {
        "hinglish": "Aapka order: {lines}. Total {total}. Confirm karun?",
        "hindi": "Aapka order hai: {lines}. Kul {total}. Confirm kar doon?",
        "haryanvi": "Thara order: {lines}. Total {total}. Pakka kar doon?",
    },
    "empty_cart": {
        "hinglish": "Abhi list khaali hai. Kya bhejna hai?",
        "hindi": "Abhi suchi khaali hai. Kya chahiye?",
        "haryanvi": "List khaali se. Ke chahiye?",
    },
    "what_to_change": {
        "hinglish": "Boliye, kya badalna hai?",
        "hindi": "Bataiye, kya badalna hai?",
        "haryanvi": "Bolo, ke badalna se?",
    },
    "placed": {
        "hinglish": "Order confirm! Total {total}. {eta} Dhanyavaad!",
        "hindi": "Order pakka ho gaya. Kul {total}. {eta} Dhanyavaad!",
        "haryanvi": "Order pakka! Total {total}. {eta} Ram Ram!",
    },
    "cancel_confirm": {
        "hinglish": "Poora order cancel kar doon?",
        "hindi": "Kya poora order cancel kar doon?",
        "haryanvi": "Saara order cancel kar doon?",
    },
    "cancelled": {
        "hinglish": "Order cancel kar diya. Kuch aur chahiye toh boliye.",
        "hindi": "Order radd kar diya. Kuch aur chahiye toh bataiye.",
        "haryanvi": "Order cancel kar diya. Kuch aur chahiye toh bolo.",
    },
    "not_cancelled": {
        "hinglish": "Theek hai, order wahi rehega. Aur kuch?",
        "hindi": "Theek hai, order wahi rahega. Aur kuch?",
        "haryanvi": "Theek se, order wahi rahega. Aur ke chahiye?",
    },
    "handoff": {
        "hinglish": "Main bhaiya ko bata deta hoon, woh do minute mein call karenge.",
        "hindi": "Main dukaan wale bhaiya ko bata deta hoon, woh do minute mein phone karenge.",
        "haryanvi": "Main bhaiya ne bata du su, woh do minute mein phone karange.",
    },
    "repeat_loaded": {
        "hinglish": "Pichli baar wala saaman: {lines}. Total {total}. Yahi bhejun?",
        "hindi": "Pichhli baar ka saaman: {lines}. Kul {total}. Yahi bhejun?",
        "haryanvi": "Pichhli baar aala saaman: {lines}. Total {total}. Yahi bhejun?",
    },
    "no_history": {
        "hinglish": "Pichla order nahi mila. Boliye, kya bhejna hai?",
        "hindi": "Pichhla order nahi mila. Bataiye, kya chahiye?",
        "haryanvi": "Pichhla order konya mila. Bolo, ke chahiye?",
    },
    "store_info": {
        "hinglish": "Dukaan subah aath se raat das baje tak khuli hai. Home delivery aadhe ghante mein, UPI aur cash dono chalte hain.",
        "hindi": "Dukaan subah aath se raat das baje tak khuli rehti hai. Delivery aadhe ghante mein hoti hai.",
        "haryanvi": "Dukaan subah aath te raat das baje tak khuli se. Delivery aadhe ghante mein.",
    },
    "price_info": {
        "hinglish": "{name} {price} {unit} hai. Kitna bhejun?",
        "hindi": "{name} {price} {unit} hai. Kitna chahiye?",
        "haryanvi": "{name} {price} {unit} se. Kitna bhejun?",
    },
    "price_two": {
        "hinglish": "{a} {pa}, {b} {pb} {unit}. Kaun sa bhejun?",
        "hindi": "{a} {pa}, aur {b} {pb} {unit}. Kaun sa chahiye?",
        "haryanvi": "{a} {pa}, {b} {pb} {unit}. Konsa bhejun?",
    },
    "stock_yes": {
        "hinglish": "Haan, {name} hai. Kitna bhejun?",
        "hindi": "Ji haan, {name} uplabdh hai. Kitna chahiye?",
        "haryanvi": "Haan, {name} se. Kitna bhejun?",
    },
    "money_qty": {
        "hinglish": "{money} ka {item}",
        "hindi": "{money} ka {item}",
        "haryanvi": "{money} ka {item}",
    },
    "udhaar_noted": {
        "hinglish": "Theek hai, udhaar mein likh dunga.",
        "hindi": "Theek hai, khaate mein likh denge.",
        "haryanvi": "Theek se, udhaar mein likh dunga.",
    },
    "udhaar_over_limit": {
        "hinglish": "Maaf kijiye, udhaar ki seema {limit} hai, abhi {balance} baaki hai. Cash ya UPI chalega?",
        "hindi": "Kshama kijiye, udhaar ki seema {limit} hai aur {balance} pehle se baaki hai. Cash ya UPI se dena hoga.",
        "haryanvi": "Maaf karna, udhaar ki had {limit} se, {balance} pehle te baaki se. Cash ya UPI chal jyaga?",
    },
    "udhaar_none": {
        "hinglish": "Maaf kijiye, abhi aapka khaata nahi hai. Cash ya UPI chalega?",
        "hindi": "Kshama kijiye, aapka khaata nahi hai. Cash ya UPI se dena hoga.",
        "haryanvi": "Maaf karna, thara khaata konya se. Cash ya UPI chal jyaga?",
    },
    "balance_info": {
        "hinglish": "Aapka {balance} baaki hai. Aur kuch?",
        "hindi": "Aapke khaate mein {balance} baaki hain. Aur kuch?",
        "haryanvi": "Thare {balance} baaki se. Aur ke chahiye?",
    },
    "placed_udhaar": {
        "hinglish": "Order confirm! {total} udhaar mein likh diya, ab kul baaki {balance}. {eta} Dhanyavaad!",
        "hindi": "Order pakka. {total} khaate mein likh diye, ab kul baaki {balance}. {eta} Dhanyavaad!",
        "haryanvi": "Order pakka! {total} udhaar mein likh diya, ib kul baaki {balance}. {eta} Ram Ram!",
    },
    "reminder": {
        "hinglish": "Namaste {name}, {shop} se yaad dila rahe hain, aapka {balance} baaki hai. Jab suvidha ho, UPI ya cash se de dijiye. Dhanyavaad!",
        "hindi": "Namaste {name}, {shop} se vinamra nivedan hai, aapke khaate mein {balance} baaki hain. Suvidha anusaar chuka dijiye. Dhanyavaad!",
        "haryanvi": "Ram Ram {name}, {shop} te yaad dila rahe san, thare {balance} baaki se. Jab time mile, UPI ya cash te de diyo. Ram Ram!",
    },
    "filler": {"hinglish": "Haan ji.", "hindi": "Ji.", "haryanvi": "Haan ji."},
}

UNIT_WORDS = {"kg": "kilo", "l": "litre", "pkt": "packet", "pc": "piece"}
FRACTION_WORDS = {0.25: "paav", 0.5: "aadha", 0.75: "paune ek", 1.25: "sawa", 1.5: "dedh", 2.5: "dhai"}


def speak_qty(qty: float, unit: str) -> str:
    u = UNIT_WORDS.get(unit, unit)
    if unit == "pc" and qty >= 12 and qty % 12 == 0:
        return f"{number_to_hindi(int(qty // 12))} darjan"
    if unit == "pc" and qty == 6:
        return "aadha darjan"
    if unit == "kg" and qty < 1 and abs(qty * 1000 - round(qty * 1000)) < 1e-6 and qty not in FRACTION_WORDS:
        return f"{number_to_hindi(int(round(qty * 1000)))} gram"
    if unit == "l" and qty < 1 and qty not in FRACTION_WORDS:
        return f"{number_to_hindi(int(round(qty * 1000)))} ml"
    if qty in FRACTION_WORDS:
        return f"{FRACTION_WORDS[qty]} {u}"
    whole, frac = int(qty), round(qty - int(qty), 2)
    if frac == 0:
        return f"{number_to_hindi(whole)} {u}"
    if frac == 0.5:
        return f"saadhe {number_to_hindi(whole)} {u}"
    if frac == 0.25:
        return f"sawa {number_to_hindi(whole)} {u}"
    if frac == 0.75:
        return f"paune {number_to_hindi(whole + 1)} {u}"
    return f"{qty:g} {u}"


def speak_money(amount: float) -> str:
    return rupees_to_hindi(amount)


VARIANTS = {  # people don't say the identical sentence every turn
    "added": {
        "hinglish": ["{item}, theek hai. Aur kuch?", "Achha ji, {item}. Aur kuch?", "Haan ji, {item} likh liya. Aur kuch?"],
        "hindi": ["{item}, likh liya. Aur kuch?", "Ji, {item}. Aur kuch?", "Theek hai, {item} likh liya. Aur kuch?"],
        "haryanvi": ["{item}, likh liya. Aur ke chahiye?", "Haan ji, {item}. Aur ke chahiye?", "Theek se, {item} likh liya. Aur ke chahiye?"],
    },
}


def render(template: str, register: str, variant: int = 0, **kw) -> str:
    if template in VARIANTS:
        opts = VARIANTS[template].get(register) or VARIANTS[template]["hinglish"]
        tmpl = opts[variant % len(opts)]
    else:
        table = T[template]
        tmpl = table.get(register) or table["hinglish"]
    return tmpl.format(**kw).replace("  ", " ").strip()
