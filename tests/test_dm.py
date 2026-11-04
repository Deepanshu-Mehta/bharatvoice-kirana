"""Text-mode conversation tests: the brain end to end, no audio, no API calls."""
import pytest

from app.brain.dm import Session
from app.brain.extract_rules import detect_register, extract


def talk(turns, cust="CUST-003"):
    s = Session(cust)
    s.greet()
    replies = [s.handle(t).reply for t in turns]
    return s, replies


def cart(s):
    return sorted((l["sku"], l["qty"], l["unit"]) for l in s.cart_snapshot())


def test_hinglish_order_with_clarification_and_correction():
    s, r = talk(["Bhaiya, do kilo atta aur ek litre Amul Taaza doodh bhej dena", "Aashirvaad wala",
                 "nahi nahi, doodh 2 litre kar do", "bas itna hi", "haan"])
    assert "Aashirvaad" in r[0] and "Pillsbury" in r[0]  # asked, did not guess
    assert cart(s) == [("aashirvaad-atta", 2, "kg"), ("amul-taaza", 2, "l")]
    assert s.order and s.order["total"] == 2 * 52 + 2 * 56
    assert "Confirm" in r[3]


def test_devanagari_codemix_transcript():
    s, _ = talk(["भैया, दो किलो Aashirvaad आता और एक लीटर Amul Taaza दूध भेज देना।"])
    assert cart(s) == [("aashirvaad-atta", 2, "kg"), ("amul-taaza", 1, "l")]


def test_no_order_without_readback_and_yes():
    s, r = talk(["do kilo aashirvaad atta", "haan"])
    assert s.order is None  # "haan" without a readback triggers the readback, not the order
    assert "Confirm" in r[1]
    s.handle("haan")
    assert s.order is not None


def test_idempotent_order():
    s, _ = talk(["ek kilo cheeni", "bas", "haan"])
    oid = s.order["order_id"]
    assert len(s.placed_orders) == 1 and oid.startswith("ORD-")


def test_size_question_and_fractions():
    s, r = talk(["ek packet maggi", "bada", "dhai sau gram haldi aur paav kilo jeera"])
    assert "chhota" in r[0] and "bada" in r[0]
    assert cart(s) == [("haldi", 0.25, "kg"), ("jeera", 0.25, "kg"), ("maggi-280", 1, "pkt")]


def test_stockout_substitution_with_price_difference():
    s, r = talk(["ek litre patanjali sarson ka tel", "haan chalega"])
    assert "khatam" in r[0] and "zyada" in r[0]
    assert cart(s) == [("fortune-mustard-oil", 1, "l")]


def test_replace_keeps_quantity():
    s, _ = talk(["do litre fortune sarson ka tel", "fortune ki jagah saffola de do"])
    assert cart(s) == [("saffola-gold", 2, "l")]


def test_remove_and_last_item_update():
    s, _ = talk(["do kilo cheeni aur ek kilo aashirvaad atta", "nahi, teen kilo", "cheeni hata do"])
    assert cart(s) == [("aashirvaad-atta", 3, "kg")]


def test_lone_do_is_two():
    s, _ = talk(["do lux aur teen lifebuoy"])
    assert cart(s) == [("lifebuoy", 3, "pc"), ("lux", 2, "pc")]


def test_dozen():
    s, r = talk(["ek darjan ande"])
    assert cart(s) == [("eggs", 12, "pc")] and "darjan" in r[0]


def test_repeat_last_order():
    s, r = talk(["pichli baar wala saaman bhej do", "haan"], cust="CUST-001")
    assert ("aashirvaad-atta", 5, "kg") in cart(s) and s.order


def test_handoff_after_two_unknown_items():
    s, r = talk(["do kilo xyzabc", "abcxyz do kilo"])
    assert s.handed_off and "bhaiya" in r[1].lower()


def test_handoff_on_request():
    s, _ = talk(["bhaiya se baat karao"])
    assert s.handed_off


def test_cancel_requires_confirmation():
    s, r = talk(["ek kilo cheeni", "sab cancel kar do"])
    assert cart(s) and "cancel" in r[1].lower()
    s.handle("haan")
    assert cart(s) == []


def test_correction_during_readback_reads_back_again():
    s, r = talk(["ek kilo cheeni", "bas", "nahi nahi, do kilo kar do"])
    assert cart(s) == [("cheeni-loose", 2, "kg")]
    assert "Confirm" in r[2] and s.order is None


def test_store_question():
    _, r = talk(["dukaan kitne baje tak khula hai"])
    assert "baje" in r[0]


def test_haryanvi_register_sticky_and_negation():
    s, r = talk(["Ram Ram! do kilo Aashirvaad atta chahiye", "ek darjan ande", "atta konya chahiye"], cust="CUST-002")
    assert all("ke chahiye" in x for x in r)
    assert cart(s) == [("eggs", 12, "pc")]


def test_prices_only_from_catalog():
    s, _ = talk(["teen kilo cheeni", "bas"])
    assert s.total == 3 * s.cat.get("cheeni-loose").price


@pytest.mark.parametrize("text,reg", [
    ("do kilo atta", "hinglish"),
    ("दो किलो आटा भेज दीजिए", "hindi"),
    ("दो किलो Aashirvaad आटा", "hinglish"),
    ("Ram Ram, mhara order likh lo", "haryanvi"),
])
def test_register(text, reg):
    assert detect_register(text) == reg


def test_extractor_frames():
    f = extract("अतः हटा दो उसकी जगह pillsberry दे दो।")
    assert [a.type for a in f.actions] == ["replace"]
    assert f.actions[0].product_phrase == "pillsberry"


def test_short_brands_not_in_static_keyterms():
    from app.stt_prisma import STTConfig, build_context
    terms = build_context(None, STTConfig(biasing="static"))["keyterms"]
    assert "Dove" not in terms and "Rin" not in terms and "Aashirvaad" in terms


def test_short_brand_added_when_in_play():
    from app.stt_prisma import STTConfig, build_context
    s = Session("CUST-003")
    s.handle("do dove soap")
    assert "Dove" in build_context(s, STTConfig(biasing="dynamic"))["keyterms"]


def test_correction_without_number_sets_not_adds():
    s, r = talk(["ek kilo moong dal", "नहीं नहीं, Moong Dal किलो कर दो।", "do kilo"])
    assert "kitna" in r[1]
    assert cart(s) == [("moong-dal", 2, "kg")]


def test_misheard_done_goes_to_readback_not_handoff():
    s, r = talk(["do litre amul taaza doodh", "जाद इतना है।"])
    assert "Confirm" in r[1] and not s.handed_off and s.order is None


def test_replace_with_misheard_target_uses_last_line():
    s, r = talk(["ek packet amul butter aur aadha kilo cheeni", "की नहीं? की जगह हल्दी दे दो।"])
    assert cart(s) == [("amul-butter-100", 1, "pkt"), ("haldi", 0.5, "kg")]


@pytest.mark.parametrize("utt", ["अंडे कौन या चाहिए?", "ande koina chahiye", "ande koniya chahiye"])
def test_konya_stt_variants_remove(utt):
    s, _ = talk(["ek darjan ande aur ek packet parle g", utt], cust="CUST-002")
    assert cart(s) == [("parle-g", 1, "pkt")]


@pytest.mark.parametrize("utt", ["बार इतना एक।", "पास इतना एक।", "bas itna e"])
def test_haryanvi_done_variants(utt):
    s, r = talk(["do kilo cheeni", utt])
    assert "Confirm" in r[1] or "Pakka" in r[1] or "pakka" in r[1]


def test_implausible_quantity_is_confirmed():
    s, r = talk(["पाँच किलो हल्दी", "nahi, paav kilo"])
    assert "pakka" in r[0].lower()
    assert cart(s) == [("haldi", 0.25, "kg")]


def test_implausible_quantity_yes_keeps_it():
    s, r = talk(["paanch kilo haldi", "haan"])
    assert cart(s) == [("haldi", 5, "kg")]


def test_konya_nasal_variant():
    s, _ = talk(["do packet amul butter aur ek packet ghadi", "Ghari detergent कौनियाँ चाहिए?"], cust="CUST-002")
    assert cart(s) == [("amul-butter-100", 2, "pkt")]


def test_english_or_and_double_quantity_split():
    s, _ = talk(["Ram Ram bhai. Do litre Fortune Sarson ka tel, or teen packet Ghadi detergent. Thik diya."])
    assert cart(s) == [("fortune-mustard-oil", 2, "l"), ("ghadi-1kg", 3, "pkt")]
    s, _ = talk(["do litre fortune sarson ka tel teen packet ghadi"])
    assert cart(s) == [("fortune-mustard-oil", 2, "l"), ("ghadi-1kg", 3, "pkt")]


def test_self_repair_not_split():
    s, _ = talk(["do kilo nahi teen kilo aashirvaad atta"])
    assert cart(s) == [("aashirvaad-atta", 3, "kg")]
