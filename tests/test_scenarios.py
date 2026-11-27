"""End-to-end scenarios through OrderFacade (fake LLM + fake catalogue, no Azure, no database)."""
from datetime import date

import pytest

import app.services.order_facade as facade_module
from app.models import OrderItem
from app.services.order_facade import OrderFacade
from app.services.status_service import OrderStatusService
from app.services.stock_service import StockService
from app.services.validation_service import OrderValidationService
from tests.fakes import FakeAdapter, FakeRepo, order

WITH_DATE = "Subject: Order\nDate: Wed, 18 Nov 2026 09:00:00 +0530\n\nplease supply"
NO_DATE = "Subject: Order\n\nplease supply"          # no Date header
BAD_DATE = "Subject: Order\nDate: not a date\n\nplease supply"


def run(extracted, raw=WITH_DATE):
    facade = OrderFacade(FakeAdapter(extracted), OrderValidationService(3), StockService(FakeRepo()), OrderStatusService())
    return facade.process_email(raw)


def laptops(qty):  # fake catalogue: Laptop has 6 in stock
    return [OrderItem(product_name="Laptop", requested=qty)]


@pytest.fixture
def fixed_today(monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 18)

    monkeypatch.setattr(facade_module, "date", FakeDate)


# ---- date missing / invalid ----
def test_missing_date_header_falls_back_and_still_works(fixed_today):
    resp = run(order(deadline="2026-11-25", items=laptops(2)), NO_DATE)
    assert resp.status == "Ready" and resp.deadline == "2026-11-25"


def test_missing_date_header_deadline_too_soon_is_need_info(fixed_today):
    resp = run(order(deadline="2026-11-19", items=laptops(2)), NO_DATE)
    assert resp.status == "Need info" and resp.deadline is None


def test_invalid_date_header_does_not_crash(fixed_today):
    assert run(order(items=laptops(2)), BAD_DATE).status == "Ready"


def test_missing_deadline_is_allowed():
    resp = run(order(deadline=None, items=laptops(2)))
    assert resp.status == "Ready" and resp.deadline is None


# ---- quantity ----
def test_missing_quantity_is_need_info():
    resp = run(order(items=laptops(None)))
    assert resp.status == "Need info" and resp.items[0].requested is None
    assert any("quantity" in i for i in resp.issues)


@pytest.mark.parametrize("qty", [0, -3])
def test_zero_or_negative_quantity_is_need_info(qty):
    resp = run(order(items=laptops(qty)))
    assert resp.status == "Need info" and resp.items[0].requested is None


@pytest.mark.parametrize("qty,status", [(1, "Ready"), (5, "Ready"), (6, "Ready"), (7, "Partial"), (500, "Partial")])
def test_low_exact_and_too_high_quantity(qty, status):
    resp = run(order(items=laptops(qty)))
    assert resp.status == status
    assert resp.items[0].available == 6
    if status == "Partial":
        assert resp.issues == [f"Laptop: requested {qty}, only 6 in stock"]
    else:
        assert resp.issues == []


# ---- names and address ----
def test_missing_customer_name_is_need_info():
    resp = run(order(customer=None, items=laptops(2)))
    assert resp.status == "Need info" and resp.customer is None
    assert any("Customer" in i for i in resp.issues)


def test_missing_product_name_is_need_info():
    resp = run(order(items=[OrderItem(product_name=None, requested=2)]))
    assert resp.status == "Need info" and resp.items == []
    assert any("no product name" in i for i in resp.issues)


def test_unknown_product_name_is_need_info():
    resp = run(order(items=[OrderItem(product_name="Hoverboard", requested=2)]))
    assert resp.status == "Need info"
    assert resp.items[0].product_code is None and resp.items[0].available is None


def test_missing_delivery_city_is_need_info():
    resp = run(order(city=None, items=laptops(2)))
    assert resp.status == "Need info" and resp.delivery_city is None
    assert any("city" in i.lower() for i in resp.issues)


def test_everything_missing_lists_every_issue():
    resp = run(order(customer=None, city=None, deadline=None, items=[]))
    assert resp.status == "Need info" and len(resp.issues) == 3
