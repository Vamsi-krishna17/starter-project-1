"""run_batch.py talks to the API over HTTP. Here HTTP is mocked: no Azure, no database, no server."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.orders import get_facade
from app.main import app
from app.models import OrderResponse
from run_batch import ENDPOINT, REQUIRED_FIELDS, find_email_files, main, run_batch, save


def api_body(status="Ready", **over):
    body = {"customer": "C", "delivery_city": "D", "deadline": None, "items": [], "status": status,
            "issues": [] if status == "Ready" else ["x"], "reply": "hello"}
    body.update(over)
    return body


def client_for(handler):
    return httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))


def make_folders(tmp_path: Path):
    lab, pra = tmp_path / "labelled", tmp_path / "practice"
    lab.mkdir(); pra.mkdir()
    (lab / "e1.txt").write_text("ok one"); (lab / "e2.txt").write_text("partial two")
    (lab / "answer_key.json").write_text("{}")
    (pra / "p1.txt").write_text("need info"); (pra / ".gitkeep").write_text("")
    return lab, pra


def status_by_content(request: httpx.Request) -> httpx.Response:
    raw = json.loads(request.content)["email"]
    status = "Partial" if "partial" in raw else "Need info" if "need" in raw else "Ready"
    return httpx.Response(200, json=api_body(status))


def test_successful_response_is_stored_and_request_matches_swagger_format(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=api_body("Ready", customer="Acme"))

    f = tmp_path / "m.txt"; f.write_text("line1\nline2 \"q\"")
    report = run_batch([("labelled", f)], client_for(handler))
    assert seen[0].method == "POST" and seen[0].url.path == ENDPOINT == "/orders"
    assert seen[0].headers["content-type"].startswith("application/json")
    assert json.loads(seen[0].content) == {"email": "line1\nline2 \"q\""}
    entry = report["results"][0]
    assert entry["response"]["customer"] == "Acme" and entry["error"] is None
    assert entry["folder"] == "labelled" and entry["file"] == "m.txt"


def test_different_statuses_are_counted(tmp_path):
    lab, pra = make_folders(tmp_path)
    report = run_batch(find_email_files([lab, pra]), client_for(status_by_content))
    s = report["summary"]
    assert s["status_counts"] == {"Ready": 1, "Partial": 1, "Need info": 1}
    assert s["total_emails"] == 3 and s["successful"] == 3 and s["failed"] == 0


@pytest.mark.parametrize("status_code,body", [(502, {"error": "llm_unavailable"}), (503, {"error": "db"}), (422, {"detail": "bad"}), (500, None)])
def test_http_errors_are_recorded(tmp_path, status_code, body):
    f = tmp_path / "m.txt"; f.write_text("x")
    handler = lambda r: httpx.Response(status_code, json=body) if body else httpx.Response(status_code, text="Internal Server Error")
    entry = run_batch([("practice", f)], client_for(handler))["results"][0]
    assert entry["response"] is None
    assert entry["error"]["type"] == "http_error" and entry["error"]["http_status"] == status_code


def test_connection_error_and_timeout_are_recorded(tmp_path):
    f = tmp_path / "m.txt"; f.write_text("x")

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    assert run_batch([("a", f)], client_for(refused))["results"][0]["error"]["type"] == "connection_error"
    assert run_batch([("a", f)], client_for(slow))["results"][0]["error"]["type"] == "timeout"


def test_invalid_responses_are_recorded(tmp_path):
    f = tmp_path / "m.txt"; f.write_text("x")
    not_json = run_batch([("a", f)], client_for(lambda r: httpx.Response(200, text="<html>oops</html>")))
    assert not_json["results"][0]["error"]["type"] == "invalid_json_response"
    wrong_shape = run_batch([("a", f)], client_for(lambda r: httpx.Response(200, json={"hello": 1})))
    assert wrong_shape["results"][0]["error"]["type"] == "invalid_response"
    assert "status" in wrong_shape["results"][0]["error"]["message"]


def test_one_failed_email_does_not_stop_the_rest(tmp_path):
    lab, pra = make_folders(tmp_path)
    (lab / "e2.txt").write_text("boom")

    def handler(request):
        if "boom" in json.loads(request.content)["email"]:
            return httpx.Response(502, json={"error": "llm_unavailable"})
        return httpx.Response(200, json=api_body())

    report = run_batch(find_email_files([lab, pra]), client_for(handler))
    assert [r["file"] for r in report["results"]] == ["e1.txt", "e2.txt", "p1.txt"]
    assert [r["response"] is not None for r in report["results"]] == [True, False, True]
    s = report["summary"]
    assert (s["successful"], s["failed"]) == (2, 1) and s["error_counts"] == {"http_error": 1}
    assert s["per_folder"] == {"labelled": {"total": 2, "successful": 1, "failed": 1},
                               "practice": {"total": 1, "successful": 1, "failed": 0}}


def test_both_folders_processed_and_non_email_files_skipped(tmp_path):
    lab, pra = make_folders(tmp_path)
    files = find_email_files([lab, pra])
    assert [(d, f.name) for d, f in files] == [("labelled", "e1.txt"), ("labelled", "e2.txt"), ("practice", "p1.txt")]


def test_output_json_is_created_with_counts_and_input_files_are_untouched(tmp_path):
    lab, pra = make_folders(tmp_path)
    before = {p: hashlib.md5(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*") if p.is_file()}
    out = tmp_path / "new_dir" / "output" / "batch_results.json"        # parent folders do not exist yet
    save(run_batch(find_email_files([lab, pra]), client_for(status_by_content)), out)
    data = json.loads(out.read_text())
    assert data["summary"]["total_emails"] == len(data["results"]) == 3
    assert data["summary"]["api_url"] == "http://api.test/orders"
    assert set(data["results"][0]["response"]) == REQUIRED_FIELDS
    assert {p: hashlib.md5(p.read_bytes()).hexdigest() for p in before} == before


def test_main_defaults_write_output_batch_results_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("labelled", "practice"):
        d = tmp_path / "data" / "input" / name
        d.mkdir(parents=True)
        (d / "a.txt").write_text("hello")
    hosts = []

    def handler(request):
        hosts.append(str(request.url))
        return httpx.Response(200, json=api_body())

    code = main(["--base-url", "http://myhost:9000"], transport=httpx.MockTransport(handler))
    data = json.loads((tmp_path / "output" / "batch_results.json").read_text())
    assert code == 0 and data["summary"]["successful"] == 2
    assert {r["folder"] for r in data["results"]} == {"labelled", "practice"}
    assert hosts == ["http://myhost:9000/orders"] * 2


def test_main_returns_error_code_when_nothing_succeeds(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "data" / "input" / "labelled"; d.mkdir(parents=True); (d / "a.txt").write_text("x")

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    assert main([], transport=httpx.MockTransport(refused)) == 1
    assert json.loads((tmp_path / "output" / "batch_results.json").read_text())["summary"]["failed"] == 1


def test_endpoint_matches_the_real_fastapi_route(tmp_path):
    """Uses the real app routing with a fake workflow: proves URL, body format and schema fit POST /orders."""
    class Rec:
        def process_email(self, email):
            return OrderResponse(customer="C", delivery_city="D", deadline=None, items=[], status="Partial",
                                 issues=["x"], reply=f"got {len(email)} chars")

    app.dependency_overrides[get_facade] = lambda: Rec()
    try:
        f = tmp_path / "m.txt"; f.write_text("some email\nbody")
        report = run_batch([("labelled", f)], TestClient(app))
    finally:
        app.dependency_overrides.clear()
    assert report["results"][0]["error"] is None
    assert report["results"][0]["response"]["status"] == "Partial"


def test_main_finds_the_Input_labelled_and_Input_practice_folders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("labelled", "practice"):
        d = tmp_path / "Input" / name
        d.mkdir(parents=True)
        (d / "email_01.txt").write_text("hello")
    code = main([], transport=httpx.MockTransport(lambda r: httpx.Response(200, json=api_body())))
    data = json.loads((tmp_path / "output" / "batch_results.json").read_text())
    assert code == 0 and data["summary"]["total_emails"] == 2
    assert {(r["folder"], r["file"]) for r in data["results"]} == {("labelled", "email_01.txt"), ("practice", "email_01.txt")}
