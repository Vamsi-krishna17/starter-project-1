"""OrderFacade - runs the four steps in a fixed order. Contains no business rules."""
import time
from datetime import date

from loguru import logger

from app.adapters.azure_openai_adapter import AzureOpenAIAdapter
from app.models import OrderResponse, ResponseItem
from app.services.status_service import OrderStatusService
from app.services.stock_service import StockService
from app.services.validation_service import OrderValidationService
from app.utils.email_parser import parse_email


class OrderFacade:
    def __init__(
        self,
        adapter: AzureOpenAIAdapter,
        validator: OrderValidationService,
        stock_service: StockService,
        status_service: OrderStatusService,
    ):
        self._adapter = adapter
        self._validator = validator
        self._stock = stock_service
        self._status = status_service

    def process_email(self, raw_email: str) -> OrderResponse:
        started = time.perf_counter()
        logger.info("Workflow started (email_chars={})", len(raw_email))

        # Parse
        parsed = parse_email(raw_email)
        reference_date = parsed.date or date.today()
        logger.info("Email parsing completed (date_header_found={}, body_chars={})", parsed.date is not None, len(parsed.body))
        if parsed.date is None:
            logger.warning("No usable Date header; falling back to today's date")

        # 1. Extract (LLM)
        logger.info("LLM extraction started")
        extracted = self._adapter.extract_order(parsed.subject, parsed.body, reference_date)
        logger.info("LLM extraction completed: {} item(s)", len(extracted.items))

        # 2. Validate (code)
        validation = self._validator.validate(extracted, reference_date, parsed.body)
        logger.info("Validation completed: passed={} issues={}", validation.passed, len(validation.issues))

        # 3. Check stock + decide status (code)
        lines = self._stock.check(extracted.items)
        logger.info("Stock checking completed: {} item(s)", len(lines))
        status, issues = self._status.decide(validation.issues, lines)
        logger.info("Order status determined: {} (issues={})", status, len(issues))

        items = [
            ResponseItem(
                product_name=l.product_name,
                product_code=l.product_code,
                requested=l.requested,
                available=l.available,
            )
            for l in lines
        ]
        computed = {
            "customer": (extracted.customer or "").strip() or None,
            "delivery_city": (extracted.delivery_city or "").strip() or None,
            "deadline": validation.deadline.isoformat() if validation.deadline else None,
            "status": status,
            "items": [i.model_dump() for i in items],
            "issues": issues,
        }

        # 4. Draft reply (LLM) - gets the computed result only, never the raw email
        logger.info("Reply generation started")
        reply = self._adapter.draft_reply(computed)
        logger.info("Reply generation completed")
        logger.info("Workflow completed in {:.0f} ms", (time.perf_counter() - started) * 1000)
        return OrderResponse(**computed, reply=reply)