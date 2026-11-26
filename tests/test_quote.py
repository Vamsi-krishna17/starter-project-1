from datetime import date
from decimal import Decimal

import pytest

from app.config import load_settings
from app.services.quote_service import QuoteConfig, QuoteService
from app.services.stock_service import StockLine

REF = date(2026, 11, 20)
svc = QuoteService()


def line(code="X", req=1, avail=1000, price=100):
    return StockLine("Item", code, req, avail, price)


@pytest.mark.parametrize("qty,pct", [(1, 0), (9, 0), (10, 5), (49, 5), (50, 8), (199, 8), (200, 12), (500, 12)])
def test_discount_tier_boundaries(qty, pct):
    assert svc.discount_percent(qty) == pct


def test_quoted_quantity_is_the_lower_of_requested_and_available():
    q = svc.build("Partial", [line("LAP-14", 30, 12, 52000)], REF)
    assert q.lines[0].quantity == 12 and q.lines[0].discount_percent == 5   # discount on the QUOTED quantity
    assert q.lines[0].line_total_inr == Decimal("592800.00")


def test_zero_available_line_is_not_quoted_and_no_lines_means_no_quote():
    assert svc.build("Partial", [line(req=5, avail=0)], REF) is None
    q = svc.build("Partial", [line("A", 5, 0), line("B", 2, 10, 1000)], REF)
    assert [l.product_code for l in q.lines] == ["B"]


def test_need_info_has_no_quote():
    assert svc.build("Need info", [line()], REF) is None


def test_delivery_on_either_side_of_the_threshold():
    below = svc.build("Ready", [line(req=1, price=49999)], REF)
    assert below.subtotal_inr == Decimal("49999.00") and below.delivery_inr == Decimal("1500.00")
    assert below.total_inr == Decimal("51499.00")
    at = svc.build("Ready", [line(req=1, price=50000)], REF)
    assert at.delivery_inr == Decimal("0.00") and at.total_inr == Decimal("50000.00")


def test_amounts_have_two_decimal_places_and_round_half_up():
    from app.services.quote_service import money
    assert money(Decimal("0.125")) == Decimal("0.13")      # half up
    assert money(Decimal("2.675")) == Decimal("2.68")      # float arithmetic would give 2.67
    q = svc.build("Ready", [line(req=10, avail=10, price=1001)], REF)   # 10 x 1001 x 95 / 100 = 9509.50
    assert q.lines[0].line_total_inr == Decimal("9509.50")
    assert q.lines[0].line_total_inr.as_tuple().exponent == -2


def test_matches_the_guide_example():
    lines = [line("LAP-14", 30, 12, 52000), line("CHG-65", 20, 200, 1499)]
    q = svc.build("Partial", lines, date(2026, 11, 20))
    assert [l.line_total_inr for l in q.lines] == [Decimal("592800.00"), Decimal("28481.00")]
    assert q.subtotal_inr == Decimal("621281.00") and q.delivery_inr == Decimal("0.00")
    assert q.total_inr == Decimal("621281.00")


def test_valid_until_is_header_date_plus_seven_days():
    assert svc.build("Ready", [line()], date(2026, 11, 20)).valid_until == "2026-11-27"


def test_amounts_are_json_numbers_not_strings():
    q = svc.build("Ready", [line(req=1, price=100)], REF)
    dumped = q.model_dump(mode="json")
    assert isinstance(dumped["total_inr"], float) and isinstance(dumped["lines"][0]["line_total_inr"], float)


def test_rules_come_from_config():
    s = load_settings()
    cfg = QuoteConfig.from_settings(s)
    assert cfg.tiers == ((1, 0), (10, 5), (50, 8), (200, 12)) and cfg.validity_days == 7
    assert cfg.free_delivery_threshold == Decimal("50000") and cfg.delivery_charge == Decimal("1500")
