from datetime import date

import pytest

from app.models import OrderItem
from app.services.validation_service import OrderValidationService
from tests.fakes import order

REF = date(2026, 11, 18)
svc = OrderValidationService(lead_time_days=3)


def test_valid_order():
    r = svc.validate(order(), REF)
    assert r.passed and r.deadline == date(2026, 11, 25)


def test_missing_customer():
    assert not svc.validate(order(customer=None), REF).passed
    assert not svc.validate(order(customer="  "), REF).passed


def test_missing_city():
    assert not svc.validate(order(city=None), REF).passed


def test_no_items():
    assert not svc.validate(order(items=[]), REF).passed


@pytest.mark.parametrize("qty", [None, 0, -5])
def test_bad_quantities(qty):
    r = svc.validate(order(items=[OrderItem(product_name="Laptop", requested=qty)]), REF)
    assert any("quantity" in i for i in r.issues)


def test_no_deadline_is_fine():
    r = svc.validate(order(deadline=None), REF)
    assert r.passed and r.deadline is None


def test_invalid_deadline():
    assert not svc.validate(order(deadline="next-ish"), REF).passed


def test_deadline_lead_time_boundary():
    assert svc.validate(order(deadline="2026-11-21"), REF).passed       # exactly 3 days
    assert not svc.validate(order(deadline="2026-11-20"), REF).passed   # 2 days


def test_lead_time_is_configurable():
    assert not OrderValidationService(5).validate(order(deadline="2026-11-21"), REF).passed


def test_deadline_uses_reference_date_not_today():
    assert svc.validate(order(deadline="2020-01-10"), date(2020, 1, 1)).passed


def test_previous_order_phrases_are_detected_for_the_resolver():
    # Starter Project 2: repeat orders are handled by product resolution (order_history), not by validation.
    from app.services.validation_service import mentions_previous_order
    assert mentions_previous_order("Please send the same as last time.")
    assert not mentions_previous_order("We need 10 laptops.")
