"""Chunking, ingestion, embeddings and retrieval with fakes (no Azure, no Postgres)."""
import pytest
from types import SimpleNamespace

from app.adapters.azure_openai_adapter import AzureOpenAIAdapter, LLMError
from app.observability import LLMTracer
from app.repositories.document_repository import DocumentChunk
from app.services.ingestion import chunk_markdown, ingest_documents, read_documents
from app.services.retrieval_service import RetrievalService
from tests.test_observability import RecordingTracer, settings

BROCHURE = """# Astra A5 (PHN-A5)

Short intro line.

## Connectivity
Supports 5G and Wi-Fi 6.

## Storage
128 GB, black.

## Empty section

"""


def test_chunks_are_split_by_heading_and_keep_source_heading_and_product_name():
    chunks = chunk_markdown("astra_a5.md", BROCHURE)
    assert [c.heading for c in chunks] == ["Overview", "Connectivity", "Storage"]   # empty section dropped
    assert all(c.source == "astra_a5.md" for c in chunks)
    assert all("Astra A5 (PHN-A5)" in c.chunk for c in chunks)                         # name + code in EVERY chunk
    assert "5G" in chunks[1].chunk and "128 GB" not in chunks[1].chunk


def test_read_documents_skips_unsupported_files(tmp_path):
    (tmp_path / "a.md").write_text("# A\n## S\ntext")
    (tmp_path / "b.pdf").write_bytes(b"%PDF")
    (tmp_path / ".hidden.md").write_text("x")
    assert [n for n, _ in read_documents(tmp_path)] == ["a.md"]


class FakeEmbedder:
    def __init__(self):
        self.batches = []

    def generate_embeddings(self, texts):
        self.batches.append(list(texts))
        return [[float(len(t))] * 3 for t in texts]

    def generate_embedding(self, text):
        return self.generate_embeddings([text])[0]


class FakeDocRepo:
    def __init__(self):
        self.stored = None

    def replace_all(self, chunks):
        self.stored = chunks            # replaced, not appended: running twice cannot duplicate
        return len(chunks)

    def search(self, embedding, limit):
        self.last = (embedding, limit)
        return [("astra_a5.md", "Storage", "Astra A5 (PHN-A5) ... 128 GB", 0.12)]


def test_ingestion_embeds_every_chunk_and_stores_them_with_metadata(tmp_path):
    (tmp_path / "astra_a5.md").write_text(BROCHURE)
    (tmp_path / "pricing_policy.md").write_text("# Pricing policy\n## Delivery\nFree above 50000.")
    emb, repo = FakeEmbedder(), FakeDocRepo()
    result = ingest_documents(tmp_path, emb, repo)
    assert result == {"documents": 2, "chunks": 4}
    assert sum(len(b) for b in emb.batches) == 4
    assert {(c.source, c.heading) for c in repo.stored} == {
        ("astra_a5.md", "Overview"), ("astra_a5.md", "Connectivity"), ("astra_a5.md", "Storage"), ("pricing_policy.md", "Delivery")}
    assert all(c.embedding for c in repo.stored)
    again = ingest_documents(tmp_path, emb, repo)                 # second run: same result, no growth
    assert again == result and len(repo.stored) == 4


def test_ingestion_with_nothing_to_ingest_changes_nothing(tmp_path):
    repo = FakeDocRepo()
    with pytest.raises(ValueError):
        ingest_documents(tmp_path, FakeEmbedder(), repo)
    assert repo.stored is None


def test_retrieval_embeds_the_query_with_the_same_embedder_and_returns_source_heading_chunk():
    emb, repo = FakeEmbedder(), FakeDocRepo()
    out = RetrievalService(emb, repo, default_limit=5).search("black 128 GB phone")
    assert emb.batches == [["black 128 GB phone"]] and repo.last[1] == 5
    assert (out[0].source, out[0].heading, out[0].distance) == ("astra_a5.md", "Storage", 0.12)
    assert "PHN-A5" in out[0].chunk
    RetrievalService(emb, repo).search("x", limit=10)
    assert repo.last[1] == 10


# ---- embeddings through the Azure adapter (fake client) ----
def fake_embedding_client(dims=1536, usage=SimpleNamespace(prompt_tokens=9, total_tokens=9)):
    calls = []

    def create(model, input):
        calls.append((model, input))
        data = [SimpleNamespace(index=i, embedding=[0.1] * dims) for i in reversed(range(len(input)))]
        return SimpleNamespace(data=data, usage=usage)

    return SimpleNamespace(embeddings=SimpleNamespace(create=create)), calls


def test_adapter_embeds_a_list_with_the_embedding_deployment_and_traces_tokens():
    client, calls = fake_embedding_client()
    tracer = RecordingTracer()
    a = AzureOpenAIAdapter(settings(embedding_deployment="text-embedding-3-small"), client=client, tracer=tracer)
    vectors = a.generate_embeddings(["one", "two"])
    assert len(vectors) == 2 and all(len(v) == 1536 for v in vectors)
    assert calls == [("text-embedding-3-small", ["one", "two"])]
    assert tracer.started[0][0] == "embedding_generation"
    assert tracer.finished[0]["outputs"]["usage_metadata"] == {"input_tokens": 9, "total_tokens": 9}
    assert len(a.generate_embedding("single")) == 1536


def test_adapter_rejects_wrong_dimensions():
    client, _ = fake_embedding_client(dims=3072)       # e.g. text-embedding-3-large
    a = AzureOpenAIAdapter(settings(), client=client, tracer=RecordingTracer())
    with pytest.raises(LLMError) as err:
        a.generate_embedding("x")
    assert err.value.category == "invalid_response" and "1536" in str(err.value)


# ---- your folder layout: docs/brochures/<CODE>.md + docs/pricing_policy.md ----
def test_brochures_in_a_subfolder_are_found_and_the_product_code_from_the_file_name_is_in_every_chunk(tmp_path):
    (tmp_path / "brochures").mkdir()
    (tmp_path / "brochures" / "CHG-65.md").write_text("# Volt 65W Charger\n## Output\n65 watts.\n## Ports\nUSB-C.")
    (tmp_path / "pricing_policy.md").write_text("# Pricing policy\n## Delivery\nFree above 50000.")
    assert [n for n, _ in read_documents(tmp_path)] == ["CHG-65.md", "pricing_policy.md"]
    chunks = chunk_markdown("CHG-65.md", (tmp_path / "brochures" / "CHG-65.md").read_text())
    assert all("Volt 65W Charger" in c.chunk and "CHG-65" in c.chunk for c in chunks)
    assert [c.source for c in chunks] == ["CHG-65.md", "CHG-65.md"]
