"""Step 3 - stock check. The comparison is separate from the DB query."""
from dataclasses import dataclass

from app.models import OrderItem
from app.repositories.product_repository import ProductRepository
from app.services.validation_service import is_valid_quantity


def normalize_name(name: str) -> str:
    """Case-insensitive, extra spaces removed. Nothing fuzzier than that."""
    return " ".join(name.lower().split())


def is_short(requested: int | None, available: int | None) -> bool:
    """True only when the request is valid, the product exists and stock is too low."""
    return is_valid_quantity(requested) and available is not None and requested > available


@dataclass
class StockLine:
    product_name: str
    product_code: str | None
    requested: int | None
    available: int | None

    @property
    def found(self) -> bool:
        return self.product_code is not None


class StockService:
    def __init__(self, repository: ProductRepository):
        self._repo = repository

    def check(self, items: list[OrderItem]) -> list[StockLine]:
        lines: list[StockLine] = []
        for item in items:
            name = (item.product_name or "").strip()
            if not name:
                continue  
            record = self._repo.find_by_normalized_name(normalize_name(name))
            lines.append(
                StockLine(
                    product_name=name,
                    product_code=record.product_code if record else None,
                    requested=item.requested if is_valid_quantity(item.requested) else None,
                    available=record.available if record else None,
                )
            )
        return lines
