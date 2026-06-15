from rtmde.eval.report import aggregate, render_report


def _row(tok, ts, cat, mid, dmid, reward, equity, fills=0, runrate=0.0):
    return dict(tok=tok, ts=ts, q=f"Q-{tok}", cat=cat, mid=mid, dmid=dmid,
                reward=reward, equity=equity, fills=fills, runrate=runrate)


def test_aggregate_empty_returns_none():
    assert aggregate([]) is None


def test_aggregate_negative_verdict_when_inventory_swamps_reward():
    rows = [
        _row("t1", 0.0,       "sports", 0.50, 0.00,  0.0, 0.0),
        _row("t1", 3 * 3600,  "sports", 0.40, -0.10, 0.5, -5.0),
    ]
    d = aggregate(rows)
    assert d["tot_net"] < 0
    assert d["verdict_code"] == "negative"
    assert d["cats"]["sports"]["vol"] < 0          # loss attributed to the volatile bucket


def test_aggregate_positive_verdict_when_reward_dominates():
    rows = [
        _row("t1", 0.0,       "geopolitics", 0.50, 0.000, 0.0, 0.0),
        _row("t1", 3 * 3600,  "geopolitics", 0.50, 0.001, 5.0, -0.5),
    ]
    d = aggregate(rows)
    assert d["tot_net"] > 0
    assert d["verdict_code"] == "positive"
    assert d["cats"]["geopolitics"]["calm"] != 0   # quiet samples attributed to calm


def test_aggregate_insufficient_when_span_under_two_hours():
    rows = [
        _row("t1", 0.0,    "sports", 0.5, 0.0, 1.0, 0.0),
        _row("t1", 1800.0, "sports", 0.5, 0.0, 2.0, 0.0),
    ]
    assert aggregate(rows)["verdict_code"] == "insufficient"


def test_render_report_returns_string_with_verdict_and_sections():
    rows = [
        _row("t1", 0.0,      "geopolitics", 0.5, 0.000, 0.0, 0.0, fills=0, runrate=1.0),
        _row("t1", 3 * 3600, "geopolitics", 0.5, 0.001, 5.0, -0.5, fills=2, runrate=1.0),
    ]
    out = render_report(rows)
    assert isinstance(out, str)
    assert "VERDICT" in out and "PER-CATEGORY" in out


def test_render_report_empty_is_safe():
    assert "No history" in render_report([])
