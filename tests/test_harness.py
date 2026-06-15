from rtmde.eval.harness import _new_market_state, _prune_dead


def test_new_market_state_has_zeroed_accounting():
    mk = dict(question="Q", cat="sports", daily_reward=100.0, rmin=50, rmax=0.05,
              tick=0.01, yes="y", no="n", event="E")
    s = _new_market_state(mk)
    assert s["pos"] == 0.0 and s["cash"] == 0.0 and s["reward"] == 0.0
    assert s["prev_bid"] is None and s["prev_ask"] is None and s["last_mid"] is None
    assert s["samples"] == 0
    assert s["cat"] == "sports" and s["daily_reward"] == 100.0


def test_prune_dead_drops_tokens_without_books(capsys):
    state = {"a": {"question": "alive market"}, "b": {"question": "dead market"}}
    books = {"a": {"mid": 0.5}, "b": None}
    pruned = _prune_dead(state, books)
    assert "a" in pruned and "b" not in pruned
    assert "dropping" in capsys.readouterr().out
