from app.brain.extract_hybrid import HybridExtractor
from app.brain.frame import Action, Frame


class FakeEvon:
    def __init__(self):
        self.calls = 0

    def __call__(self, t, ctx=None):
        self.calls += 1
        return Frame(source="evon", actions=[Action(type="add", product_phrase="cheeni", qty_text="ek kilo")])


def test_common_turn_stays_on_rules():
    ev = FakeEvon()
    f = HybridExtractor(ev)("do kilo aashirvaad atta aur ek litre amul taaza")
    assert f.source == "rules" and ev.calls == 0


def test_unparseable_turn_escalates():
    ev = FakeEvon()
    f = HybridExtractor(ev)("woh meethi safed cheez jo chai mein daalte hain ek kilo")
    assert ev.calls == 1 and f.source == "evon"
