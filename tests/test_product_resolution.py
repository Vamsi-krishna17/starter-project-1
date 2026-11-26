"""Product resolution with FAKE retrieval, FAKE model and FAKE database (no Azure, no Postgres)."""
from app.repositories.order_history_repository import HistoryLine
from app.repositories.product_repository import ProductRecord
from app.services.order_facade import OrderFacade
from app.services.product_resolver import ProductResolver
from app.services.retrieval_service import RetrievedChunk
from app.services.status_service import OrderStatusService
from app.services.stock_service import StockService
from app.services.validation_service import OrderValidationService
from app.models import OrderItem
from tests.fakes import FakeAdapter, order

RAW = "Subject: Order\nDate: Fri, 20 Nov 2026 09:00:00 +0530\n\nplease supply"

PRODUCTS = {
    "PHN-A5": ProductRecord("PHN-A5", "Astra A5", 40, 12999),
    "PHN-X9": ProductRecord("PHN-X9", "Astra X9", 6, 34999),
    "TAB-08": ProductRecord("TAB-08", "Nimbus Tab 8 Lite", 60, 9499),
}


class Repo:
    def find_by_normalized_name(self, name):
        return next((p for p in PRODUCTS.values() if p.name.lower() == name), None)

    def find_by_code(self, code):
        return PRODUCTS.get(code)


class Retrieval:
    def __init__(self, chunks):
        self.chunks, self.queries = chunks, []

    def search(self, query, limit=None):
        self.queries.append(query)
        return self.chunks


class Matcher:
    """Fake model: returns whatever codes the test wants."""
    def __init__(self, codes):
        self.codes, self.calls = codes, 0

    def select_matching_products(self, description, chunks):
        self.calls += 1
        return self.codes


class History:
    def __init__(self, lines):
        self.lines, self.asked = lines, []

    def latest_order(self, customer):
        self.asked.append(customer)
        return self.lines


CHUNKS = [RetrievedChunk("astra_a5.md", "Specs", "Astra A5 (PHN-A5) black 128 GB 5G"),
          RetrievedChunk("astra_x9.md", "Specs", "Astra X9 (PHN-X9) black 128 GB 5G")]


def run(extracted, codes=(), history=None, chunks=CHUNKS):
    matcher = Matcher(list(codes))
    resolver = ProductResolver(Repo(), Retrieval(chunks), matcher, History(history or []))
    facade = OrderFacade(FakeAdapter(extracted), OrderValidationService(3), StockService(Repo()), OrderStatusService(), resolver=resolver)
    return facade.process_email(RAW), matcher


def items(name, qty=2):
    return [OrderItem(product_name=name, requested=qty)]


def test_exact_name_is_matched_without_rag():
    resp, matcher = run(order(items=items("  nimbus tab 8   LITE ")))
    assert resp.status == "Ready" and resp.items[0].product_code == "TAB-08" and matcher.calls == 0


def test_description_fitting_one_product_resolves_and_keeps_the_customers_wording():
    resp, _ = run(order(items=items("black 128 GB 5G phone", 3)), codes=["PHN-A5"])
    assert resp.status == "Ready"
    assert resp.items[0].product_name == "black 128 GB 5G phone" and resp.items[0].product_code == "PHN-A5"
    assert resp.quote.lines[0].product_code == "PHN-A5"


def test_description_fitting_two_products_is_ambiguous_need_info():
    resp, _ = run(order(items=items("black 128 GB 5G phone")), codes=["PHN-A5", "PHN-X9"])
    assert resp.status == "Need info" and resp.quote is None
    assert resp.items[0].product_code is None and resp.items[0].available is None
    assert any("more than one product" in i and "PHN-A5" in i and "PHN-X9" in i for i in resp.issues)


def test_description_fitting_none_is_unknown_need_info():
    resp, _ = run(order(items=items("pink 1 TB phone")), codes=[])
    assert resp.status == "Need info" and resp.issues == ["pink 1 TB phone: not found in the catalogue"]
    assert resp.items[0].product_code is None


def test_invented_code_is_never_accepted():
    resp, _ = run(order(items=items("black 128 GB 5G phone")), codes=["PHN-ZZ9"])          # not in products
    assert resp.status == "Need info" and resp.items[0].product_code is None
    resp, _ = run(order(items=items("black 128 GB 5G phone")), codes=["PHN-A5", "PHN-ZZ9"])  # one real + one invented
    assert resp.status == "Need info" and resp.items[0].product_code is None


def test_real_code_that_was_not_in_the_retrieved_chunks_is_rejected():
    resp, _ = run(order(items=items("a phone")), codes=["TAB-08"], chunks=CHUNKS)
    assert resp.status == "Need info" and resp.items[0].product_code is None


def test_no_retrieved_chunks_means_unknown():
    resp, matcher = run(order(items=items("black phone")), codes=["PHN-A5"], chunks=[])
    assert resp.status == "Need info" and matcher.calls == 0


def test_repeat_order_with_history_uses_catalogue_names_and_latest_order():
    hist = [HistoryLine("PHN-A5", "Astra A5", 20), HistoryLine("TAB-08", "Nimbus Tab 8 Lite", 10)]
    resp, _ = run(order(items=[], repeat_order=True), history=hist)
    assert resp.status == "Ready"
    assert [(i.product_name, i.product_code, i.requested) for i in resp.items] == [
        ("Astra A5", "PHN-A5", 20), ("Nimbus Tab 8 Lite", "TAB-08", 10)]
    assert resp.quote is not None and len(resp.quote.lines) == 2


def test_repeat_order_without_history_is_need_info():
    resp, _ = run(order(items=[], repeat_order=True), history=[])
    assert resp.status == "Need info" and resp.quote is None
    assert any("No previous order" in i for i in resp.issues)


def test_repeat_phrase_in_the_email_triggers_the_lookup_even_if_the_flag_is_missing():
    hist = [HistoryLine("PHN-A5", "Astra A5", 20)]
    matcher_history = History(hist)
    resolver = ProductResolver(Repo(), None, None, matcher_history)
    facade = OrderFacade(FakeAdapter(order(items=[], repeat_order=False)), OrderValidationService(3),
                         StockService(Repo()), OrderStatusService(), resolver=resolver)
    resp = facade.process_email("Date: Fri, 20 Nov 2026 09:00:00 +0530\n\nsame as last time please")
    assert matcher_history.asked == ["ABC Electronics"] and resp.status == "Ready"


def test_status_precedence_is_unchanged_need_info_beats_partial():
    resp, _ = run(order(items=[OrderItem(product_name="Astra X9", requested=50), OrderItem(product_name="Nope", requested=1)]))
    assert resp.status == "Need info"
    resp, _ = run(order(items=items("Astra X9", 50)))
    assert resp.status == "Partial" and resp.quote.lines[0].quantity == 6


def test_reply_still_generated_when_the_documents_table_is_not_loaded_yet():
    from app.repositories.product_repository import RepositoryError

    class Broken:
        def search(self, query, limit=None):
            raise RepositoryError("documents missing")

    adapter = FakeAdapter(order(items=items("Astra A5")))
    facade = OrderFacade(adapter, OrderValidationService(3), StockService(Repo()), OrderStatusService(), retrieval=Broken())
    resp = facade.process_email(RAW)
    assert resp.status == "Ready" and adapter.reply_input["documents"] == []
