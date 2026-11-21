"""LangSmith tracing helpers. Observability only - never used for business decisions.

Every function here swallows its own failures: if LangSmith is down, misconfigured or not
installed, the order workflow carries on exactly as before.
"""
import re
from typing import Any

from loguru import logger

from app.config import Settings

# ---- sanitising (affects trace data only, never what is sent to Azure OpenAI) ----
_DSN = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@]+@[^\s]+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|lsv2_[A-Za-z0-9_]{10,}|[A-Fa-f0-9]{32,}|[A-Za-z0-9+/_=-]{40,})\b"
)
_PHONE = re.compile(r"(?<![\w])\+?\d(?:[\s().-]?\d){9,14}(?![\w])")


def _mask_phone(match: re.Match) -> str:
    return "[PHONE]" if len(re.sub(r"\D", "", match.group(0))) >= 10 else match.group(0)


def sanitize_text(text: str) -> str:
    text = _DSN.sub("[DB_URL]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _SECRET.sub("[SECRET]", text)
    return _PHONE.sub(_mask_phone, text)


# ---- tracer -----------------------------------------------------------------------
class _NoopRun:
    def finish(self, outputs=None, metadata=None, error=None) -> None:
        return None


class _ActiveRun:
    def __init__(self, run):
        self._run = run

    def finish(self, outputs=None, metadata=None, error=None) -> None:
        try:
            if metadata:
                self._run.add_metadata(metadata)
            self._run.end(outputs=outputs or {}, error=error)
            self._run.patch()
        except Exception as exc:  # tracing must never break the app
            logger.warning("LangSmith trace update failed: {}", type(exc).__name__)


class LLMTracer:
    """Creates one LangSmith run per LLM call (order_extraction / customer_reply_generation)."""

    def __init__(self, settings: Settings, client=None):
        self._project = settings.langsmith_project
        self._client = client
        self.enabled = bool(settings.langsmith_tracing and (settings.langsmith_api_key or client))
        if settings.langsmith_tracing and not self.enabled:
            logger.warning("LANGSMITH_TRACING is on but LANGSMITH_API_KEY is empty: tracing disabled")
        if self.enabled and self._client is None:
            try:
                from langsmith import Client

                self._client = Client(
                    api_url=settings.langsmith_endpoint, api_key=settings.langsmith_api_key
                )
            except Exception as exc:
                logger.warning("LangSmith client setup failed ({}): tracing disabled", type(exc).__name__)
                self.enabled = False
        if self.enabled:
            logger.info("LangSmith tracing enabled (project={})", self._project)

    def start(self, name: str, inputs: dict[str, Any], metadata: dict[str, Any]):
        if not self.enabled:
            return _NoopRun()
        try:
            from langsmith.run_trees import RunTree

            run = RunTree(
                name=name,
                run_type="llm",
                inputs=inputs,
                project_name=self._project,
                client=self._client,
                extra={"metadata": metadata},
                tags=["email-to-order"],
            )
            run.post()
            return _ActiveRun(run)
        except Exception as exc:
            logger.warning("LangSmith trace start failed: {}", type(exc).__name__)
            return _NoopRun()