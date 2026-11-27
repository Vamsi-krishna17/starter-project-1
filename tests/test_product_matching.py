"""General product-matching behaviour: names, aliases, codes, similar names, specs, unknown, ambiguous.
Everything is fake (catalogue, retrieval, model): no Azure, no database. Each test checks the chosen product
code AND the resulting status, stock and quote."""
import pytest

from app.models import OrderItem
from app.repositories.product_repository import ProductRecord
from app.services.order_facade import OrderFacade
from app.services.product_resolver import ProductResolver, fits_candidate, similar_but_different
from app.services.retrieval_service import RetrievedChunk
from app.services.status_service import OrderStatusService
from app.services.stock_service import StockService
from app.services.validation_service import OrderValidationService
from tests.fakes import FakeAdapter, order

RAW = "Subject: Order\nDate: Fri, 20 Nov 2026 09:00:00 +0530\n\nplease supply"

CATALOGUE = {
    "TAB-10": ProductRecord("TAB-10", "Nimbus Tab 10", 25, 14999),
    "TAB-08": ProductRecord("TAB-08", "Nimbus Tab 8 Lite", 60, 9499),
    "PHN-A5": ProductRecord("PHN-A5", "Astra A5", 40, 12999),
    "PHN-X9": ProductRecord("PHN-X9", "Astra X9", 6, 34999),
    "EAR-01": ProductRecord("EAR-01", "Pebble Buds", 350, 1299),
    "EAR-02": ProductRecord("EAR-02", "Pebble Buds Pro", 80, 3499),
    "CHG-65": ProductRecord("CHG-65", "Volt 65W Charger", 200, 1499),
    "LAP-14": ProductRecord("LAP-14", "Corelink Book 14", 12, 52000),
}
# what the brochures say (note: the Tab 10 brochure contains the number 12 on purpose)
BROCHURES = {
    "TAB-10": "Nimbus Tab 10 (TAB-10) 10-inch tablet, grey, battery lasts 12 hours",
    "TAB-08": "Nimbus Tab 8 Lite (TAB-08) 8-inch tablet, silver",
    "PHN-A5": "Astra A5 (PHN-A5) black, 64 GB storage, 4G only",
    "PHN-X9": "Astra X9 (PHN-X9) black, 128GB storage, 5G",
    "EAR-01": "Pebble Buds (EAR-01) wireless earbuds, white",
    "EAR-02": "Pebble Buds Pro (EAR-02) wireless earbuds with noise cancelling",
    "CHG-65": "Volt 65W Charger (CHG-65) USB-C fast charger",
    "LAP-14": "Corelink Book 14 (LAP-14) 14-inch laptop",
}


class Repo:
    def find_by_normalized_name(self, name):
        return next((p for p in CATALOGUE.values() if " ".join(p.name.lower().split()) == name), None)

    def find_by_code(self, code):
        return CATALOGUE.get(code)

    def list_products(self):
        return [(p.product_code, p.name) for p in CATALOGUE.values()]


class Retrieval:
    """Fake RAG: always returns every brochure (the worst case for wrong matches)."""
    def search(self, query, limit=None):
        return [RetrievedChunk(f"{c}.md", "Specs", t) for c, t in BROCHURES.items()]


class Matcher:
    """Fake model: returns the codes the test wants, even bad ones. Python must still verify them."""
    def __init__(self, codes=()):
        self.codes, self.calls = list(codes), 0

    def select_matching_products(self, description, chunks):
        self.calls += 1
        return self.codes


def run(name, qty=2, model_codes=(), aliases=None, extra_items=()):
    matcher = Matcher(model_codes)
    resolver = ProductResolver(Repo(), Retrieval(), matcher, None, aliases=aliases)
    items = [OrderItem(product_name=name, requested=qty), *extra_items]
    facade = OrderFacade(FakeAdapter(order(items=items)), OrderValidationService(3), StockService(Repo()),
                         OrderStatusService(), resolver=resolver)
    return facade.process_email(RAW), matcher


# ---- A. exact names (case, spaces, harmless punctuation) ----
@pytest.mark.parametrize("written", ["Nimbus Tab 8 Lite", "  NIMBUS   tab 8 lite ", "nimbus tab 8 lite.", '"Nimbus Tab 8 Lite",'])
def test_exact_names_with_harmless_formatting(written):
    resp, matcher = run(written)
    assert resp.items[0].product_code == "TAB-08" and resp.items[0].available == 60
    assert resp.status == "Ready" and resp.quote.lines[0].product_code == "TAB-08" and matcher.calls == 0


def test_names_with_model_numbers_pick_the_right_model():
    for written, code in [("Nimbus Tab 10", "TAB-10"), ("Astra X9", "PHN-X9"), ("Astra A5", "PHN-A5"), ("Volt 65W Charger", "CHG-65")]:
        assert run(written)[0].items[0].product_code == code


# ---- B. product code and explicit aliases (catalogue data / configuration only) ----
def test_a_product_code_written_by_the_customer_resolves():
    resp, _ = run("tab-08")
    assert resp.items[0].product_code == "TAB-08" and resp.status == "Ready"


def test_explicit_alias_resolves_and_only_configured_aliases_do():
    resp, _ = run("pebble earbuds standard", aliases={"Pebble Earbuds Standard": "Pebble Buds"})
    assert resp.items[0].product_code == "EAR-01" and resp.status == "Ready"
    resp, _ = run("pebble earbuds standard")                                       # not configured: not an equivalent
    assert resp.items[0].product_code is None and resp.status == "Need info"
    resp, _ = run("ghost alias", aliases={"ghost alias": "Product That Does Not Exist"})
    assert resp.items[0].product_code is None and resp.status == "Need info"


# ---- C. similar names with a different model number: NEVER chosen (the Nimbus Tab 12 regression) ----
@pytest.mark.parametrize("written", [
    "Nimbus Tab 12", "nimbus tab 12", "the Nimbus Tab 12", "Nimbus Tab 12.", "Corelink Book 13", "Astra X10",
    "Volt 100W Charger", "Pebble Buds 2",
])
def test_similar_name_with_a_different_model_is_unknown_even_if_the_model_suggests_the_closest(written):
    resp, matcher = run(written, qty=30, model_codes=["TAB-10", "LAP-14", "PHN-X9", "CHG-65", "EAR-01"])
    assert resp.items[0].product_code is None and resp.items[0].available is None
    assert resp.status == "Need info" and resp.quote is None
    assert any("not found in the catalogue" in i for i in resp.issues)
    assert matcher.calls == 0                                   # rejected before the model is even asked


def test_nimbus_tab_12_is_not_nimbus_tab_10_regression():
    resp, _ = run("Nimbus Tab 12", qty=30, model_codes=["TAB-10"])
    assert (resp.status, resp.items[0].product_code, resp.items[0].available, resp.quote) == ("Need info", None, None, None)
    assert resp.issues == ["Nimbus Tab 12: not found in the catalogue"]       # no stock shortage for TAB-10 is reported
    assert run("Nimbus Tab 10", qty=30)[0].status == "Partial"                # the real Tab 10 still works (25 in stock)


def test_a_brochure_that_mentions_the_number_cannot_rescue_a_name_with_the_wrong_model():
    # even if the model were asked and returned TAB-10, "Tab 10" brochure text containing "12" must not help
    assert not fits_candidate("Nimbus Tab 12", "Nimbus Tab 10", BROCHURES["TAB-10"])


# ---- D/E. specifications ----
def test_description_matching_exactly_one_product_by_specification():
    resp, _ = run("black 128 GB 5G phones", qty=5, model_codes=["PHN-X9"])
    assert resp.items[0].product_name == "black 128 GB 5G phones" and resp.items[0].product_code == "PHN-X9"
    assert resp.items[0].available == 6 and resp.status == "Ready"


def test_wrong_storage_is_rejected_even_if_the_model_suggests_the_product():
    resp, _ = run("black 256 GB 5G phone", model_codes=["PHN-X9"])              # brochure says 128GB
    assert resp.items[0].product_code is None and resp.status == "Need info"
    resp, _ = run("black 128 GB phone", model_codes=["PHN-A5"])                 # A5 is 64 GB
    assert resp.items[0].product_code is None and resp.status == "Need info"


def test_size_and_other_specifications_must_appear_in_the_brochure():
    assert fits_candidate("10-inch grey tablets", "Nimbus Tab 10", BROCHURES["TAB-10"])
    assert not fits_candidate("12-inch tablet", "Nimbus Tab 8 Lite", BROCHURES["TAB-08"])
    assert fits_candidate("8GB RAM", "X", "X has 8 GB RAM") and fits_candidate("20,000 mAh bank", "Y", "Y 20000 mAh")


def test_partial_name_with_the_right_model_is_resolved_only_through_verification():
    resp, _ = run("Nimbus Tab 8", model_codes=["TAB-08"])
    assert resp.items[0].product_code == "TAB-08" and resp.status == "Ready"
    resp, _ = run("Nimbus Tab 8", model_codes=["TAB-10"])                       # model suggests the wrong one: "8" is not in its name
    assert resp.items[0].product_code is None and resp.status == "Need info"


# ---- unknown and ambiguous ----
def test_unknown_product_is_rejected_and_not_priced_or_stocked():
    resp, _ = run("hoverboard", model_codes=[])
    assert resp.items[0].product_code is None and resp.items[0].available is None
    assert resp.status == "Need info" and resp.quote is None


def test_model_inventing_a_code_is_rejected():
    resp, _ = run("flying carpet", model_codes=["XXX-99"])
    assert resp.items[0].product_code is None and resp.status == "Need info"


def test_ambiguous_names_are_not_guessed():
    resp, _ = run("wireless earbuds", qty=20, model_codes=["EAR-01", "EAR-02"])
    assert resp.items[0].product_code is None and resp.items[0].available is None
    assert resp.status == "Need info" and resp.quote is None
    assert any("EAR-01" in i and "EAR-02" in i and "more than one" in i for i in resp.issues)
    resp, _ = run("Nimbus Tab", model_codes=["TAB-10", "TAB-08"])               # no model given: both plausible
    assert resp.status == "Need info" and resp.items[0].product_code is None


# ---- several products in one email ----
def test_valid_product_next_to_an_unknown_one_keeps_the_valid_line_but_no_quote():
    resp, _ = run("Corelink Book 13", qty=6, extra_items=[OrderItem(product_name="Volt 65W Charger", requested=6)])
    codes = {i.product_name: i.product_code for i in resp.items}
    assert codes == {"Corelink Book 13": None, "Volt 65W Charger": "CHG-65"}
    assert resp.status == "Need info" and resp.quote is None


def test_each_product_in_an_email_is_resolved_independently():
    resp, _ = run("Astra X9", qty=10, model_codes=["PHN-X9"], extra_items=[
        OrderItem(product_name="black 128 GB 5G phones", requested=1),    # description fitting X9 too
        OrderItem(product_name="Nimbus Tab 12", requested=3),             # unknown
        OrderItem(product_name="Pebble Buds", requested=20),
    ])
    assert [i.product_code for i in resp.items] == ["PHN-X9", "PHN-X9", None, "EAR-01"]
    by_name = {i.product_name: i for i in resp.items}
    assert by_name["Astra X9"].available == 6 and by_name["Pebble Buds"].product_code == "EAR-01"
    assert by_name["Nimbus Tab 12"].product_code is None
    assert resp.status == "Need info" and resp.quote is None


def test_partial_stock_after_correct_resolution_still_quotes_the_quoted_quantity():
    resp, _ = run("Astra X9", qty=10)
    assert resp.status == "Partial" and resp.items[0].product_code == "PHN-X9"
    assert resp.quote.lines[0].quantity == 6 and resp.quote.lines[0].product_code == "PHN-X9"


# ---- helper edge cases ----
def test_similar_but_different_helper_boundaries():
    names = [p.name for p in CATALOGUE.values()]
    assert similar_but_different("Nimbus Tab 12", names)
    assert not similar_but_different("Nimbus Tab 8", names)        # right model number, name just shortened
    assert not similar_but_different("Nimbus Tab", names)          # no model given: ambiguity, not a mismatch
    assert not similar_but_different("black 128 GB 5G phone", names)
    assert not similar_but_different("wireless earbuds", names)