"""Step 2 - resolve what the customer wants to real catalogue products (code + RAG).

- exact catalogue name (case/space-insensitive) -> matched directly, as in Starter Project 1
- anything else is treated as a description: retrieve brochure chunks (RAG), let the model suggest
  codes, and accept ONLY when exactly one suggested code exists in products. Closest match = not a match.
- "same as last time" -> every line of the customer's latest order in order_history
"""
from dataclasses import dataclass, field

from loguru import logger

from app.models import ExtractedOrder
from app.services.stock_service import normalize_name


@dataclass
class ResolvedItem:
    written_name: str | None   # what the customer wrote (catalogue name for repeat orders)
    requested: int | None
    lookup_name: str | None    # catalogue name to look up stock; None when ambiguous / no name
    ambiguous: bool = False


@dataclass
class ResolutionResult:
    items: list[ResolvedItem] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)  # ambiguity / missing history only


class ProductResolver:
    def __init__(self, product_repo, retrieval=None, matcher=None, history_repo=None, resolve_top_k: int = 10):
        self._products = product_repo
        self._retrieval = retrieval    # RetrievalService; None disables description matching
        self._matcher = matcher        # object with select_matching_products(description, chunks)
        self._history = history_repo   # OrderHistoryRepository; None means "no history"
        self._top_k = resolve_top_k

    def resolve(self, extracted: ExtractedOrder, repeat_requested: bool) -> ResolutionResult:
        result = ResolutionResult()
        if repeat_requested:
            self._add_repeat_lines(extracted.customer, result)
        for item in extracted.items:
            name = (item.product_name or "").strip()
            if not name:
                result.items.append(ResolvedItem(None, item.requested, None))  # validation reports it
            elif self._products.find_by_normalized_name(normalize_name(name)):
                result.items.append(ResolvedItem(name, item.requested, name))
            else:
                result.items.append(self._resolve_description(name, item.requested, result))
        return result

    def _add_repeat_lines(self, customer: str | None, result: ResolutionResult) -> None:
        history = self._history.latest_order(customer) if (self._history and (customer or "").strip()) else []
        if not history:
            result.issues.append("No previous order was found for this customer, so the earlier order cannot be repeated")
            logger.info("Repeat order requested but no order history found")
            return
        logger.info("Repeat order: {} line(s) from the latest past order", len(history))
        for line in history:
            result.items.append(ResolvedItem(line.name, line.quantity, line.name))

    def _resolve_description(self, text: str, requested: int | None, result: ResolutionResult) -> ResolvedItem:
        unknown = ResolvedItem(text, requested, text)  # stock lookup will report "not found in the catalogue"
        if self._retrieval is None or self._matcher is None:
            return unknown
        chunks = self._retrieval.search(text, limit=self._top_k)
        if not chunks:
            return unknown
        suggested = list(dict.fromkeys(c.strip() for c in self._matcher.select_matching_products(text, chunks)))
        valid = []
        for code in suggested:
            record = self._products.find_by_code(code)
            if record is not None and any(code in c.chunk for c in chunks):  # real product AND seen in retrieval
                valid.append(record)
        rejected = len(suggested) - len(valid)
        logger.info("Description resolution: suggested={} valid={} rejected={}", suggested, [r.product_code for r in valid], rejected)
        if len(valid) >= 2:
            codes = ", ".join(r.product_code for r in valid)
            result.issues.append(f"{text}: matches more than one product ({codes}); please say which one you mean")
            return ResolvedItem(text, requested, None, ambiguous=True)
        if len(valid) == 1 and rejected == 0:
            return ResolvedItem(text, requested, valid[0].name)
        return unknown  # nothing fits, or the model returned a code that does not exist
