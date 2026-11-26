"""Past orders for "same as last time" requests. Read-only."""
from dataclasses import dataclass

from app.config import get_settings
from app.repositories.product_repository import fetch_rows

# Real schema (02_sp2_seed.sql): order_history(order_id, customer, order_date, product_code, quantity),
# primary key (order_id, product_code): one row per order line.
# The customer's LATEST order = the order with the latest order_date (ties: highest order_id);
# every line of that order is returned. Customer match: exact name, ignoring case (and outer spaces).
LATEST_ORDER_SQL = """
SELECT oh.product_code, p.name, oh.quantity
FROM order_history oh
JOIN products p ON p.product_code = oh.product_code
WHERE oh.order_id = (
    SELECT order_id
    FROM order_history
    WHERE lower(btrim(customer)) = %s
    ORDER BY order_date DESC, order_id DESC
    LIMIT 1
)
ORDER BY oh.product_code
"""


@dataclass(frozen=True)
class HistoryLine:
    product_code: str
    name: str
    quantity: int


class OrderHistoryRepository:
    def __init__(self, database_url: str | None = None):
        self._url = database_url or get_settings().database_url

    def latest_order(self, customer: str) -> list[HistoryLine]:
        rows = fetch_rows(self._url, LATEST_ORDER_SQL, (customer.strip().lower(),))
        return [HistoryLine(product_code=r[0], name=r[1], quantity=int(r[2])) for r in rows]
