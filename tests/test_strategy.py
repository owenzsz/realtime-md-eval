from rtmde.eval.strategy import (Config, State, compute_quotes, reward_share,
                                 simulate_fills, snap)


def _cfg():
    return Config(quote_size=200, half_spread_ticks=2, max_inventory=800, skew_strength=1.0)


def test_snap_rounds_to_tick_grid():
    assert snap(0.2649, 0.01, "down") == 0.26
    assert snap(0.2651, 0.01, "up") == 0.27


def test_compute_quotes_symmetric_at_zero_inventory():
    bid, ask, bsz, asz = compute_quotes(0.265, 0.01, 0, _cfg(), 0.035)
    assert bid < 0.265 < ask
    assert bsz == 200 and asz == 200


def test_long_inventory_skews_center_down():
    cfg = _cfg()
    bid, ask, _, _ = compute_quotes(0.265, 0.01, 0, cfg, 0.035)
    b2, a2, _, _ = compute_quotes(0.265, 0.01, 400, cfg, 0.035)
    assert b2 <= bid and a2 <= ask          # both quotes shift down when long


def test_inventory_cap_pulls_buy_side():
    _, _, bsz, _ = compute_quotes(0.265, 0.01, 800, _cfg(), 0.035)
    assert bsz == 0


def test_simulate_fills_models_adverse_selection_and_pnl():
    st = State()
    # price fell to/through our resting bid 0.26 -> we buy 200 @ 0.26
    simulate_fills(st, 0.26, 0.28, 200, dict(ask=0.25, bid=0.24, ask_sz=999, bid_sz=999), 800)
    assert st.pos == 200
    assert abs(st.cash - (-52.0)) < 1e-9                       # 200 * 0.26 = 52 spent
    assert abs(st.equity(0.30) - (200 * 0.30 - 52)) < 1e-9     # mark up to 0.30 -> +8


def test_reward_share_requires_two_sided_quotes():
    bk = dict(bids=[(0.26, 500)], asks=[(0.27, 500)])
    assert reward_share(0.265, 0.26, 0.27, 200, 0, 0.035, 50, bk) == 0.0      # one-sided -> 0
    assert reward_share(0.265, 0.26, 0.27, 200, 200, 0.035, 50, bk) > 0.0     # two-sided -> >0


def test_state_pnl_includes_reward():
    st = State(pos=100, cash=-20.0, reward=1.5)
    assert st.equity(0.30) == -20.0 + 100 * 0.30
    assert st.pnl(0.30) == st.equity(0.30) + 1.5


def test_config_from_yaml_dict_maps_interval_s():
    cfg = Config.from_config({"strategy": {"quote_size": 50, "interval_s": 9}, "live": {"enabled": True}})
    assert cfg.quote_size == 50.0
    assert cfg.interval == 9.0
    assert cfg.live is True
