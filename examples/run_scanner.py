#!/usr/bin/env python3
"""Run one structural-arbitrage scan over the top-volume markets (read-only, keyless).

    python examples/run_scanner.py --pages 3 --min-net 0.3 --maxN 16
"""
import argparse

from rtmde.scanner.arb import scan


def main():
    ap = argparse.ArgumentParser(description="One depth + fee-aware arbitrage scan.")
    ap.add_argument("--pages", type=int, default=3, help="100-event pages to scan")
    ap.add_argument("--min-net", type=float, default=0.3, help="min net edge (cents/set) to report")
    ap.add_argument("--maxN", type=int, default=16, help="max basket size treated as executable")
    args = ap.parse_args()
    scan(args.pages, args.min_net, args.maxN)


if __name__ == "__main__":
    main()
