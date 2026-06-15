#!/usr/bin/env python3
"""Live demo: subscribe to a few reward-bearing markets over WebSocket and print
top-of-book moves with their age in milliseconds (the ~90 ms latency you can see).

    python examples/run_feed.py --seconds 25 --markets 6
"""
import argparse
import asyncio

from rtmde.feed.ws import demo


def main():
    ap = argparse.ArgumentParser(description="Live WebSocket order-book demo.")
    ap.add_argument("--seconds", type=int, default=25, help="how long to run")
    ap.add_argument("--markets", type=int, default=6, help="how many markets to subscribe to")
    args = ap.parse_args()
    asyncio.run(demo(args.seconds, args.markets))


if __name__ == "__main__":
    main()
