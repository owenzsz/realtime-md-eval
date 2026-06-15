from rtmde.scanner.arb import best_ask, vwap_buy


def _book(asks):
    return {"asks": [{"price": str(p), "size": str(s)} for p, s in asks]}


def test_best_ask_picks_lowest_price():
    assert best_ask(_book([(0.30, 5), (0.27, 3), (0.40, 9)])) == (0.27, 3.0)


def test_best_ask_empty_book():
    assert best_ask({"asks": []}) == (None, 0.0)


def test_vwap_buy_walks_multiple_levels():
    cost, avg = vwap_buy(_book([(0.20, 10), (0.30, 10)]), 15)   # 10@0.20 + 5@0.30 = 3.5
    assert abs(cost - 3.5) < 1e-9
    assert abs(avg - 3.5 / 15) < 1e-9


def test_vwap_buy_insufficient_depth_returns_none():
    assert vwap_buy(_book([(0.20, 5)]), 100) is None
