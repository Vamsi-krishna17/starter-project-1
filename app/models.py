"""Pydantic models passed between the workflow steps."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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


# ---- API response contract
class ResponseItem(BaseModel):
    product_name: str
    product_code: str | None
    requested: int | None
    available: int | None


class OrderResponse(BaseModel):
    customer: str | None
    delivery_city: str | None
    deadline: str | None
    items: list[ResponseItem]
    status: Status
    issues: list[str]
    reply: str
