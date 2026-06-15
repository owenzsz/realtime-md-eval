"""rtmde.config — layered configuration loading.

Resolution order: built-in ``DEFAULTS`` <- optional YAML file (``config.yaml`` by
default, which is gitignored). Secrets are NEVER stored in config. Wallet and
Telegram credentials are read from environment variables (or a chmod-600 env
file) and are referenced here by *name* only — never by value.
"""
from __future__ import annotations

import os

DEFAULTS = {
    "feed": {
        "ws_endpoint": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        "rest_clob": "https://clob.polymarket.com",
        "rest_gamma": "https://gamma-api.polymarket.com",
        "inactivity_timeout_s": 120,
    },
    "strategy": {
        "quote_size": 200.0,
        "half_spread_ticks": 2,
        "max_inventory": 800.0,
        "skew_strength": 1.0,
        "kill_move": 0.04,
        "interval_s": 5.0,
        "loops": 40,
    },
    "eval": {
        "markets": 6,
        "state_dir": "./state",   # gitignored; holds state.json / samples.jsonl / probe.json
    },
    "notify": {
        "backend": "stdout",      # "stdout" | "telegram"
        "telegram_env_file": "~/.rtmde_tg.env",
    },
    "live": {"enabled": False},   # live order placement is opt-in; reads POLY_PK/POLY_FUNDER from env
    "deploy": {
        "gcp_project": "<GCP_PROJECT>",
        "gcp_zone": "<GCP_ZONE>",
        "vm_name": "<VM_NAME>",
        "vm_user": "<VM_USER>",
    },
}


def _deep_merge(base, override):
    """Recursively merge *override* onto a copy of *base* (override wins on leaves)."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path="config.yaml"):
    """Return the merged config dict.

    If *path* exists, deep-merge it over ``DEFAULTS`` (requires PyYAML, imported
    lazily so the core feed has no hard YAML dependency). Otherwise return a fresh
    copy of ``DEFAULTS``. Never mutates the module-level ``DEFAULTS``.
    """
    if path and os.path.exists(path):
        import yaml  # lazy: only needed when a real config file is present
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        return _deep_merge(DEFAULTS, user)
    return _deep_merge(DEFAULTS, {})


def state_paths(cfg):
    """Resolve ``(state_file, samples_file, probe_file)`` under ``eval.state_dir``."""
    d = os.path.expanduser(cfg["eval"]["state_dir"])
    return (
        os.path.join(d, "state.json"),
        os.path.join(d, "samples.jsonl"),
        os.path.join(d, "probe.json"),
    )


def ensure_state_dir(cfg):
    """Create the state directory if missing; return its (expanded) path."""
    d = os.path.expanduser(cfg["eval"]["state_dir"])
    os.makedirs(d, exist_ok=True)
    return d
