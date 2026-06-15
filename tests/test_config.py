from rtmde.config import DEFAULTS, load_config, state_paths


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(str(tmp_path / "missing.yaml"))
    assert cfg["strategy"]["quote_size"] == 200.0
    assert cfg["notify"]["backend"] == "stdout"
    assert cfg["feed"]["ws_endpoint"].startswith("wss://")


def test_override_merges_over_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("strategy:\n  quote_size: 50\nnotify:\n  backend: telegram\n")
    cfg = load_config(str(p))
    assert cfg["strategy"]["quote_size"] == 50          # overridden leaf
    assert cfg["strategy"]["max_inventory"] == 800.0    # sibling default preserved
    assert cfg["notify"]["backend"] == "telegram"


def test_load_config_does_not_mutate_module_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("eval:\n  markets: 99\n")
    load_config(str(p))
    assert DEFAULTS["eval"]["markets"] == 6


def test_state_paths_join_under_state_dir(tmp_path):
    cfg = load_config(str(tmp_path / "missing.yaml"))
    cfg["eval"]["state_dir"] = "/tmp/rtmde_state"
    sf, lf, pf = state_paths(cfg)
    assert sf == "/tmp/rtmde_state/state.json"
    assert lf == "/tmp/rtmde_state/samples.jsonl"
    assert pf == "/tmp/rtmde_state/probe.json"
