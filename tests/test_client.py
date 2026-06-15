from rtmde.feed.client import _normalize_book, categorize, fee_share


def test_normalize_book_picks_best_levels():
    raw = {
        "bids": [{"price": "0.24", "size": "10"}, {"price": "0.26", "size": "5"}],
        "asks": [{"price": "0.30", "size": "7"}, {"price": "0.27", "size": "3"}],
        "tick_size": "0.01",
    }
    bk = _normalize_book(raw)
    assert bk["bid"] == 0.26 and bk["ask"] == 0.27        # best (highest bid, lowest ask)
    assert bk["bid_sz"] == 5.0 and bk["ask_sz"] == 3.0
    assert bk["mid"] == (0.26 + 0.27) / 2
    assert bk["tick"] == 0.01
    assert bk["bids"][0] == (0.26, 5.0) and bk["asks"][0] == (0.27, 3.0)


def test_normalize_book_none_when_a_side_is_empty():
    assert _normalize_book({"bids": [], "asks": [{"price": "0.3", "size": "1"}]}) is None
    assert _normalize_book(None) is None


def test_normalize_book_defaults_tick_to_one_cent():
    raw = {"bids": [{"price": "0.5", "size": "1"}], "asks": [{"price": "0.6", "size": "1"}]}
    assert _normalize_book(raw)["tick"] == 0.01


def test_categorize_tag_match_and_keyword_fallback():
    assert categorize({"tags": [{"slug": "crypto"}], "title": "BTC?"}) == "crypto"
    assert categorize({"tags": [], "title": "Will Iran close the Strait of Hormuz?"}) == "geopolitics"
    assert categorize({"tags": [], "title": "Will the Fed cut the interest rate?"}) == "economics"
    assert categorize({"tags": [], "title": "A totally generic question"}) == "default"


def test_fee_share_geopolitics_is_zero():
    assert fee_share(0.5, "geopolitics") == 0.0


def test_fee_share_peaks_near_half_and_clamps_extremes():
    peak = fee_share(0.5, "crypto")
    assert peak > fee_share(0.1, "crypto") > 0
    assert peak > fee_share(0.9, "crypto") > 0
    assert fee_share(0.0, "crypto") > 0      # clamped, never zero/negative
    assert fee_share(1.0, "crypto") > 0
