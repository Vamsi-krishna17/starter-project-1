"""Step 2 - validation. Pure Python, no LLM, no database."""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.models import ExtractedOrder

PREVIOUS_ORDER_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bsame as (last|previous|before)\b",
        r"\bas (last time|before|usual)\b",
        r"\b(previous|last|earlier|usual) order\b",
        r"\bre-?order\b",
        r"\brepeat (my|our|the) (last |previous )?order\b",
        r"\bthe usual\b",
    )
]


@dataclass
class ValidationResult:
    issues: list[str] = field(default_factory=list)
    deadline: date | None = None

    @property
    def passed(self) -> bool:
        return not self.issues


def is_valid_quantity(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class OrderValidationService:
    def __init__(self, lead_time_days: int, require_deadline: bool = False):
        self.lead_time_days = lead_time_days
        self.require_deadline = require_deadline

    def validate(
        self, extracted: ExtractedOrder, reference_date: date, body: str = ""
    ) -> ValidationResult:
        result = ValidationResult()

        if not (extracted.customer or "").strip():
            result.issues.append("Customer (organisation name) is missing")
        if not (extracted.delivery_city or "").strip():
            result.issues.append("Delivery city is missing")

        if not extracted.items:
            result.issues.append("No items were requested")
        for item in extracted.items:
            name = (item.product_name or "").strip()
            if not name:
                result.issues.append("An item has no product name")
            elif not is_valid_quantity(item.requested):
                result.issues.append(f"{name}: quantity is missing or invalid")

        self._check_deadline(extracted.deadline, reference_date, result)

        if any(p.search(body) for p in PREVIOUS_ORDER_PATTERNS):
            result.issues.append(
                "The email refers to a previous order, which cannot be looked up; "
                "please list the items and quantities"
            )
        return result

    def _check_deadline(
        self, raw: str | None, reference_date: date, result: ValidationResult
    ) -> None:
        if raw is None or not raw.strip():
            if self.require_deadline:
                result.issues.append("Deadline is missing")
            return  # optional by default (official guide)
        try:
            deadline = date.fromisoformat(raw.strip())
        except ValueError:
            result.issues.append(f"Deadline '{raw}' is not a valid date")
            return
        earliest = reference_date + timedelta(days=self.lead_time_days)
        if deadline < earliest:
            result.issues.append(
                f"Deadline {deadline.isoformat()} is too soon: it must be at least "
                f"{self.lead_time_days} days after the email date ({reference_date.isoformat()})"
            )
            return
        result.deadline = deadline
