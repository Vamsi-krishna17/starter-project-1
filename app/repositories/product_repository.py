"""All PostgreSQL access to the product catalogue and stock lives here. Read-only, fixed, parameterised SQL."""
from dataclasses import dataclass

import psycopg2
from loguru import logger

from app.config import get_settings

# Matches the supplied seed.sql: products(product_code, name, unit_price_inr) and stock(product_code, quantity_available).
# The %s is the normalised (lower-case, single-spaced) product name.
FIND_PRODUCT_SQL = r"""
SELECT p.product_code, p.name, s.quantity_available, p.unit_price_inr
FROM products p
JOIN stock s ON s.product_code = p.product_code
WHERE lower(regexp_replace(btrim(p.name), '\s+', ' ', 'g')) = %s
LIMIT 1
"""

# Used to confirm that a product code (e.g. chosen while resolving a description) really exists.
FIND_BY_CODE_SQL = """
SELECT p.product_code, p.name, s.quantity_available, p.unit_price_inr
FROM products p
JOIN stock s ON s.product_code = p.product_code
WHERE p.product_code = %s
LIMIT 1
"""


class RepositoryError(Exception):
    """Database problem (connection or query)."""


@dataclass(frozen=True)
class ProductRecord:
    product_code: str
    name: str
    available: int
    unit_price_inr: int | None = None


def fetch_rows(database_url: str, sql: str, params: tuple, register_vectors: bool = False) -> list[tuple]:
    """Run one read-only query. Shared by every repository so connection handling lives in one place."""
    conn = None
    try:
        conn = psycopg2.connect(database_url, connect_timeout=5)
        conn.set_session(readonly=True)  # reads only: stock and history are never modified
        if register_vectors:
            from pgvector.psycopg2 import register_vector

            register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except psycopg2.Error as exc:
        logger.error("Database error: {}", type(exc).__name__)
        raise RepositoryError("Could not read from the database") from exc
    finally:
        if conn is not None:
            conn.close()


class ProductRepository:
    def __init__(self, database_url: str | None = None):
        self._url = database_url or get_settings().database_url

    @staticmethod
    def _record(row: tuple) -> ProductRecord:
        return ProductRecord(
            product_code=row[0], name=row[1], available=int(row[2]),
            unit_price_inr=int(row[3]) if row[3] is not None else None,
        )

    def find_by_normalized_name(self, normalized_name: str) -> ProductRecord | None:
        rows = fetch_rows(self._url, FIND_PRODUCT_SQL, (normalized_name,))
        return self._record(rows[0]) if rows else None

    def find_by_code(self, product_code: str) -> ProductRecord | None:
        rows = fetch_rows(self._url, FIND_BY_CODE_SQL, (product_code,))
        return self._record(rows[0]) if rows else None
