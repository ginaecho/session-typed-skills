"""test_exemplars.py — the stdlib-only BM25 exemplar retriever. Fully
offline (no I/O)."""
from experiments.seam_bench.t0.exemplars import ExemplarCandidate, ExemplarIndex, tokenize


CANDIDATES = [
    ExemplarCandidate(item_id="auction", intent="Run a sealed-bid auction "
                       "with three bidders who submit numeric bids",
                       protocol="protocol Auction ..."),
    ExemplarCandidate(item_id="banking", intent="Process a bank wire "
                       "transfer between two accounts with fraud review",
                       protocol="protocol Banking ..."),
    ExemplarCandidate(item_id="travel", intent="Book a flight and hotel "
                       "package for a two-city trip",
                       protocol="protocol Travel ..."),
]


def test_tokenize_lowercases_and_strips_punctuation():
    assert tokenize("Run a Sealed-Bid Auction!") == \
        ["run", "a", "sealed", "bid", "auction"]


def test_top_k_ranks_the_lexically_closest_candidate_first():
    index = ExemplarIndex(CANDIDATES)
    top = index.top_k("Auction bidders submit sealed bids", 1)
    assert len(top) == 1
    assert top[0].item_id == "auction"


def test_top_k_excludes_named_item_ids():
    index = ExemplarIndex(CANDIDATES)
    top = index.top_k("Auction bidders submit sealed bids", 3,
                       exclude_item_ids=["auction"])
    assert "auction" not in {c.item_id for c in top}
    assert len(top) == 2


def test_top_k_respects_k():
    index = ExemplarIndex(CANDIDATES)
    assert len(index.top_k("anything at all", 2)) == 2
    assert len(index.top_k("anything at all", 0)) == 0
    assert len(index.top_k("anything at all", 100)) == len(CANDIDATES)


def test_top_k_pairs_returns_intent_protocol_tuples():
    index = ExemplarIndex(CANDIDATES)
    pairs = index.top_k_pairs("Book a flight package", 1)
    assert pairs == [("Book a flight and hotel package for a two-city trip",
                       "protocol Travel ...")]


def test_empty_index_returns_nothing():
    index = ExemplarIndex([])
    assert index.top_k("anything", 3) == []
