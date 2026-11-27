"""Step 5 - build the quote. Pure Python with Decimal; the LLM never touches a number."""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.config import Settings
from app.models import NEED_INFO, Quote, QuoteLine
from app.services.stock_service import StockLine
from app.services.validation_service import is_valid_quantity

CENT = Decimal("0.01")


@dataclass(frozen=True)
class QuoteConfig:
    tiers: tuple  # ((min_quantity, discount_percent), ...) ascending
    free_delivery_threshold: Decimal
    delivery_charge: Decimal
    validity_days: int

    @classmethod
    def from_settings(cls, s: Settings) -> "QuoteConfig":
        return cls(s.quote_discount_tiers, s.quote_free_delivery_threshold_inr,
                   s.quote_delivery_charge_inr, s.quote_validity_days)


DEFAULT_CONFIG = QuoteConfig(((1, 0), (10, 5), (50, 8), (200, 12)), Decimal("50000"), Decimal("1500"), 7)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class QuoteService:
    def __init__(self, config: QuoteConfig = DEFAULT_CONFIG):
        self._cfg = config

    def discount_percent(self, quantity: int) -> int:
        discount = 0
        for min_quantity, percent in self._cfg.tiers:
            if quantity >= min_quantity:
                discount = percent
        return discount

    def build(self, status: str, lines: list[StockLine], reference_date: date) -> Quote | None:
        if status == NEED_INFO:
            return None
        quote_lines: list[QuoteLine] = []
        for line in lines:
            if not (line.found and is_valid_quantity(line.requested) and line.available and line.unit_price_inr is not None):
                continue  # not resolved, invalid quantity, or nothing available: this line is not quoted
            quantity = min(line.requested, line.available)
            discount = self.discount_percent(quantity)
            total = money(Decimal(quantity) * Decimal(line.unit_price_inr) * (100 - discount) / 100)
            quote_lines.append(QuoteLine(product_code=line.product_code, quantity=quantity,
                                         unit_price_inr=line.unit_price_inr, discount_percent=discount,
                                         line_total_inr=total))
        if not quote_lines:
            return None
        subtotal = money(sum((q.line_total_inr for q in quote_lines), Decimal("0")))
        delivery = Decimal("0") if subtotal >= self._cfg.free_delivery_threshold else self._cfg.delivery_charge
        return Quote(
            lines=quote_lines,
            subtotal_inr=subtotal,
            delivery_inr=money(delivery),
            total_inr=money(subtotal + delivery),
            valid_until=(reference_date + timedelta(days=self._cfg.validity_days)).isoformat(),
        )
