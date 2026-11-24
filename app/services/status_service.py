"""Step 3b - status decision. Need info > Partial > Ready. Python only."""
from app.models import NEED_INFO, PARTIAL, READY
from app.services.stock_service import StockLine, is_short


class OrderStatusService:
    def decide(
        self, validation_issues: list[str], lines: list[StockLine]
    ) -> tuple[str, list[str]]:
        need_info = list(validation_issues)
        partial: list[str] = []

        for line in lines:
            if not line.found:
                need_info.append(f"{line.product_name}: not found in the catalogue")
            elif is_short(line.requested, line.available):
                partial.append(
                    f"{line.product_name}: requested {line.requested}, "
                    f"only {line.available} in stock"
                )

        if need_info:
            return NEED_INFO, need_info + partial
        if partial:
            return PARTIAL, partial
        return READY, []
