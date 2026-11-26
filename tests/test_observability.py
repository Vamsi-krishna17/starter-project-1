import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from langsmith import Client
from loguru import logger
from openai import APITimeoutError, AuthenticationError, NotFoundError, RateLimitError

import app.adapters.azure_openai_adapter as adapter_module
from app.adapters.azure_openai_adapter import (
    AzureOpenAIAdapter, LLMError, categorize_error, extract_retry_after,
)
from app.config import load_settings
from app.models import ExtractedOrder, OrderItem
from app.observability import LLMTracer, sanitize_text

API_KEY = "sk-test-SECRET-KEY-1234567890abcdef"
DB_URL = "postgresql://postgres:supersecret@db:5432/orders"
REQ = httpx.Request("POST", "https://example.openai.azure.com/x")


def settings(**over):
    base = dataclasses.replace(
        load_settings(), azure_endpoint="https://example.openai.azure.com", azure_api_key=API_KEY,
        azure_deployment="gpt-test", azure_api_version="2024-10-21", database_url=DB_URL,
        llm_max_retries=3, langsmith_tracing=False, langsmith_api_key="", langsmith_trace_content=False,
    )
    return dataclasses.replace(base, **over)


def http_error(cls, status, headers=None):
    return cls("boom", response=httpx.Response(status, headers=headers or {}, request=REQ), body=None)


class RecordingTracer:
    def __init__(self):
        self.started, self.finished = [], []

    def start(self, name, inputs, metadata):
        self.started.append((name, inputs, metadata))
        tracer = self

        class Run:
            def finish(self, outputs=None, metadata=None, error=None):
                tracer.finished.append({"outputs": outputs, "metadata": metadata, "error": error})

        return Run()


EXTRACTED = ExtractedOrder(customer="Acme", delivery_city="Pune", deadline=None, repeat_order=False,
                           items=[OrderItem(product_name="Pebble Buds", requested=3)])


def fake_client(parse=None, create=None, usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)):
    def default_parse(**kw):
        msg = SimpleNamespace(parsed=EXTRACTED)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)

    def default_create(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Hello"))], usage=usage)

    return SimpleNamespace(
        beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse or default_parse))),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create or default_create)),
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(adapter_module.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


# ---------------- tracing ----------------
def test_extraction_and_reply_are_traced_with_metadata_latency_and_tokens():
    tracer = RecordingTracer()
    a = AzureOpenAIAdapter(settings(), client=fake_client(), tracer=tracer)
    a.extract_order("Order", "3 pebble buds", __import__("datetime").date(2026, 11, 18))
    a.draft_reply({"status": "Ready", "items": [], "issues": [], "customer": "Acme"})
    assert [s[0] for s in tracer.started] == ["order_extraction", "customer_reply_generation"]
    assert tracer.started[0][2]["deployment"] == "gpt-test"
    for f in tracer.finished:
        assert f["metadata"]["status"] == "success" and f["metadata"]["llm_latency_ms"] >= 0
        assert f["outputs"]["usage_metadata"] == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


def test_missing_token_usage_is_handled():
    tracer = RecordingTracer()
    a = AzureOpenAIAdapter(settings(), client=fake_client(usage=None), tracer=tracer)
    assert a.draft_reply({"status": "Ready", "items": [], "issues": []}) == "Hello"
    assert "usage_metadata" not in tracer.finished[0]["outputs"]


def test_tracing_disabled_or_missing_key_does_not_break_app():
    for s in (settings(langsmith_tracing=False), settings(langsmith_tracing=True, langsmith_api_key="")):
        tracer = LLMTracer(s)
        assert tracer.enabled is False
        a = AzureOpenAIAdapter(s, client=fake_client(), tracer=tracer)
        assert a.draft_reply({"status": "Ready", "items": [], "issues": []}) == "Hello"


def test_langsmith_failures_never_break_the_workflow():
    client = MagicMock(spec=Client)
    client.create_run.side_effect = RuntimeError("langsmith down")
    client.update_run.side_effect = RuntimeError("langsmith down")
    s = settings(langsmith_tracing=True, langsmith_api_key="ls-key")
    a = AzureOpenAIAdapter(s, client=fake_client(), tracer=LLMTracer(s, client=client))
    assert a.draft_reply({"status": "Ready", "items": [], "issues": []}) == "Hello"


def test_real_tracer_posts_and_patches_run():
    client = MagicMock(spec=Client)
    s = settings(langsmith_tracing=True, langsmith_api_key="ls-key")
    a = AzureOpenAIAdapter(s, client=fake_client(), tracer=LLMTracer(s, client=client))
    a.draft_reply({"status": "Ready", "items": [], "issues": []})
    assert client.create_run.called and client.update_run.called
    assert client.create_run.call_args.kwargs["name"] == "customer_reply_generation"


# ---------------- errors, rate limits, retries ----------------
def test_error_categories():
    assert categorize_error(http_error(RateLimitError, 429)) == "rate_limit"
    assert categorize_error(http_error(AuthenticationError, 401)) == "authentication_error"
    assert categorize_error(http_error(NotFoundError, 404)) == "deployment_or_endpoint_not_found"
    assert categorize_error(APITimeoutError(request=REQ)) == "timeout"
    assert categorize_error(ValueError("x")) == "unexpected_error"


def test_429_is_retried_a_bounded_number_of_times_and_retry_after_captured(no_sleep):
    calls = []

    def always_429(**kw):
        calls.append(1)
        raise http_error(RateLimitError, 429, {"retry-after": "2"})

    tracer = RecordingTracer()
    a = AzureOpenAIAdapter(settings(llm_max_retries=3), client=fake_client(create=always_429), tracer=tracer)
    with pytest.raises(LLMError) as err:
        a.draft_reply({"status": "Ready", "items": [], "issues": []})
    assert len(calls) == 3 and err.value.category == "rate_limit"
    assert no_sleep == [2.0, 2.0]                       # Retry-After honoured, no wait after last try
    meta = tracer.finished[0]["metadata"]
    assert meta["attempts"] == 3 and meta["retries"] == 2 and meta["retry_after_seconds"] == 2.0
    assert meta["error_categories"] == ["rate_limit"] * 3 and tracer.finished[0]["error"].startswith("rate_limit")


def test_retry_after_wait_is_capped(no_sleep):
    def always_429(**kw):
        raise http_error(RateLimitError, 429, {"retry-after": "9999"})

    a = AzureOpenAIAdapter(settings(llm_max_retries=2), client=fake_client(create=always_429), tracer=RecordingTracer())
    with pytest.raises(LLMError):
        a.draft_reply({"status": "Ready", "items": [], "issues": []})
    assert no_sleep == [adapter_module.MAX_RETRY_WAIT_SECONDS]


def test_transient_error_then_success(no_sleep):
    state = {"n": 0}

    def flaky(**kw):
        state["n"] += 1
        if state["n"] == 1:
            raise http_error(RateLimitError, 429)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=None)

    tracer = RecordingTracer()
    a = AzureOpenAIAdapter(settings(), client=fake_client(create=flaky), tracer=tracer)
    assert a.draft_reply({"status": "Ready", "items": [], "issues": []}) == "ok"
    assert tracer.finished[0]["metadata"]["retries"] == 1


def test_auth_error_is_not_retried(no_sleep):
    calls = []

    def bad_key(**kw):
        calls.append(1)
        raise http_error(AuthenticationError, 401)

    tracer = RecordingTracer()
    a = AzureOpenAIAdapter(settings(), client=fake_client(create=bad_key), tracer=tracer)
    with pytest.raises(LLMError) as err:
        a.draft_reply({"status": "Ready", "items": [], "issues": []})
    assert len(calls) == 1 and no_sleep == [] and err.value.category == "authentication_error"
    assert tracer.finished[0]["outputs"] == {"error_category": "authentication_error"}


def test_invalid_structured_response_is_categorised():
    def none_parsed(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=None))], usage=None)

    a = AzureOpenAIAdapter(settings(), client=fake_client(parse=none_parsed), tracer=RecordingTracer())
    with pytest.raises(LLMError) as err:
        a.extract_order("s", "b", __import__("datetime").date(2026, 11, 18))
    assert err.value.category == "invalid_response"


# ---------------- security / privacy ----------------
def test_sanitize_masks_obvious_sensitive_values():
    text = f"mail a.b@corp.example phone +91 98765 43210 key {API_KEY} db {DB_URL} qty 12 date 2026-11-18"
    out = sanitize_text(text)
    for secret in ("a.b@corp.example", "98765 43210", API_KEY, "supersecret"):
        assert secret not in out
    assert "12" in out and "2026-11-18" in out


def test_default_trace_has_no_email_text_customer_city_or_secrets():
    tracer = RecordingTracer()
    a = AzureOpenAIAdapter(settings(), client=fake_client(), tracer=tracer)
    a.extract_order("Order", "Acme Pune ravi@acme.example call 9876543210", __import__("datetime").date(2026, 11, 18))
    a.draft_reply({"status": "Ready", "customer": "Acme", "delivery_city": "Pune", "items": [], "issues": []})
    blob = str(tracer.started) + str(tracer.finished)
    for forbidden in ("ravi@acme.example", "9876543210", "Acme", "Pune", API_KEY, "supersecret", DB_URL, "SELECT"):
        assert forbidden not in blob


def test_content_mode_sanitizes_and_still_omits_customer_and_city():
    tracer = RecordingTracer()
    a = AzureOpenAIAdapter(settings(langsmith_trace_content=True), client=fake_client(), tracer=tracer)
    a.extract_order("Order", "write to ravi@acme.example", __import__("datetime").date(2026, 11, 18))
    blob = str(tracer.started) + str(tracer.finished)
    assert "[EMAIL]" in blob and "ravi@acme.example" not in blob
    assert "'Acme'" not in blob and "Pune" not in blob


def test_logs_never_contain_secrets(no_sleep):
    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        def boom(**kw):
            raise http_error(RateLimitError, 429, {"retry-after": "1"})

        a = AzureOpenAIAdapter(settings(), client=fake_client(create=boom), tracer=RecordingTracer())
        with pytest.raises(LLMError):
            a.draft_reply({"status": "Ready", "items": [], "issues": []})
    finally:
        logger.remove(sink)
    text = "".join(lines)
    assert "LLM retry" in text and "LLM operation failed" in text
    assert API_KEY not in text and "supersecret" not in text


def test_retry_after_header_parsing():
    assert extract_retry_after(http_error(RateLimitError, 429, {"retry-after": "3"})) == 3.0
    assert extract_retry_after(http_error(RateLimitError, 429)) is None
    assert extract_retry_after(ValueError("x")) is None
