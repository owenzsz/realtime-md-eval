#!/usr/bin/env python3
"""rtmde.feed.ws — real-time Polymarket order-book feed over WebSocket.

Replaces 10 s REST polling with a live push feed: connect to the CLOB market
channel, subscribe to a set of token_ids, maintain an in-memory order book per
token, and fire ``on_update(token_id, book)`` on every change. Strategy-agnostic —
consumers just read ``feed.books[token].best_bid()/best_ask()/mid()``.

Protocol (verified against Polymarket docs, 2026-06):
  endpoint : wss://ws-subscriptions-clob.polymarket.com/ws/market   (public, no auth)
  subscribe: {"assets_ids": [<token_id>...], "type": "market"}      (sent on open)
  messages : event_type in {book, price_change, last_trade_price, tick_size_change}
             - book         : full snapshot {asset_id, bids[], asks[], timestamp, hash}
             - price_change : {price_changes:[{asset_id, price, size, side, ...}], ...}
                              side BUY->bid, SELL->ask; size "0" REMOVES the level.

There are no sequence numbers and the message hash is unreliable, so the book is
rebuilt on each ``book`` snapshot and the client reconnects after ``inactivity``
seconds of silence (mitigates the known "silent freeze" bug). Keepalive: send the
literal text "PING" every 10 s; the server replies "PONG".

Run a live demo:
    python -m rtmde.feed.ws [seconds] [num_markets]
"""
import asyncio
import json
import sys
import time

import websockets

from rtmde.feed.client import read_book, rewarded_candidates

WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class LiveBook:
    """In-memory order book for one token: ``price (float) -> size (float)``."""
    __slots__ = ("bids", "asks", "ts", "recv", "snaps", "updates")

    def __init__(self):
        self.bids = {}
        self.asks = {}
        self.ts = 0.0        # server timestamp of last message (ms)
        self.recv = 0.0      # local monotonic clock at last update
        self.snaps = 0
        self.updates = 0

    def snapshot(self, bids, asks, ts):
        """Rebuild both sides from a full ``book`` snapshot (drops size-0 levels)."""
        self.bids = {float(b["price"]): float(b["size"]) for b in bids if float(b["size"]) > 0}
        self.asks = {float(a["price"]): float(a["size"]) for a in asks if float(a["size"]) > 0}
        self.ts = float(ts or 0)
        self.recv = time.monotonic()
        self.snaps += 1

    def change(self, price, size, side, ts):
        """Apply one ``price_change`` level update. Size is SET-TO-ABSOLUTE; size 0 removes."""
        p = float(price)
        s = float(size)
        book = self.bids if side.upper() == "BUY" else self.asks
        if s <= 0:
            book.pop(p, None)
        else:
            book[p] = s
        self.ts = float(ts or self.ts)
        self.recv = time.monotonic()
        self.updates += 1

    def best_bid(self):
        return max(self.bids) if self.bids else None

    def best_ask(self):
        return min(self.asks) if self.asks else None

    def mid(self):
        bb, ba = self.best_bid(), self.best_ask()
        return (bb + ba) / 2 if bb is not None and ba is not None else None

    def spread(self):
        bb, ba = self.best_bid(), self.best_ask()
        return (ba - bb) if bb is not None and ba is not None else None


class PolyFeed:
    """Subscribes to the CLOB market channel and maintains a ``LiveBook`` per token,
    auto-reconnecting on error or inactivity. Pass ``on_update(token, book)``."""

    def __init__(self, token_ids, on_update=None, inactivity=120.0, endpoint=WSS):
        self.tokens = list(token_ids)
        self.books = {t: LiveBook() for t in self.tokens}
        self.on_update = on_update
        self.inactivity = inactivity
        self.endpoint = endpoint
        self.msgs = 0
        self.last_any = time.monotonic()
        self.connects = 0

    async def _ping(self, ws):
        try:
            while True:
                await asyncio.sleep(10)
                await ws.send("PING")            # docs: literal text "PING" every 10 s
        except Exception:
            pass

    async def _watchdog(self):
        while True:
            await asyncio.sleep(5)
            if time.monotonic() - self.last_any > self.inactivity:
                return                           # silence too long -> force reconnect

    def _handle(self, o):
        et = o.get("event_type")
        if et == "book":
            bk = self.books.get(o.get("asset_id"))
            if bk is None:
                return
            bk.snapshot(o.get("bids") or [], o.get("asks") or [], o.get("timestamp"))
            self._notify(o.get("asset_id"))
        elif et == "price_change":
            for ch in o.get("price_changes") or []:
                bk = self.books.get(ch.get("asset_id"))
                if bk is None:
                    continue
                bk.change(ch.get("price"), ch.get("size"), ch.get("side", ""), o.get("timestamp"))
                self._notify(ch.get("asset_id"))
        # last_trade_price / tick_size_change: not needed for book state (hook here if wanted)

    def _notify(self, token):
        self.last_any = time.monotonic()
        self.msgs += 1
        if self.on_update:
            try:
                self.on_update(token, self.books[token])
            except Exception as e:
                print("on_update error:", e, file=sys.stderr)

    async def _read(self, ws):
        async for raw in ws:
            if not raw or raw == "PONG":
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if isinstance(data, list):
                for o in data:
                    self._handle(o)
            elif isinstance(data, dict):
                self._handle(data)

    async def run(self):
        """Connect, subscribe, and pump updates forever, reconnecting with backoff."""
        backoff = 1
        while True:
            try:
                async with websockets.connect(self.endpoint, ping_interval=None,
                                               max_size=None, open_timeout=15) as ws:
                    self.connects += 1
                    await ws.send(json.dumps({"assets_ids": self.tokens, "type": "market"}))
                    self.last_any = time.monotonic()
                    backoff = 1
                    ping = asyncio.create_task(self._ping(ws))
                    wd = asyncio.create_task(self._watchdog())
                    rdr = asyncio.create_task(self._read(ws))
                    await asyncio.wait({wd, rdr}, return_when=asyncio.FIRST_COMPLETED)
                    for t in (ping, wd, rdr):
                        t.cancel()
            except Exception as e:
                print("ws error -> reconnect:", e, file=sys.stderr)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def demo(seconds, n):
    """Subscribe to a few live reward-bearing markets and print top-of-book moves."""
    toks, labels = [], {}
    for c in rewarded_candidates(""):
        tok = c[5][0]
        if read_book(tok):
            toks.append(tok)
            labels[tok] = f"{c[2][:5]}:{c[3][:24]}"
        if len(toks) >= n:
            break
    print(f"subscribing to {len(toks)} live markets on {WSS}\n")
    last = {}

    def on_update(token, bk):
        bb, ba = bk.best_bid(), bk.best_ask()
        key = (bb, ba)
        if last.get(token) == key:
            return                            # only print when top-of-book moves
        last[token] = key
        age = time.time() * 1000 - bk.ts if bk.ts else 0
        tag = "SNAP" if bk.updates == 0 else "TICK"
        mid = round(bk.mid(), 4) if bk.mid() else None
        print(f"{time.strftime('%H:%M:%S')} {tag} {labels.get(token, '')[:30]:30s} "
              f"bid={bb} ask={ba} mid={mid} (age {age:.0f}ms)")

    feed = PolyFeed(toks, on_update=on_update)
    try:
        await asyncio.wait_for(feed.run(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
    print(f"\n--- {seconds}s done | connects={feed.connects} | messages={feed.msgs} ---")
    for t in toks:
        bk = feed.books[t]
        sp = round(bk.spread(), 3) if bk.spread() else None
        print(f"  {labels[t][:34]:34s} snaps={bk.snaps} updates={bk.updates} "
              f"bid={bk.best_bid()} ask={bk.best_ask()} spread={sp}")


if __name__ == "__main__":
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    nmk = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    asyncio.run(demo(secs, nmk))
