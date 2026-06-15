from rtmde.feed.ws import LiveBook


def test_snapshot_sets_levels_and_drops_zero_size():
    bk = LiveBook()
    bk.snapshot(
        bids=[{"price": "0.26", "size": "5"}, {"price": "0.25", "size": "0"}],
        asks=[{"price": "0.27", "size": "3"}],
        ts=1000,
    )
    assert bk.bids == {0.26: 5.0}            # the size-0 level is dropped
    assert bk.asks == {0.27: 3.0}
    assert bk.best_bid() == 0.26 and bk.best_ask() == 0.27
    assert bk.snaps == 1 and bk.updates == 0


def test_change_sets_absolute_size():
    bk = LiveBook()
    bk.snapshot([{"price": "0.26", "size": "5"}], [{"price": "0.27", "size": "3"}], 0)
    bk.change("0.26", "9", "BUY", 1)         # absolute level size, not a delta
    assert bk.bids[0.26] == 9.0
    assert bk.updates == 1


def test_change_zero_size_removes_level():
    bk = LiveBook()
    bk.snapshot([{"price": "0.26", "size": "5"}], [{"price": "0.27", "size": "3"}], 0)
    bk.change("0.27", "0", "SELL", 1)
    assert 0.27 not in bk.asks
    assert bk.best_ask() is None


def test_mid_and_spread_are_none_safe():
    bk = LiveBook()
    assert bk.best_bid() is None and bk.mid() is None and bk.spread() is None
    bk.snapshot([{"price": "0.20", "size": "1"}], [{"price": "0.30", "size": "1"}], 0)
    assert bk.mid() == 0.25
    assert round(bk.spread(), 10) == 0.10
