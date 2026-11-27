"""Step 2 - resolve what the customer wants to real catalogue products (code + RAG).

Order of checks for every requested product text (the first that applies decides):
  1. exact catalogue name (ignoring case, spaces, outer punctuation)
  2. exact product code (catalogue data), e.g. "TAB-08"
  3. an explicitly configured alias (PRODUCT_ALIASES)
  4. SIMILAR BUT DIFFERENT: the words match a catalogue name but its model/identifier does not
     ("Nimbus Tab 12" vs "Nimbus Tab 10") -> unknown, no product is chosen
  5. a description ("black 128 GB 5G phone"): RAG chunks -> the model only SUGGESTS codes -> Python
     accepts a code only if it exists, was seen in the retrieved chunks AND every model number /
     specification in the customer's text is found in that product's name or brochure.
     Exactly one such code = match; two or more = ambiguous; none = unknown.
"Same as last time" reads the customer's latest order from order_history.
Embedding similarity is never used as proof of equivalence.
"""
import re
from dataclasses import dataclass, field

from loguru import logger

from app.models import ExtractedOrder
from app.services.stock_service import normalize_name

# ---- token helpers ---------------------------------------------------------------
_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STOPWORDS = {"the", "a", "an", "of", "for", "and", "or", "pc", "pcs", "unit", "units", "no", "nos", "new"}


def tokens(text: str) -> list[str]:
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text.lower())  # 20,000 -> 20000
    return _TOKEN.findall(text)


def _has_digit(token: str) -> bool:
    return any(c.isdigit() for c in token)


def _parts(token: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?|[a-z]+", token)  # 128gb -> 128, gb


def evidence_tokens(text: str) -> set[str]:
    found = set(tokens(text))
    for t in list(found):
        found.update(_parts(t))
    return found


def _identifiers_present(identifiers: list[str], pool: set[str]) -> bool:
    for t in identifiers:
        if t in pool:
            continue
        parts = _parts(t)
        if len(parts) > 1 and all(p in pool for p in parts):  # "128gb" written as "128 GB"
            continue
        return False
    return True


def _split_request(text: str) -> tuple[set[str], list[str]]:
    toks = tokens(text)
    words = {t for t in toks if not _has_digit(t) and t not in _STOPWORDS}
    identifiers = [t for t in toks if _has_digit(t)]
    return words, identifiers


def fits_candidate(text: str, name: str, evidence: str) -> bool:
    """Do the model numbers / specifications in the customer's text match this catalogue product?
    If the customer's words all appear in the product NAME (they wrote a name), the identifiers must be
    in the NAME. Otherwise (a description) they may also be found in the product's brochure text."""
    words, identifiers = _split_request(text)
    name_pool = evidence_tokens(name)
    pool = name_pool if words <= name_pool else name_pool | evidence_tokens(evidence)
    return _identifiers_present(identifiers, pool)


def similar_but_different(text: str, catalogue_names: list[str]) -> bool:
    """True when the customer's words match catalogue names but no such name carries the model/identifier
    they wrote (e.g. 'Nimbus Tab 12' when only 'Nimbus Tab 10' and 'Nimbus Tab 8 Lite' exist)."""
    words, identifiers = _split_request(text)
    if not words or not identifiers:
        return False
    close = [n for n in catalogue_names if words <= evidence_tokens(n)]
    return bool(close) and not any(_identifiers_present(identifiers, evidence_tokens(n)) for n in close)


_OUTER_PUNCT = " \t\r\n.,;:!?\"'()[]"


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
    def __init__(self, product_repo, retrieval=None, matcher=None, history_repo=None,
                 resolve_top_k: int = 10, aliases: dict[str, str] | None = None):
        self._products = product_repo
        self._retrieval = retrieval    # RetrievalService; None disables description matching
        self._matcher = matcher        # object with select_matching_products(description, chunks)
        self._history = history_repo   # OrderHistoryRepository; None means "no history"
        self._top_k = resolve_top_k
        self._aliases = {" ".join(k.lower().split()): v for k, v in (aliases or {}).items()}

    def resolve(self, extracted: ExtractedOrder, repeat_requested: bool) -> ResolutionResult:
        result = ResolutionResult()
        self._catalogue_names: list[str] | None = None  # loaded at most once per request
        if repeat_requested:
            self._add_repeat_lines(extracted.customer, result)
        for item in extracted.items:
            name = (item.product_name or "").strip()
            if not name:
                result.items.append(ResolvedItem(None, item.requested, None))  # validation reports it
            else:
                result.items.append(self._resolve_text(name, item.requested, result))
        return result

    # ---- one requested product ----------------------------------------------------
    def _resolve_text(self, text: str, requested: int | None, result: ResolutionResult) -> ResolvedItem:
        clean = text.strip(_OUTER_PUNCT) or text
        record = self._products.find_by_normalized_name(normalize_name(clean))
        if record:
            logger.info("Product resolution: exact name -> {}", record.product_code)
            return ResolvedItem(text, requested, record.name)

        if " " not in clean and hasattr(self._products, "find_by_code"):   # a product code written by the customer
            record = self._products.find_by_code(clean.upper())
            if record:
                logger.info("Product resolution: product code -> {}", record.product_code)
                return ResolvedItem(text, requested, record.name)

        alias_target = self._aliases.get(" ".join(clean.lower().split()))
        if alias_target:
            record = self._products.find_by_normalized_name(normalize_name(alias_target))
            if record:
                logger.info("Product resolution: configured alias -> {}", record.product_code)
                return ResolvedItem(text, requested, record.name)
            logger.warning("Configured alias points to a product that is not in the catalogue: {}", alias_target)

        if self._retrieval is None or self._matcher is None:
            return ResolvedItem(text, requested, text)  # stock lookup reports "not found in the catalogue"

        if similar_but_different(clean, self._names()):
            logger.info("Product resolution: rejected, similar name but a different model/identifier")
            return ResolvedItem(text, requested, text)  # unknown: no other product is chosen
        return self._resolve_description(text, requested, result)

    def _names(self) -> list[str]:
        if self._catalogue_names is None:
            self._catalogue_names = [name for _, name in self._products.list_products()]
        return self._catalogue_names

    def _resolve_description(self, text: str, requested: int | None, result: ResolutionResult) -> ResolvedItem:
        unknown = ResolvedItem(text, requested, text)  # stock lookup will report "not found in the catalogue"
        chunks = self._retrieval.search(text, limit=self._top_k)
        if not chunks:
            return unknown
        suggested = list(dict.fromkeys(c.strip() for c in self._matcher.select_matching_products(text, chunks)))
        valid = []
        for code in suggested:
            record = self._products.find_by_code(code)                       # authoritative catalogue
            evidence = " ".join(c.chunk for c in chunks if code in c.chunk)  # empty = never seen in retrieval
            if record is not None and evidence and fits_candidate(text, record.name, evidence):
                valid.append(record)
        rejected = len(suggested) - len(valid)
        logger.info("Description resolution: suggested={} accepted={} rejected={}", suggested, [r.product_code for r in valid], rejected)
        if len(valid) >= 2:
            codes = ", ".join(r.product_code for r in valid)
            result.issues.append(f"{text}: matches more than one product ({codes}); please say which one you mean")
            return ResolvedItem(text, requested, None, ambiguous=True)
        if len(valid) == 1 and rejected == 0:
            return ResolvedItem(text, requested, valid[0].name)
        return unknown  # nothing fits, or a suggestion failed verification

    # ---- repeat orders ------------------------------------------------------------
    def _add_repeat_lines(self, customer: str | None, result: ResolutionResult) -> None:
        history = self._history.latest_order(customer) if (self._history and (customer or "").strip()) else []
        if not history:
            result.issues.append("No previous order was found for this customer, so the earlier order cannot be repeated")
            logger.info("Repeat order requested but no order history found")
            return
        logger.info("Repeat order: {} line(s) from the latest past order", len(history))
        for line in history:
            result.items.append(ResolvedItem(line.name, line.quantity, line.name))