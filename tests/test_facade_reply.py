from app.models import OrderItem
from app.services.order_facade import OrderFacade
from app.services.status_service import OrderStatusService
from app.services.stock_service import StockService
from app.services.validation_service import OrderValidationService
from tests.fakes import FakeAdapter, FakeRepo, order

RAW = "Subject: Order\nDate: Wed, 18 Nov 2026 09:00:00 +0530\n\nplease send 2 laptops"


class SpyAdapter(FakeAdapter):
    """Mocked LLM: records every draft_reply call and returns a known text."""

    def __init__(self, extracted):
        super().__init__(extracted)
        self.reply_calls = []

    def draft_reply(self, result):
        self.reply_calls.append(result)
        return "REPLY FROM THE LLM"


def test_process_email_calls_the_llm_once_and_returns_its_reply():
    adapter = SpyAdapter(order(items=[OrderItem(product_name="Laptop", requested=2)]))
    facade = OrderFacade(adapter, OrderValidationService(3), StockService(FakeRepo()), OrderStatusService())
    resp = facade.process_email(RAW)
    assert resp is not None
    assert resp.reply == "REPLY FROM THE LLM"          # value returned by draft_reply reaches the response
    assert len(adapter.reply_calls) == 1               # the reply LLM step ran exactly once
    assert adapter.reply_calls[0]["status"] == resp.status == "Ready"
    assert set(resp.model_dump()) == {"customer", "delivery_city", "deadline", "items", "status", "issues", "quote", "reply"}
