"""All PostgreSQL access lives here. Read-only, fixed, parameterised SQL."""
from dataclasses import dataclass

import psycopg2
from loguru import logger

from app.config import get_settings

# Matches the supplied seed.sql: products(product_code, name) and stock(product_code, quantity_available).
# The %s is the normalised (lower-case, single-spaced) product name.
FIND_PRODUCT_SQL = r"""
SELECT p.product_code, p.name, s.quantity_available
FROM products p
JOIN stock s ON s.product_code = p.product_code
WHERE lower(regexp_replace(btrim(p.name), '\s+', ' ', 'g')) = %s
LIMIT 1
"""


class RepositoryError(Exception):
    """Database problem (connection or query)."""


@dataclass(frozen=True)
class ProductRecord:
    product_code: str
    name: str
    available: int


class ProductRepository:
    def __init__(self, database_url: str | None = None):
        self._url = database_url or get_settings().database_url

    def find_by_normalized_name(self, normalized_name: str) -> ProductRecord | None:
        conn = None
        try:
            conn = psycopg2.connect(self._url, connect_timeout=5)
            conn.set_session(readonly=True)  # stock is never modified
            with conn.cursor() as cur:
                cur.execute(FIND_PRODUCT_SQL, (normalized_name,))
                row = cur.fetchone()
        except psycopg2.Error as exc:
            logger.error("Database error: {}", type(exc).__name__)
            raise RepositoryError("Could not read the product catalogue") from exc
        finally:
            if conn is not None:
                conn.close()
        if row is None:
            return None
        return ProductRecord(product_code=row[0], name=row[1], available=int(row[2]))