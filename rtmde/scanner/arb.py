#!/usr/bin/env python3
"""rtmde.scanner.arb — depth-walked, fee-aware structural-arbitrage scanner.

Read-only and keyless. Scans LIVE order books and reports only ACTIONABLE,
depth-verified edges: it walks each book (VWAP), applies the CLOB-v2 per-category
taker-fee curve (shared with the strategy via ``rtmde.feed.client``), and reports the
largest size that still clears a net profit.

Three structural edges (riskless if filled atomically):
  1. NEG-RISK YES BASKET  -- N mutually-exclusive outcomes; buy 1 YES of each for < $1
                             -> lock $1 - cost per set.
  2. NEG-RISK NO BASKET   -- buy 1 NO of each outcome for < (N-1); convert -> (N-1).
  3. BINARY MERGE ARB     -- within one market, best YES ask + best NO ask < $1.

Reality check: clean liquid arbs last seconds (bots eat them). Use --watch to watch
them decay. Empirically (see docs/research), depth + fees leave ~0 actionable arbs for
a slow individual — which is exactly why the project pivoted to evaluating a market-
making strategy instead.

    python -m rtmde.scanner.arb [pages] [min_net_cents] [--watch SECONDS] [--maxN N]
"""
import json
import sys
import time

from rtmde.feed.client import CLOB, GAMMA, categorize, fee_share, hget, hpost


def fetch_events(pages):
    out = []
    for off in range(0, pages * 100, 100):
        try:
            page = hget(f"{GAMMA}/events?closed=false&active=true&limit=100&offset={off}&order=volume24hr&ascending=false")
        except Exception as e:
            print(f"  events off={off} fail: {e}", file=sys.stderr)
            break
        if not page:
            break
        out += page
        time.sleep(0.12)
    return out


def fetch_books(tokens):
    """Batch-fetch raw books (full ladders) via POST /books — needed for depth walking."""
    books = {}
    chunk_size = 50
    for i in range(0, len(tokens), chunk_size):
        try:
            res = hpost(f"{CLOB}/books", [{"token_id": t} for t in tokens[i:i + chunk_size]])
        except Exception as e:
            print(f"  books {i} fail: {e}", file=sys.stderr)
            res = []
        for e in res or []:
            books[str(e.get("asset_id"))] = e
        time.sleep(0.08)
    return books


def asks_sorted(book):
    return sorted((book.get("asks") or []), key=lambda x: float(x["price"]))


def best_ask(book):
    a = asks_sorted(book)
    return (float(a[0]["price"]), float(a[0]["size"])) if a else (None, 0.0)


def vwap_buy(book, qty):
    """Cost to buy *qty* shares walking the asks. Returns ``(cost, avg_price)`` or
    None if there is not enough depth to fill *qty*."""
    need = qty
    cost = 0.0
    for lvl in asks_sorted(book):
        p = float(lvl["price"])
        s = float(lvl["size"])
        take = min(need, s)
        cost += take * p
        need -= take
        if need <= 1e-9:
            break
    if need > 1e-9:
        return None
    return cost, cost / qty


def scan(pages, min_net, maxN):
    events = fetch_events(pages)
    markets = []
    tokens = set()
    for ev in events:
        cat = categorize(ev)
        nr = bool(ev.get("negRisk"))
        for m in ev.get("markets") or []:
            if not m.get("enableOrderBook") or m.get("closed") or m.get("acceptingOrders") is False:
                continue
            try:
                cl = json.loads(m["clobTokenIds"])
            except Exception:
                continue
            if len(cl) != 2:
                continue
            markets.append(dict(event=ev.get("title"), cat=cat, nr=nr, q=m.get("question", ""), yes=cl[0], no=cl[1]))
            tokens.add(cl[0])
            tokens.add(cl[1])
    books = fetch_books(list(tokens))
    print(f"events={len(events)}  live_markets={len(markets)}  books={len(books)}")

    def bk(t):
        return books.get(str(t))

    # group neg-risk markets by (event, category)
    grp = {}
    for m in markets:
        if m["nr"]:
            grp.setdefault((m["event"], m["cat"]), []).append(m)

    yes_arbs = []
    no_arbs = []
    for (event, cat), ms in grp.items():
        N = len(ms)
        if N < 2 or N > maxN:
            continue
        ybooks = [bk(m["yes"]) for m in ms]
        nbooks = [bk(m["no"]) for m in ms]
        if any(b is None for b in ybooks):
            continue
        if any(best_ask(b)[0] is None for b in ybooks):    # require a complete, executable basket
            continue
        best = None
        for Q in [10, 25, 50, 100, 200, 400, 800, 1600, 3200]:
            legs = [vwap_buy(b, Q) for b in ybooks]
            if any(l is None for l in legs):
                break
            cost = sum(c for c, _ in legs)
            fee = sum(fee_share(ap, cat) * Q for _, ap in legs)
            net = Q * 1.0 - cost - fee
            if net > 0:
                best = (Q, cost, fee, net, net / Q)
        if best:
            Q, cost, fee, net, edge = best
            yes_arbs.append(dict(event=event, cat=cat, N=N, Q=Q, cost=cost, net=net, edge=edge))
        if all(best_ask(b)[0] is not None for b in nbooks):
            best = None
            for Q in [10, 25, 50, 100, 200, 400]:
                legs = [vwap_buy(b, Q) for b in nbooks]
                if any(l is None for l in legs):
                    break
                cost = sum(c for c, _ in legs)
                fee = sum(fee_share(ap, cat) * Q for _, ap in legs)
                net = Q * (N - 1) - cost - fee
                if net > 0:
                    best = (Q, cost, net, net / Q)
            if best:
                Q, cost, net, edge = best
                no_arbs.append(dict(event=event, cat=cat, N=N, Q=Q, net=net, edge=edge))

    # binary merge arb (within one market)
    bin_arbs = []
    for m in markets:
        by, bn = bk(m["yes"]), bk(m["no"])
        if by is None or bn is None:
            continue
        if best_ask(by)[0] is None or best_ask(bn)[0] is None:
            continue
        best = None
        for Q in [10, 25, 50, 100, 200, 400, 800]:
            cy = vwap_buy(by, Q)
            cn = vwap_buy(bn, Q)
            if cy is None or cn is None:
                break
            cost = cy[0] + cn[0]
            fee = (fee_share(cy[1], m["cat"]) + fee_share(cn[1], m["cat"])) * Q
            net = Q * 1.0 - cost - fee
            if net > 0:
                best = (Q, net, net / Q)
        if best:
            Q, net, edge = best
            bin_arbs.append(dict(q=m["q"], event=m["event"], cat=m["cat"], Q=Q, net=net, edge=edge))

    def show(title, rows):
        print("\n" + "=" * 92 + f"\n{title}\n" + "=" * 92)
        rows = [r for r in rows if r["edge"] * 100 >= min_net]
        for r in sorted(rows, key=lambda x: -x["net"])[:12]:
            tag = f"N={r['N']:2d} " if "N" in r else ""
            print(f"  net ${r['net']:7.2f} @ Q={r['Q']:4d} | edge {r['edge'] * 100:+5.2f}c/set | "
                  f"{tag}{r['cat'][:6]:6s} | {(r.get('q') or r['event'])[:46]}")
        if not rows:
            print("  (none above threshold)")
        return rows

    a = show("NEG-RISK YES BASKET  (buy all YES < $1; depth-verified net after fees)", yes_arbs)
    b = show("NEG-RISK NO BASKET   (buy all NO < N-1; complete legs only)", no_arbs)
    c = show("BINARY MERGE ARB     (YES ask + NO ask < $1; buy both, merge -> $1)", bin_arbs)
    print("\n" + "-" * 92)
    print(f"ACTIONABLE now: {len(a)} YES-basket, {len(b)} NO-basket, {len(c)} merge  "
          f"(threshold {min_net}c/set, executable size walked through the book)")
    return a, b, c


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    pages = int(args[0]) if len(args) > 0 else 3
    min_net = float(args[1]) if len(args) > 1 else 0.3
    watch = None
    maxN = 16
    if "--watch" in sys.argv:
        watch = int(sys.argv[sys.argv.index("--watch") + 1])
    if "--maxN" in sys.argv:
        maxN = int(sys.argv[sys.argv.index("--maxN") + 1])
    while True:
        t = time.strftime("%H:%M:%S")
        print("\n" + "#" * 92 + f"\n# scan @ {t}\n" + "#" * 92)
        try:
            scan(pages, min_net, maxN)
        except Exception as e:
            print("scan error:", e, file=sys.stderr)
        if not watch:
            break
        time.sleep(watch)
