"""Central configuration. Every other module imports settings from here."""
import os
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    azure_endpoint: str
    azure_api_key: str
    azure_deployment: str
    azure_api_version: str
    database_url: str
    lead_time_days: int
    log_level: str
    llm_timeout_seconds: float
    llm_max_retries: int
    llm_max_completion_tokens: int
    max_email_chars: int
    require_deadline: bool
    langsmith_tracing: bool
    langsmith_api_key: str
    langsmith_project: str
    langsmith_endpoint: str
    langsmith_trace_content: bool
    embedding_deployment: str
    embedding_dimensions: int
    rag_top_k: int
    rag_resolve_top_k: int
    quote_discount_tiers: tuple  # ((min_quantity, discount_percent), ...) ascending
    quote_free_delivery_threshold_inr: Decimal
    quote_delivery_charge_inr: Decimal
    quote_validity_days: int


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in ("1", "true", "yes")


def _tiers(raw: str) -> tuple:
    pairs = [p.split(":") for p in raw.split(",") if p.strip()]
    return tuple(sorted((int(q), int(d)) for q, d in pairs))


def load_settings() -> Settings:
    return Settings(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        azure_api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", ""),
        azure_api_version=os.getenv("AZURE_OPENAI_API_VERSION", ""),
        database_url=os.getenv("DATABASE_URL", ""),
        lead_time_days=int(os.getenv("LEAD_TIME_DAYS", "3")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
        llm_max_completion_tokens=int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "2000")),
        max_email_chars=int(os.getenv("MAX_EMAIL_CHARS", "8000")),
        require_deadline=_flag("REQUIRE_DEADLINE"),
        langsmith_tracing=_flag("LANGSMITH_TRACING"),
        langsmith_api_key=os.getenv("LANGSMITH_API_KEY", ""),
        langsmith_project=os.getenv("LANGSMITH_PROJECT", "email-to-order-starter-project"),
        langsmith_endpoint=os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
        langsmith_trace_content=_flag("LANGSMITH_TRACE_CONTENT"),
        embedding_deployment=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small"),
        embedding_dimensions=1536,  # text-embedding-3-small; documents and queries use the same model
        rag_top_k=int(os.getenv("RAG_TOP_K", "5")),
        rag_resolve_top_k=int(os.getenv("RAG_RESOLVE_TOP_K", "10")),
        quote_discount_tiers=_tiers(os.getenv("QUOTE_DISCOUNT_TIERS", "1:0,10:5,50:8,200:12")),
        quote_free_delivery_threshold_inr=Decimal(os.getenv("QUOTE_FREE_DELIVERY_THRESHOLD_INR", "50000")),
        quote_delivery_charge_inr=Decimal(os.getenv("QUOTE_DELIVERY_CHARGE_INR", "1500")),
        quote_validity_days=int(os.getenv("QUOTE_VALIDITY_DAYS", "7")),
    )


@lru_cache
def get_settings() -> Settings:
    return load_settings()
