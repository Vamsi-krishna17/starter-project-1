"""The RAG documents table (pgvector). Only this table is ever written, and only by the ingest script."""
from dataclasses import dataclass

import numpy as np
import psycopg2
from loguru import logger

from app.config import get_settings
from app.repositories.product_repository import RepositoryError, fetch_rows

# The %(query_embedding)s vector is passed as a parameter, never built into the SQL string.
SEARCH_SQL = """
SELECT source, heading, chunk, embedding <=> %(query_embedding)s AS distance
FROM documents
ORDER BY embedding <=> %(query_embedding)s
LIMIT %(limit)s
"""
CREATE_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector"
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id        SERIAL PRIMARY KEY,
    source    TEXT         NOT NULL,
    heading   TEXT         NOT NULL,
    chunk     TEXT         NOT NULL,
    embedding VECTOR(1536) NOT NULL
)
"""
CLEAR_SQL = "DELETE FROM documents"
INSERT_SQL = "INSERT INTO documents (source, heading, chunk, embedding) VALUES (%s, %s, %s, %s)"


@dataclass(frozen=True)
class DocumentChunk:
    source: str
    heading: str
    chunk: str
    embedding: list[float] | None = None


class DocumentRepository:
    def __init__(self, database_url: str | None = None):
        self._url = database_url or get_settings().database_url

    def search(self, query_embedding: list[float], limit: int) -> list[tuple[str, str, str, float]]:
        rows = fetch_rows(
            self._url, SEARCH_SQL,
            {"query_embedding": np.array(query_embedding), "limit": limit},
            register_vectors=True,
        )
        return [(r[0], r[1], r[2], float(r[3])) for r in rows]

    def replace_all(self, chunks: list[DocumentChunk]) -> int:
        """Clear the documents table and load the chunks in ONE transaction (safe to run twice)."""
        conn = None
        try:
            conn = psycopg2.connect(self._url, connect_timeout=5)
            from pgvector.psycopg2 import register_vector

            with conn:  # commit on success, roll back on any error
                with conn.cursor() as cur:
                    cur.execute(CREATE_EXTENSION_SQL)
                    cur.execute(CREATE_TABLE_SQL)   # no-op when the seed already created it
                    register_vector(conn)           # the vector type must exist before it is registered
                    cur.execute(CLEAR_SQL)
                    for c in chunks:
                        cur.execute(INSERT_SQL, (c.source, c.heading, c.chunk, np.array(c.embedding)))
            logger.info("documents table reloaded: {} chunk(s)", len(chunks))
            return len(chunks)
        except psycopg2.Error as exc:
            logger.error("Database error while loading documents: {}", type(exc).__name__)
            raise RepositoryError("Could not write the documents table") from exc
        finally:
            if conn is not None:
                conn.close()
