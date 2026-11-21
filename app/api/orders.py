from functools import lru_cache

from fastapi import APIRouter, Depends
from loguru import logger

from app.adapters.azure_openai_adapter import AzureOpenAIAdapter
from app.config import get_settings
from app.models import OrderRequest, OrderResponse
from app.repositories.product_repository import ProductRepository
from app.services.order_facade import OrderFacade
from app.services.status_service import OrderStatusService
from app.services.stock_service import StockService
from app.services.validation_service import OrderValidationService

router = APIRouter()


@lru_cache
def get_facade() -> OrderFacade:
    settings = get_settings()
    return OrderFacade(
        adapter=AzureOpenAIAdapter(settings),
        validator=OrderValidationService(settings.lead_time_days, settings.require_deadline),
        stock_service=StockService(ProductRepository(settings.database_url)),
        status_service=OrderStatusService(),
    )


@router.post("/orders", response_model=OrderResponse)
def create_order(request: OrderRequest, facade: OrderFacade = Depends(get_facade)):
    logger.info("Request received: POST /orders (email_chars={})", len(request.email))
    return facade.process_email(request.email)
