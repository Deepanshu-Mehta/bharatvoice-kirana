import pytest

from app.brain.qty import find_quantities, parse_qty, tokenize

CASES = [
    # (text, value, unit)
    ("do kilo", 2, "kg"),
    ("2 kilo", 2, "kg"),
    ("२ किलो", 2, "kg"),
    ("दो किलो", 2, "kg"),
    ("ek kilo", 1, "kg"),
    ("teen kg", 3, "kg"),
    ("chaar packet", 4, "pkt"),
    ("paanch kilo", 5, "kg"),
    ("panch kilo", 5, "kg"),
    ("पाँच किलो", 5, "kg"),
    ("das kilo", 10, "kg"),
    ("bees packet", 20, "pkt"),
    ("pachaas gram", 50, "g"),
    ("aadha kilo", 0.5, "kg"),
    ("आधा किलो", 0.5, "kg"),
    ("adha litre", 0.5, "l"),
    ("paav kilo", 0.25, "kg"),
    ("पाव किलो", 0.25, "kg"),
    ("dedh kilo", 1.5, "kg"),
    ("डेढ़ किलो", 1.5, "kg"),
    ("dhai kilo", 2.5, "kg"),
    ("ढाई किलो", 2.5, "kg"),
    ("sawa kilo", 1.25, "kg"),
    ("सवा किलो", 1.25, "kg"),
    ("sawa do kilo", 2.25, "kg"),
    ("saadhe teen kilo", 3.5, "kg"),
    ("साढ़े तीन किलो", 3.5, "kg"),
    ("paune do kilo", 1.75, "kg"),
    ("पौने दो किलो", 1.75, "kg"),
    ("paune kilo", 0.75, "kg"),
    ("dhai sau gram", 250, "g"),
    ("ढाई सौ ग्राम", 250, "g"),
    ("paanch sau gram", 500, "g"),
    ("dedh sau gram", 150, "g"),
    ("sau gram", 100, "g"),
    ("250 gram", 250, "g"),
    ("250 ग्राम", 250, "g"),
    ("0.5 किलो", 0.5, "kg"),
    ("1.5 litre", 1.5, "l"),
    ("ek litre", 1, "l"),
    ("एक लीटर", 1, "l"),
    ("do litre", 2, "l"),
    ("एक लिट्रे", 1, "l"),
    ("500 ml", 500, "ml"),
    ("ek darjan", 1, "dozen"),
    ("एक दर्जन", 1, "dozen"),
    ("aadha darjan", 0.5, "dozen"),
    ("teen dabba", 3, "pkt"),
    ("do bottle", 2, "pc"),
    ("ek packet", 1, "pkt"),
    ("एक पैकेट", 1, "pkt"),
    ("chhah piece", 6, "pc"),
    ("barah", 12, None),
    ("2", 2, None),
    ("teen", 3, None),
    ("nau kilo", 9, "kg"),
    ("pachchees kilo", 25, "kg"),
]


@pytest.mark.parametrize("text,value,unit", CASES)
def test_parse(text, value, unit):
    q = parse_qty(text)
    assert q is not None, text
    assert q.value == pytest.approx(value), text
    assert q.unit == unit, text


@pytest.mark.parametrize("text", ["de do", "kar do", "bhej do", "atta hata do", "haan kar do", "bas itna hi",
                                  "theek hai", "nau baje tak khula hai kya"])
def test_no_quantity(text):
    assert parse_qty(text) is None, text


def test_do_as_number_before_product():
    q = parse_qty("do maggi de do")
    assert q.value == 2 and q.unit is None


def test_self_repair_last_wins():
    assert parse_qty("do kilo nahi teen kilo").value == 3


def test_multiple_quantities_spans():
    toks = tokenize("do kilo atta aur ek litre doodh")
    qs = find_quantities(toks)
    assert [(q.value, q.unit) for q in qs] == [(2, "kg"), (1, "l")]
    assert toks[qs[0].start:qs[0].end] == ["do", "kilo"]


def test_normalized():
    assert parse_qty("dhai sau gram").normalized() == (0.25, "kg")
    assert parse_qty("ek darjan").normalized() == (12, "pc")
    assert parse_qty("500 ml").normalized() == (0.5, "l")


def test_certainty_lower_without_unit():
    assert parse_qty("do kilo").certainty > parse_qty("teen").certainty


@pytest.mark.parametrize("text", ["chai patti", "jo chai mein daalte hain", "चाय"])
def test_chai_is_not_six(text):
    assert parse_qty(text) is None


def test_six_still_parses():
    assert parse_qty("chhah packet").value == 6 and parse_qty("छह पैकेट").value == 6
