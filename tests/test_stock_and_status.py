from app.models import NEED_INFO, PARTIAL, READY, OrderItem
from app.services.status_service import OrderStatusService
from app.services.stock_service import StockLine, StockService, is_short, normalize_name
from tests.fakes import FakeRepo

stock = StockService(FakeRepo())
status = OrderStatusService()


def test_normalize():
    assert normalize_name("  LAPTOP ") == "laptop"
    assert normalize_name("Nimbus   Tab  10") == "nimbus tab 10"


def test_exact_and_case_and_spaces():
    for name in ("Laptop", "LAPTOP", "  laptop  "):
        line = stock.check([OrderItem(product_name=name, requested=1)])[0]
        assert line.product_code == "LAP-101" and line.available == 6


def test_unknown_and_similar_names_do_not_match():
    for name in ("Hoverboard", "Laptops", "Nimbus Tab"):
        line = stock.check([OrderItem(product_name=name, requested=1)])[0]
        assert line.product_code is None and line.available is None


def test_is_short():
    assert is_short(10, 6)
    assert not is_short(6, 6)
    assert not is_short(None, 6)
    assert not is_short(5, None)


def L(code, req, avail, name="X"):
    return StockLine(name, code, req, avail)


def test_ready():
    assert status.decide([], [L("A", 2, 5)]) == (READY, [])


def test_partial():
    s, issues = status.decide([], [L("A", 10, 6, "Laptop")])
    assert s == PARTIAL and issues == ["Laptop: requested 10, only 6 in stock"]


def test_unknown_product_is_need_info():
    s, issues = status.decide([], [L(None, 2, None, "Ghost")])
    assert s == NEED_INFO and "not found" in issues[0]


def test_precedence_need_info_beats_partial_beats_ready():
    assert status.decide(["Delivery city is missing"], [L("A", 10, 6)])[0] == NEED_INFO
    assert status.decide([], [L(None, 1, None), L("A", 10, 6)])[0] == NEED_INFO
    assert status.decide([], [L("A", 10, 6), L("B", 1, 9)])[0] == PARTIAL
    assert status.decide([], [L("B", 1, 9)])[0] == READY


def test_need_info_still_lists_shortages():
    _, issues = status.decide(["Delivery city is missing"], [L("A", 10, 6, "Laptop")])
    assert len(issues) == 2
