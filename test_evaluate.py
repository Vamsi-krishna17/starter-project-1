import json
from decimal import Decimal

from evaluate import compare_fields, load_key, run_evaluation
from app.models import OrderResponse, Quote, QuoteLine

KEY = {"email_01": {"customer": "Acme", "delivery_city": "Pune", "deadline": None, "status": "Partial",
                    "issues": ["A: requested 5, only 2 in stock"],
                    "items": [{"product_name": "A", "product_code": "A-1", "requested": 5, "available": 2}],
                    "quote": {"lines": [{"product_code": "A-1", "quantity": 2, "unit_price_inr": 100, "discount_percent": 0, "line_total_inr": 200.0}],
                              "subtotal_inr": 200.0, "delivery_inr": 1500.0, "total_inr": 1700.0, "valid_until": "2026-11-27"}}}


def response(customer="Acme", total="1700.00"):
    q = Quote(lines=[QuoteLine(product_code="A-1", quantity=2, unit_price_inr=100, discount_percent=0, line_total_inr=Decimal("200.00"))],
              subtotal_inr=Decimal("200.00"), delivery_inr=Decimal("1500.00"), total_inr=Decimal(total), valid_until="2026-11-27")
    from app.models import ResponseItem
    return OrderResponse(customer=customer, delivery_city="Pune", deadline=None, status="Partial",
                         issues=["A: requested 5, only 2 in stock"], quote=q, reply="hello",
                         items=[ResponseItem(product_name="A", product_code="A-1", requested=5, available=2)])


def test_all_fields_match_and_quote_can_be_ignored(tmp_path):
    (tmp_path / "email_01.txt").write_text("x")
    rep = run_evaluation(tmp_path, KEY, lambda raw: response())
    assert rep["totals"]["pass"] == 1 and rep["totals"]["field_match_pct"] == 100.0
    ign = run_evaluation(tmp_path, KEY, lambda raw: response(), ignore={"quote"})
    assert all(not f["field"].startswith("quote") for f in ign["emails"][0]["fields"])


def test_mismatches_are_reported_field_by_field(tmp_path):
    (tmp_path / "email_01.txt").write_text("x")
    rep = run_evaluation(tmp_path, KEY, lambda raw: response(customer="Wrong", total="1650.00"))
    bad = {f["field"]: (f["expected"], f["actual"]) for f in rep["emails"][0]["fields"] if not f["match"]}
    assert bad["customer"] == ("Acme", "Wrong") and "quote.total_inr" in bad
    assert rep["totals"]["fail"] == 1 and rep["per_field"]["customer"] == {"compared": 1, "matched": 0}


def test_a_failing_email_is_recorded_and_the_rest_continue(tmp_path):
    (tmp_path / "email_01.txt").write_text("x"); (tmp_path / "email_02.txt").write_text("y")
    key = {**KEY, "email_02": KEY["email_01"]}

    def process(raw):
        if raw == "x":
            raise RuntimeError("boom")
        return response()

    rep = run_evaluation(tmp_path, key, process)
    assert [e["error"] is not None for e in rep["emails"]] == [True, False] and rep["totals"]["errors"] == 1


def test_files_without_an_answer_key_entry_are_skipped_and_the_key_loads(tmp_path):
    (tmp_path / "practice_01.txt").write_text("x")
    assert run_evaluation(tmp_path, KEY, lambda raw: response())["totals"]["emails"] == 0
    k = tmp_path / "k.json"; k.write_text(json.dumps({"email_01.txt": {"status": "Ready"}}))
    assert load_key(k) == {"email_01": {"status": "Ready"}}