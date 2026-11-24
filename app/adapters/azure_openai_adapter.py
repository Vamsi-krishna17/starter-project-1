"""The only module that touches the Azure OpenAI SDK."""
import json
import time
from datetime import date

from loguru import logger
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    AzureOpenAI,
    BadRequestError,
    ContentFilterFinishReasonError,
    InternalServerError,
    LengthFinishReasonError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.models import ExtractedOrder, ProductMatch
from app.observability import LLMTracer, sanitize_text
from app.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    MATCH_SYSTEM_PROMPT,
    REPLY_SYSTEM_PROMPT,
    build_extraction_user_prompt,
    build_match_user_prompt,
    build_reply_user_prompt,
)

MAX_RETRY_WAIT_SECONDS = 30.0  # upper bound for any single wait between retries

OP_EXTRACT = "order_extraction"
OP_REPLY = "customer_reply_generation"
OP_MATCH = "product_description_matching"
OP_EMBED = "embedding_generation"


class LLMError(Exception):
    """Clear, secret-free error raised when the LLM step cannot complete."""

    def __init__(self, message: str, category: str = "unexpected_error"):
        super().__init__(message)
        self.category = category


def categorize_error(exc: Exception) -> str:
    """Map an SDK/parsing error to a short category (no message text, so no secrets)."""
    if isinstance(exc, RateLimitError):
        return "rate_limit"
    if isinstance(exc, APITimeoutError):
        return "timeout"
    if isinstance(exc, AuthenticationError):
        return "authentication_error"  # invalid API key
    if isinstance(exc, PermissionDeniedError):
        return "permission_denied"
    if isinstance(exc, NotFoundError):
        return "deployment_or_endpoint_not_found"  # wrong deployment name / endpoint path
    if isinstance(exc, BadRequestError):
        return "invalid_request"
    if isinstance(exc, InternalServerError):
        return "server_error"
    if isinstance(exc, APIConnectionError):
        return "connection_error"  # includes an unreachable / invalid endpoint
    if isinstance(exc, (LengthFinishReasonError, ContentFilterFinishReasonError)):
        return "invalid_response"
    if isinstance(exc, (ValidationError, json.JSONDecodeError)):
        return "json_parse_error"
    return "unexpected_error"


def extract_retry_after(exc: Exception) -> float | None:
    """Seconds from the Retry-After header, when the error carries one."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        value = headers.get("retry-after")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class AzureOpenAIAdapter:
    def __init__(self, settings: Settings | None = None, client=None, tracer: LLMTracer | None = None):
        self._s = settings or get_settings()
        if client is None:
            missing = [
                name
                for name, value in (
                    ("AZURE_OPENAI_ENDPOINT", self._s.azure_endpoint),
                    ("AZURE_OPENAI_API_KEY", self._s.azure_api_key),
                    ("AZURE_OPENAI_DEPLOYMENT_NAME", self._s.azure_deployment),
                    ("AZURE_OPENAI_API_VERSION", self._s.azure_api_version),
                )
                if not value
            ]
            if missing:
                raise LLMError(
                    f"Azure OpenAI is not configured: {', '.join(missing)}", "configuration_error"
                )
            client = AzureOpenAI(
                azure_endpoint=self._s.azure_endpoint,
                api_key=self._s.azure_api_key,
                api_version=self._s.azure_api_version,
                timeout=self._s.llm_timeout_seconds,
                max_retries=0,  # we do our own bounded retries
            )
        self._client = client
        self._tracer = tracer or LLMTracer(self._s)

    # ---- application-level methods -------------------------------------
    def extract_order(
        self, subject: str, body: str, reference_date: date
    ) -> ExtractedOrder:
        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_extraction_user_prompt(
                    subject, body, reference_date, self._s.max_email_chars
                ),
            },
        ]

        def request():
            return self._client.beta.chat.completions.parse(
                model=self._s.azure_deployment,
                messages=messages,
                response_format=ExtractedOrder,
                max_completion_tokens=self._s.llm_max_completion_tokens,
            )

        def finalize(response):
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise LLMError(
                    "The model did not return a valid structured extraction", "invalid_response"
                )
            return parsed, self._extraction_trace_outputs(parsed)

        return self._call(
            OP_EXTRACT, request, self._extraction_trace_inputs(subject, body, reference_date), finalize
        )

    def draft_reply(self, result: dict) -> str:
        messages = [
            {"role": "system", "content": REPLY_SYSTEM_PROMPT},
            {"role": "user", "content": build_reply_user_prompt(result)},
        ]

        def request():
            return self._client.chat.completions.create(
                model=self._s.azure_deployment,
                messages=messages,
                max_completion_tokens=self._s.llm_max_completion_tokens,
            )

        def finalize(response):
            text = (response.choices[0].message.content or "").strip()
            if not text:
                raise LLMError("The model returned an empty reply", "invalid_response")
            return text, self._reply_trace_outputs(text)

        return self._call(OP_REPLY, request, self._reply_trace_inputs(result), finalize)

    def select_matching_products(self, description: str, chunks) -> list[str]:
        """Ask the model which product codes in the retrieved brochure chunks fit EVERY detail.
        The codes are only a suggestion: Python validates each one against the products table."""
        messages = [
            {"role": "system", "content": MATCH_SYSTEM_PROMPT},
            {"role": "user", "content": build_match_user_prompt(description, chunks)},
        ]

        def request():
            return self._client.beta.chat.completions.parse(
                model=self._s.azure_deployment,
                messages=messages,
                response_format=ProductMatch,
                max_completion_tokens=self._s.llm_max_completion_tokens,
            )

        def finalize(response):
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise LLMError("The model did not return a valid product match", "invalid_response")
            return parsed.product_codes, {"code_count": len(parsed.product_codes)}

        return self._call(
            OP_MATCH, request, {"description_chars": len(description), "chunk_count": len(chunks)}, finalize
        )

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Embed one or many texts with the embedding deployment (text-embedding-3-small, 1536 numbers each)."""
        if not texts:
            return []

        def request():
            return self._client.embeddings.create(model=self._s.embedding_deployment, input=texts)

        def finalize(response):
            vectors = [d.embedding for d in sorted(response.data, key=lambda d: d.index)]
            expected = self._s.embedding_dimensions
            if len(vectors) != len(texts) or any(len(v) != expected for v in vectors):
                raise LLMError(
                    f"Embedding response is not {len(texts)} vector(s) of {expected} dimensions "
                    "(is the deployment text-embedding-3-small?)",
                    "invalid_response",
                )
            return vectors, {"embedding_count": len(vectors)}

        return self._call(
            OP_EMBED, request, {"input_count": len(texts), "input_chars": sum(len(t) for t in texts)}, finalize
        )

    def generate_embedding(self, text: str) -> list[float]:
        return self.generate_embeddings([text])[0]

    # ---- what LangSmith may see (never customer name/city, DB data, SQL or secrets) ----
    def _extraction_trace_inputs(self, subject: str, body: str, reference_date: date) -> dict:
        inputs = {"email_chars": len(subject) + len(body), "reference_date": reference_date.isoformat()}
        if self._s.langsmith_trace_content:
            text = f"Subject: {subject}\n\n{body}"[: self._s.max_email_chars]
            inputs["email_text_sanitized"] = sanitize_text(text)
        return inputs

    def _extraction_trace_outputs(self, parsed: ExtractedOrder) -> dict:
        out = {
            "item_count": len(parsed.items),
            "customer_found": bool((parsed.customer or "").strip()),
            "delivery_city_found": bool((parsed.delivery_city or "").strip()),
            "deadline_found": bool((parsed.deadline or "").strip()),
        }
        if self._s.langsmith_trace_content:
            out["items"] = [
                {"product_name": sanitize_text(i.product_name or ""), "requested": i.requested}
                for i in parsed.items
            ]
        return out

    def _reply_trace_inputs(self, result: dict) -> dict:
        inputs = {
            "status": result.get("status"),
            "item_count": len(result.get("items", [])),
            "issue_count": len(result.get("issues", [])),
        }
        if self._s.langsmith_trace_content:
            inputs["issues"] = [sanitize_text(str(i)) for i in result.get("issues", [])]
        return inputs

    def _reply_trace_outputs(self, text: str) -> dict:
        out = {"reply_chars": len(text)}
        if self._s.langsmith_trace_content:
            out["reply_sanitized"] = sanitize_text(text)
        return out

    # ---- bounded retries, latency, tokens, error categories, tracing ------------------
    def _call(self, op: str, request_fn, trace_inputs: dict, finalize):
        attempts_allowed = max(1, self._s.llm_max_retries)
        run = self._tracer.start(
            op,
            trace_inputs,
            {
                "operation": op,
                "deployment": self._s.azure_deployment,
                "ls_provider": "azure_openai",
                "ls_model_name": self._s.azure_deployment,
                "max_attempts": attempts_allowed,
            },
        )
        logger.info("LLM operation started: {}", op)
        started = time.perf_counter()
        delay = 1.0
        categories: list[str] = []
        retry_after_seen: float | None = None

        def metrics(attempt: int, status: str, extra: dict | None = None) -> dict:
            data = {
                "status": status,
                "attempts": attempt,
                "retries": attempt - 1,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "error_categories": list(categories),
            }
            if retry_after_seen is not None:
                data["retry_after_seconds"] = retry_after_seen
            data.update(extra or {})
            return data

        for attempt in range(1, attempts_allowed + 1):
            call_start = time.perf_counter()
            try:
                response = request_fn()
                result, outputs = finalize(response)
            except (RateLimitError, APIConnectionError, InternalServerError) as exc:
                # Transient: retried a bounded number of times. (APITimeoutError is an APIConnectionError.)
                category = categorize_error(exc)
                categories.append(category)
                retry_after = extract_retry_after(exc)
                if retry_after is not None:
                    retry_after_seen = retry_after
                if attempt == attempts_allowed:
                    logger.error(
                        "LLM operation failed: {} category={} attempts={}", op, category, attempt
                    )
                    run.finish(
                        outputs={"error_category": category},
                        metadata=metrics(attempt, "error"),
                        error=f"{category}: {type(exc).__name__}",
                    )
                    raise LLMError(
                        f"Azure OpenAI unavailable after {attempt} attempts ({category})", category
                    ) from exc
                wait = min(retry_after, MAX_RETRY_WAIT_SECONDS) if retry_after is not None else delay
                logger.warning(
                    "LLM retry: {} category={} attempt={}/{} retry_after={} wait_s={:.1f}",
                    op, category, attempt, attempts_allowed, retry_after, wait,
                )
                time.sleep(wait)
                delay = min(delay * 2, MAX_RETRY_WAIT_SECONDS)
                continue
            except LLMError as exc:  # invalid response raised by finalize
                logger.error("LLM operation failed: {} category={}", op, exc.category)
                run.finish(
                    outputs={"error_category": exc.category},
                    metadata=metrics(attempt, "error"),
                    error=f"{exc.category}: LLMError",
                )
                raise
            except Exception as exc:  # auth, bad request, not found, parse errors...: never retried
                category = categorize_error(exc)
                categories.append(category)
                logger.error("LLM operation failed: {} category={}", op, category)
                run.finish(
                    outputs={"error_category": category},
                    metadata=metrics(attempt, "error"),
                    error=f"{category}: {type(exc).__name__}",
                )
                raise LLMError(f"Azure OpenAI request failed ({category})", category) from exc

            latency_ms = (time.perf_counter() - call_start) * 1000
            usage = getattr(response, "usage", None)
            tokens = {
                key: getattr(usage, attr, None)
                for key, attr in (
                    ("input_tokens", "prompt_tokens"),
                    ("output_tokens", "completion_tokens"),
                    ("total_tokens", "total_tokens"),
                )
            }
            tokens = {k: v for k, v in tokens.items() if isinstance(v, int)}  # never invent counts
            logger.info(
                "LLM operation completed: {} latency_ms={:.0f} attempts={} "
                "input_tokens={} output_tokens={} total_tokens={}",
                op, latency_ms, attempt,
                tokens.get("input_tokens"), tokens.get("output_tokens"), tokens.get("total_tokens"),
            )
            if tokens:
                outputs = {**outputs, "usage_metadata": tokens}
            run.finish(
                outputs=outputs,
                metadata=metrics(attempt, "success", {"llm_latency_ms": round(latency_ms)}),
            )
            return result
        raise LLMError("Azure OpenAI call failed")  # pragma: no cover
