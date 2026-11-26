"""OrderFacade - runs the steps in a fixed order. Contains no business rules."""
import time
from datetime import date

from loguru import logger

from app.adapters.azure_openai_adapter import AzureOpenAIAdapter
from app.models import OrderItem, OrderResponse, ResponseItem
from app.repositories.product_repository import RepositoryError
from app.services.product_resolver import ProductResolver
from app.services.quote_service import QuoteService
from app.services.status_service import OrderStatusService
from app.services.stock_service import StockLine, StockService
from app.services.validation_service import OrderValidationService, is_valid_quantity, mentions_previous_order
from app.utils.email_parser import parse_email


class OrderFacade:
    def __init__(
        self,
        adapter: AzureOpenAIAdapter,
        validator: OrderValidationService,
        stock_service: StockService,
        status_service: OrderStatusService,
        resolver: ProductResolver | None = None,
        quote_service: QuoteService | None = None,
        retrieval=None,
        reply_top_k: int = 5,
        max_query_chars: int = 2000,
    ):
        self._adapter = adapter
        self._validator = validator
        self._stock = stock_service
        self._status = status_service
        self._resolver = resolver
        self._quote = quote_service or QuoteService()
        self._retrieval = retrieval          # None = no RAG context for the reply
        self._reply_top_k = reply_top_k
        self._max_query_chars = max_query_chars

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
        logger.info("LLM extraction completed: {} item(s), repeat_order={}", len(extracted.items), extracted.repeat_order)

        # 2. Resolve products (code + RAG): names, descriptions, repeat orders
        repeat = extracted.repeat_order or mentions_previous_order(parsed.body)
        resolver = self._resolver or ProductResolver(self._stock.repository)
        resolution = resolver.resolve(extracted, repeat)
        logger.info("Product resolution completed: {} item(s), {} issue(s)", len(resolution.items), len(resolution.issues))

        # 3. Validate (code) - the items being validated are the resolved ones
        effective = extracted.model_copy(update={"items": [
            OrderItem(product_name=r.written_name, requested=r.requested) for r in resolution.items
        ]})
        validation = self._validator.validate(effective, reference_date)
        logger.info("Validation completed: passed={} issues={}", validation.passed, len(validation.issues))

        # 4. Check stock + decide status (code)
        output_lines: list[StockLine] = []   # every item, shown in the response
        decide_lines: list[StockLine] = []   # items the status step looks at
        for r in resolution.items:
            if r.written_name is None:
                continue  # no product name: validation already reported it
            if r.ambiguous:
                output_lines.append(StockLine(r.written_name, None, r.requested if is_valid_quantity(r.requested) else None, None))
                continue
            line = self._stock.check([OrderItem(product_name=r.lookup_name, requested=r.requested)])[0]
            line.product_name = r.written_name  # a resolved description keeps the customer's wording
            output_lines.append(line)
            decide_lines.append(line)
        logger.info("Stock checking completed: {} item(s)", len(output_lines))
        status, issues = self._status.decide(validation.issues + resolution.issues, decide_lines)
        logger.info("Order status determined: {} (issues={})", status, len(issues))

        # 5. Build quote (code, Decimal)
        quote = self._quote.build(status, output_lines, reference_date)
        logger.info("Quote calculation: {}", "none" if quote is None else f"{len(quote.lines)} line(s), total={quote.total_inr}")

        items = [
            ResponseItem(product_name=l.product_name, product_code=l.product_code,
                         requested=l.requested, available=l.available)
            for l in output_lines
        ]
        computed = {
            "customer": (extracted.customer or "").strip() or None,
            "delivery_city": (extracted.delivery_city or "").strip() or None,
            "deadline": validation.deadline.isoformat() if validation.deadline else None,
            "status": status,
            "items": [i.model_dump() for i in items],
            "issues": issues,
        }

        # 6. Draft reply (LLM) - gets the computed result, the quote and retrieved brochure/policy chunks
        reply_context = dict(computed)
        reply_context["quote"] = quote.model_dump(mode="json") if quote else None
        reply_context["documents"] = self._reply_documents(parsed)
        logger.info("Reply generation started")
        reply = self._adapter.draft_reply(reply_context)
        logger.info("Reply generation completed")
        logger.info("Workflow completed in {:.0f} ms", (time.perf_counter() - started) * 1000)
        return OrderResponse(**computed, quote=quote, reply=reply)

    def _reply_documents(self, parsed) -> list[dict]:
        """Brochure/policy chunks for the reply. The email text is only used to embed the search query."""
        if self._retrieval is None:
            return []
        query = f"{parsed.subject}\n{parsed.body}"[: self._max_query_chars]
        try:
            chunks = self._retrieval.search(query, limit=self._reply_top_k)
        except RepositoryError:
            logger.warning("Documents could not be searched (has 'python ingest_docs.py' been run?); replying without documents")
            return []
        return [{"source": c.source, "heading": c.heading, "chunk": c.chunk} for c in chunks]
