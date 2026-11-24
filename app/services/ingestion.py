"""Document ingestion: read docs/, split by '## ' headings, embed, store in the documents table."""
import re
from pathlib import Path

from loguru import logger

from app.repositories.document_repository import DocumentChunk

SUPPORTED_SUFFIXES = {".md", ".txt"}
EMBED_BATCH_SIZE = 16


def read_documents(docs_dir: Path) -> list[tuple[str, str]]:
    """(file name, text) for every supported file. Other file types are reported, not guessed at."""
    docs = []
    for path in sorted(p for p in docs_dir.rglob("*") if p.is_file() and not p.name.startswith(".")):  # includes docs/brochures/
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            logger.warning("Skipping unsupported file type: {}", path.name)
            continue
        docs.append((path.name, path.read_text(encoding="utf-8-sig")))
    return docs


def chunk_markdown(source: str, text: str) -> list[DocumentChunk]:
    """One chunk per '## ' section. The document title (first '# ' line, which carries the product
    name and code) is repeated in every chunk's text so a retrieved chunk always names its product."""
    title = ""
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    intro: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            current = (line[3:].strip(), [])
            sections.append(current)
        elif line.startswith("# ") and not title and current is None:
            title = line[2:].strip()
        elif current is not None:
            current[1].append(line)
        else:
            intro.append(line)
    if "".join(intro).strip():
        sections.insert(0, ("Overview", intro))

    chunks = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        stem = Path(source).stem  # brochure file names are the product codes (e.g. CHG-65.md)
        header = f"{title} [{stem}]\n" if title else f"[{stem}]\n"
        chunks.append(DocumentChunk(source=source, heading=heading, chunk=f"{header}## {heading}\n{body}"))
    return chunks


def ingest_documents(docs_dir: Path, embedder, repo) -> dict:
    docs = read_documents(docs_dir)
    chunks: list[DocumentChunk] = []
    for source, text in docs:
        file_chunks = chunk_markdown(source, text)
        if not file_chunks:
            logger.warning("No '## ' sections found in {}", source)
        chunks.extend(file_chunks)
    logger.info("Ingestion: {} document(s), {} chunk(s)", len(docs), len(chunks))
    if not chunks:
        raise ValueError(f"No chunks produced from {docs_dir}: nothing was changed in the database")

    embedded: list[DocumentChunk] = []
    for i in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[i:i + EMBED_BATCH_SIZE]
        vectors = embedder.generate_embeddings([c.chunk for c in batch])
        embedded.extend(DocumentChunk(c.source, c.heading, c.chunk, v) for c, v in zip(batch, vectors))
        logger.info("Embedded {}/{} chunk(s)", len(embedded), len(chunks))

    stored = repo.replace_all(embedded)  # embeds first, then clears + loads in one transaction
    return {"documents": len(docs), "chunks": stored}
