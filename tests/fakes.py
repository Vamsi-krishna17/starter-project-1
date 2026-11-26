from app.models import ExtractedOrder, OrderItem
from app.repositories.product_repository import ProductRecord

CATALOGUE = {
    "laptop": ProductRecord("LAP-101", "Laptop", 6),
    "pebble buds": ProductRecord("EAR-01", "Pebble Buds", 350),
    "nimbus tab 10": ProductRecord("TAB-10", "Nimbus Tab 10", 25),
}


class FakeRepo:
    def find_by_normalized_name(self, name):
        return CATALOGUE.get(name)


class FakeAdapter:
    """Stands in for Azure OpenAI. Records what the reply step receives."""

    def __init__(self, extracted: ExtractedOrder):
        self.extracted = extracted
        self.reply_input = None
        self.extract_input = None

    def extract_order(self, subject, body, reference_date):
        self.extract_input = (subject, body, reference_date)
        return self.extracted

    def draft_reply(self, result):
        self.reply_input = result
        return "Dear customer, ..."


def order(customer="ABC Electronics", city="Hyderabad", deadline="2026-11-25", items=None, repeat_order=False):
    items = items if items is not None else [OrderItem(product_name="Laptop", requested=2)]
    return ExtractedOrder(customer=customer, delivery_city=city, deadline=deadline, items=items, repeat_order=repeat_order)
