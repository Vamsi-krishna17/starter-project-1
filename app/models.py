"""Pydantic models passed between the workflow steps."""
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer, field_validator

READY = "Ready"
PARTIAL = "Partial"
NEED_INFO = "Need info"
Status = Literal["Ready", "Partial", "Need info"]


class OrderRequest(BaseModel):
    email: str = Field(..., description="The raw email text")

    @field_validator("email")
    @classmethod
    def email_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("email must not be empty")
        return v


# ---- Step 1 output (LLM structured output). Deadline is a string on purpose;
# ---- Python converts it to a date.
class OrderItem(BaseModel):
    product_name: str | None
    requested: int | None


class ExtractedOrder(BaseModel):
    customer: str | None
    delivery_city: str | None
    deadline: str | None
    items: list[OrderItem]
    repeat_order: bool  # true when the customer asks to repeat their previous order


# Result of the product-matching LLM call (codes only; Python validates every code)
class ProductMatch(BaseModel):
    product_codes: list[str]


# ---- API response contract
class ResponseItem(BaseModel):
    product_name: str
    product_code: str | None
    requested: int | None
    available: int | None


# Money is computed with Decimal; it is written to JSON as a number (two decimal places of precision).
Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]


class QuoteLine(BaseModel):
    product_code: str
    quantity: int
    unit_price_inr: int
    discount_percent: int
    line_total_inr: Money


class Quote(BaseModel):
    lines: list[QuoteLine]
    subtotal_inr: Money
    delivery_inr: Money
    total_inr: Money
    valid_until: str  # YYYY-MM-DD


class OrderResponse(BaseModel):
    customer: str | None
    delivery_city: str | None
    deadline: str | None
    items: list[ResponseItem]
    status: Status
    issues: list[str]
    quote: Quote | None = None
    reply: str
