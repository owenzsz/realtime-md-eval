from rtmde.notify.digest import format_digest, send


def _data():
    return {
        "samples": 100, "markets": 3, "span_h": 5.0, "fills": 12,
        "tot_reward": 4.0, "tot_inv": -1.5, "tot_net": 2.5,
        "cats": {
            "geopolitics": {"cat": "geopolitics", "mkts": 1, "reward": 3.0, "inv": 0.2,
                            "net": 3.2, "calm": 3.0, "vol": 0.2},
            "sports": {"cat": "sports", "mkts": 1, "reward": 1.0, "inv": -1.7,
                       "net": -0.7, "calm": 0.0, "vol": -1.7},
        },
        "cap": 300.0, "run_rate_day": 12.0, "pct_day": 4.0, "verdict_code": "positive",
    }


def test_format_digest_contains_verdict_and_categories():
    out = format_digest(_data())
    assert "geopolitics" in out and "sports" in out
    assert "Verdict" in out
    assert "POSITIVE" in out                 # text comes from report.VERDICTS["positive"]


def test_format_digest_handles_no_data():
    assert "No data" in format_digest(None)


def test_send_stdout_prints_and_returns_true(capsys):
    ok = send("hello digest", backend="stdout")
    assert ok is True
    assert "hello digest" in capsys.readouterr().out
