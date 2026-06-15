#!/usr/bin/env python3
"""Run a short paper-evaluation session, then print the go/no-go verdict report.

    python examples/run_eval.py --loops 30 --interval 5 --markets 6
"""
import argparse

from rtmde.config import ensure_state_dir, load_config, state_paths
from rtmde.eval.harness import collect
from rtmde.eval.report import load_rows, render_report
from rtmde.eval.strategy import Config


def main():
    ap = argparse.ArgumentParser(description="Short paper-eval session, then report.")
    ap.add_argument("--loops", type=int, default=30)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--markets", type=int, default=None, help="default: config eval.markets")
    ap.add_argument("--market", type=str, default="", help="substring filter for market pick")
    args = ap.parse_args()

    cfg_dict = load_config()
    ensure_state_dir(cfg_dict)
    state_file, samples_file, _ = state_paths(cfg_dict)

    cfg = Config.from_config(cfg_dict)
    cfg.loops = args.loops
    cfg.interval = args.interval
    cfg.market_query = args.market
    n_markets = args.markets if args.markets is not None else cfg_dict["eval"]["markets"]

    collect(cfg, n_markets, state_file, samples_file)
    print("\n" + render_report(load_rows(samples_file)))


if __name__ == "__main__":
    main()
