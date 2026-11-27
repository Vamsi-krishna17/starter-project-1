import json
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from pydantic import ValidationError

from app.adapters.azure_openai_adapter import AzureOpenAIAdapter
from app.config import get_settings
from app.models import OrderRequest, OrderResponse
from app.repositories.document_repository import DocumentRepository
from app.repositories.order_history_repository import OrderHistoryRepository
from app.repositories.product_repository import ProductRepository
from app.services.order_facade import OrderFacade
from app.services.product_resolver import ProductResolver
from app.services.quote_service import QuoteConfig, QuoteService
from app.services.retrieval_service import RetrievalService
from app.services.status_service import OrderStatusService
from app.services.stock_service import StockService
from app.services.validation_service import OrderValidationService

router = APIRouter()

# Shown in Swagger UI: pick "text/plain" in the request body dropdown to paste a raw email.
_EXAMPLE_EMAIL = (
    "From: Arun Rao <arun@brightpathcoaching.example>\n"
    "Date: Sun, 18 Nov 2026 16:40:00 +0530\n"
    "Subject: tablets + earbuds for our new batch\n\n"
    "hello team,\n"
    "we want 12 nimbus tab 8 lite and 10 PEBBLE BUDS.\n"
    "deliver to Brightpath Coaching Centre, Ameerpet, Hyderabad.\n"
    "a week from today is the latest we can take them.\n\n"
    "regards,\nArun Rao\nBrightpath Coaching Centre"
)

_REQUEST_BODY_DOC = {
    "requestBody": {
        "required": True,
        "content": {
            "text/plain": {
                "schema": {"type": "string"},
                "example": _EXAMPLE_EMAIL,
            },
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["email"],
                    "properties": {"email": {"type": "string", "description": "The raw email text"}},
                },
                "example": {"email": _EXAMPLE_EMAIL},
            },
        },
    }
}


@lru_cache
def get_facade() -> OrderFacade:
    settings = get_settings()
    adapter = AzureOpenAIAdapter(settings)          # one configured client: chat + embeddings
    products = ProductRepository(settings.database_url)
    retrieval = RetrievalService(adapter, DocumentRepository(settings.database_url), settings.rag_top_k)
    resolver = ProductResolver(
        products, retrieval=retrieval, matcher=adapter,
        history_repo=OrderHistoryRepository(settings.database_url), resolve_top_k=settings.rag_resolve_top_k,
        aliases=dict(settings.product_aliases),
    )
    return OrderFacade(
        adapter=adapter,
        validator=OrderValidationService(settings.lead_time_days, settings.require_deadline),
        stock_service=StockService(products),
        status_service=OrderStatusService(),
        resolver=resolver,
        quote_service=QuoteService(QuoteConfig.from_settings(settings)),
        retrieval=retrieval,
        reply_top_k=settings.rag_top_k,
    )


async def _read_email(request: Request) -> str:
    """Return the raw email from either a text/plain body or a JSON {"email": ...} body."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    raw = await request.body()

    if content_type == "text/plain":
        email = raw.decode("utf-8", errors="replace")
        if not email.strip():
            raise HTTPException(status_code=422, detail="Request body is empty: paste the raw email text")
        return email

    if content_type == "application/json":
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="Invalid JSON body")
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail='JSON body must be an object like {"email": "..."}')
        try:
            return OrderRequest(**data).email
        except ValidationError as exc:
            detail = [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
            raise HTTPException(status_code=422, detail=detail)

    raise HTTPException(
        status_code=415,
        detail="Unsupported Content-Type: use text/plain (raw email) or application/json",
    )


@router.post("/orders", response_model=OrderResponse, openapi_extra=_REQUEST_BODY_DOC)
async def create_order(request: Request, facade: OrderFacade = Depends(get_facade)):
    email = await _read_email(request)
    logger.info("Request received: POST /orders (email_chars={})", len(email))
    # The workflow is synchronous (blocking LLM/DB calls), so keep it off the event loop.
    return await run_in_threadpool(facade.process_email, email)