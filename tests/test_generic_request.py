from app.models import OrderItem
from app.prompts import REPLY_SYSTEM_PROMPT
from app.services.order_facade import OrderFacade
from app.services.status_service import OrderStatusService
from app.services.stock_service import StockService
from app.services.validation_service import OrderValidationService
from tests.fakes import FakeAdapter, FakeRepo, order

RAW = "Subject: Order\nDate: Wed, 18 Nov 2026 09:00:00 +0530\n\nwe need 10 laptops"


def test_generic_request_is_need_info_and_reply_gets_only_computed_fields():
    adapter = FakeAdapter(order(items=[OrderItem(product_name="laptops", requested=10)]))
    facade = OrderFacade(adapter, OrderValidationService(3), StockService(FakeRepo()), OrderStatusService())
    resp = facade.process_email(RAW)
    assert resp.status == "Need info" and resp.issues == ["laptops: not found in the catalogue"]
    assert set(adapter.reply_input) == {"customer", "delivery_city", "deadline", "status", "items", "issues", "quote", "documents"}


def test_reply_prompt_asks_for_brand_model_and_forbids_listing_products():
    assert "preferred brand/model" in REPLY_SYSTEM_PROMPT
    assert "Do not list, suggest or invent" in REPLY_SYSTEM_PROMPT
    assert "catalogue_options" not in REPLY_SYSTEM_PROMPT
