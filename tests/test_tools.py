import json

from fastapi.testclient import TestClient

from app.api.orders import get_facade
from app.main import app
from app.models import OrderResponse
from email_to_json import email_to_json

# ---- raw email -> JSON ----
SAMPLE = """From: Sai Teja <procurement@example.com>
Date: Wed, 18 Nov 2026 11:35:00 +0530
Subject: 5G routers and headsets

Hi team,

- Meshwave 5G Home Router: 10
- Quietline Headset: 10

Delivery to Skyreach Broadband, Dwaraka Nagar, Visakhapatnam by 27 November 2026.

Sai Teja"""


def test_email_to_json_round_trip_and_format():
    out = email_to_json(SAMPLE)
    assert json.loads(out) == {"email": SAMPLE}
    assert "\\n\\nHi team,\\n\\n- Meshwave" in out and list(json.loads(out)) == ["email"]


def test_special_characters_and_line_endings_preserved():
    raw = 'He said "5G" \\ path\ttab, price \u20b91,499\r\nline2\r\n\r\n'
    assert json.loads(email_to_json(raw))["email"] == 'He said "5G" \\ path\ttab, price \u20b91,499\nline2'


def test_converted_json_is_accepted_by_the_api():
    class Rec:
        got = []

        def process_email(self, email):
            Rec.got.append(email)
            return OrderResponse(customer=None, delivery_city=None, deadline=None, items=[],
                                 status="Need info", issues=["x"], reply="hi")

    app.dependency_overrides[get_facade] = lambda: Rec()
    try:
        r = TestClient(app).post("/orders", content=email_to_json(SAMPLE), headers={"Content-Type": "application/json"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200 and Rec.got == [SAMPLE]
