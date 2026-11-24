from fastapi.testclient import TestClient

from app.api.orders import get_facade
from app.main import app
from app.models import OrderResponse

RAW = (
    "From: Arun Rao <arun@brightpathcoaching.example>\n"
    "Date: Sun, 18 Nov 2026 16:40:00 +0530\n"
    "Subject: tablets + earbuds\n\n"
    "we want 12 nimbus tab 8 lite and 10 PEBBLE BUDS.\n"
    "deliver to Brightpath Coaching Centre, Ameerpet, Hyderabad."
)


class RecordingFacade:
    """Stands in for OrderFacade; records the email it receives."""

    def __init__(self):
        self.received = []

    def process_email(self, email):
        self.received.append(email)
        return OrderResponse(
            customer="X", delivery_city="Y", deadline=None, items=[],
            status="Need info", issues=["x"], reply="hi",
        )


def make_client():
    facade = RecordingFacade()
    app.dependency_overrides[get_facade] = lambda: facade
    return TestClient(app), facade


def teardown_function():
    app.dependency_overrides.clear()


def test_plain_text_is_accepted():
    client, facade = make_client()
    r = client.post("/orders", content=RAW.encode(), headers={"Content-Type": "text/plain"})
    assert r.status_code == 200
    assert set(r.json()) == {"customer", "delivery_city", "deadline", "items", "status", "issues", "quote", "reply"}
    assert facade.received == [RAW]


def test_plain_text_with_charset_is_accepted():
    client, facade = make_client()
    r = client.post("/orders", content=RAW.encode(), headers={"Content-Type": "text/plain; charset=utf-8"})
    assert r.status_code == 200 and facade.received == [RAW]


def test_json_still_works():
    client, facade = make_client()
    r = client.post("/orders", json={"email": RAW})
    assert r.status_code == 200 and facade.received == [RAW]


def test_both_formats_reach_the_same_workflow_with_same_input():
    client, facade = make_client()
    client.post("/orders", content=RAW.encode(), headers={"Content-Type": "text/plain"})
    client.post("/orders", json={"email": RAW})
    assert facade.received[0] == facade.received[1] == RAW


def test_empty_and_blank_text_rejected():
    client, facade = make_client()
    for body in (b"", b"   \n  "):
        r = client.post("/orders", content=body, headers={"Content-Type": "text/plain"})
        assert r.status_code == 422
    assert facade.received == []


def test_invalid_json_rejected():
    client, facade = make_client()
    for body in (b"{not json", b'["email"]', b'{"email": 5}', b"{}", b'{"email": "  "}'):
        r = client.post("/orders", content=body, headers={"Content-Type": "application/json"})
        assert r.status_code == 422, body
    assert facade.received == []


def test_unsupported_content_type_rejected():
    client, facade = make_client()
    r = client.post("/orders", content=b"email=hi", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415 and facade.received == []


def test_swagger_documents_both_body_types():
    client, _ = make_client()
    content = client.get("/openapi.json").json()["paths"]["/orders"]["post"]["requestBody"]["content"]
    assert set(content) == {"text/plain", "application/json"}
