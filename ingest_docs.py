"""Fill the documents table from the docs/ folder (brochures + pricing policy).

Usage (from the project root, with the database running and .env filled in):
  python ingest_docs.py              # reads ./docs
  python ingest_docs.py path/to/docs

Safe to run more than once: the documents table (and only that table) is cleared and reloaded
in one transaction, so a failed run leaves the old data untouched.
"""
import sys
from pathlib import Path

from app.adapters.azure_openai_adapter import AzureOpenAIAdapter
from app.config import get_settings
from app.logging_config import configure_logging
from app.repositories.document_repository import DocumentRepository
from app.services.ingestion import ingest_documents


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    docs_dir = Path(argv[0] if argv else "docs")
    if not docs_dir.is_dir():
        sys.exit(f"Folder not found: {docs_dir}")
    settings = get_settings()
    configure_logging(settings.log_level)
    result = ingest_documents(docs_dir, AzureOpenAIAdapter(settings), DocumentRepository(settings.database_url))
    print(f"Ingested {result['documents']} document(s) -> {result['chunks']} chunk(s) in the documents table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
