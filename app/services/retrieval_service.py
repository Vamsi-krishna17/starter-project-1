"""RAG search: embed the query, then ask pgvector for the closest chunks. Retrieval only - no decisions."""
from dataclasses import dataclass

from loguru import logger

from app.repositories.document_repository import DocumentRepository


@dataclass(frozen=True)
class RetrievedChunk:
    source: str
    heading: str
    chunk: str
    distance: float | None = None


class RetrievalService:
    def __init__(self, embedder, document_repo: DocumentRepository, default_limit: int = 5):
        self._embedder = embedder  # the AzureOpenAIAdapter: same embedding model for documents and queries
        self._repo = document_repo
        self._limit = default_limit

    def search(self, query: str, limit: int | None = None) -> list[RetrievedChunk]:
        limit = limit or self._limit
        embedding = self._embedder.generate_embedding(query)
        rows = self._repo.search(embedding, limit)
        chunks = [RetrievedChunk(source=r[0], heading=r[1], chunk=r[2], distance=r[3]) for r in rows]
        logger.info("Retrieval: query_chars={} limit={} returned={}", len(query), limit, len(chunks))
        for c in chunks:
            logger.info("Retrieved: source={} heading={!r} distance={}", c.source, c.heading,
                        None if c.distance is None else round(c.distance, 4))
        return chunks
