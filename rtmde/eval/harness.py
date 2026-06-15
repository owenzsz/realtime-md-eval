#!/usr/bin/env python3
"""rtmde.eval.harness — multi-day paper evaluation collector.

Before risking capital or building a fast executor, this harness PAPER-measures the
exact strategy in ``rtmde.eval.strategy`` across several markets over time and answers:
    1. Do projected liquidity REWARDS beat simulated INVENTORY losses (adverse selection)?
    2. Under WHICH conditions does it bleed? (bucketed by price volatility — see report)

State + history persist to disk (under the configured ``state_dir``), so it can run in
chunks over 3-5 days (systemd / cron / nohup) and accumulate. It reuses the strategy's
logic verbatim — single source of truth.

    python -m rtmde.eval.harness --loops 60 --interval 5 --markets 6   # collect a session
    python -m rtmde.eval.harness --report                              # aggregate -> verdict
    python -m rtmde.eval.harness --reset                               # wipe state + history

Live reward reconciliation (needs creds + funds; template — verify endpoints):
    python -m rtmde.eval.harness --probe --market hormuz              # post minimal orders
    python -m rtmde.eval.harness --reconcile                          # next day: actual vs paper

Files (under state_dir): state.json (continuity), samples.jsonl (history), probe.json.

NOTE: the network-bound collection paths (pick_markets / collect) are exercised by
``examples/run_eval.py``, not the unit tests; the unit tests cover the pure helpers.
"""
import json
import os
import sys
import time

from rtmde.feed.client import hget, read_book, read_books, rewarded_candidates
from rtmde.eval.strategy import Config, State, compute_quotes, reward_share, simulate_fills


def pick_markets(n, query=""):
    """Round-robin across CATEGORIES so the tracked set is diversified (geopolitics +
    sports + econ + ...), letting us compare which market types are MM-safe. Within a
    category, highest-reward live market first."""
    by_cat = []
    idx = {}
    for c in rewarded_candidates(query):           # c = (score, daily, cat, q, et, cl, rmin, rmax)
        cat = c[2]
        if cat not in idx:
            idx[cat] = len(by_cat)
            by_cat.append([])
        by_cat[idx[cat]].append(c)
    out = []
    seen = set()
    while len(out) < n and any(by_cat):
        advanced = False
        for lst in by_cat:                          # one market from each category per round
            if not lst:
                continue
            _, daily, cat, q, et, cl, rmin, rmax = lst.pop(0)
            advanced = True
            if cl[0] in seen:
                continue
            bk = read_book(cl[0])
            if not bk or bk["bid"] is None or bk["ask"] is None:
                continue
            seen.add(cl[0])
            out.append(dict(yes=cl[0], no=cl[1], question=q, event=et, cat=cat,
                            tick=bk["tick"], daily_reward=daily, rmin=rmin, rmax=rmax))
            if len(out) >= n:
                break
        if not advanced:
            break
    return out


def load_state(state_file):
    return json.load(open(state_file)) if os.path.exists(state_file) else {}


def save_state(state, state_file):
    json.dump(state, open(state_file, "w"), indent=1)


def _new_market_state(mk):
    return dict(pos=0.0, cash=0.0, reward=0.0, prev_bid=None, prev_ask=None, last_mid=None,
                question=mk["question"], cat=mk["cat"], daily_reward=mk["daily_reward"],
                rmin=mk["rmin"], rmax=mk["rmax"], tick=mk["tick"], samples=0)


def _prune_dead(state, books):
    """Drop markets whose book is gone (resolved/closed) so multi-day runs stay healthy."""
    alive = {}
    for tok, s in state.items():
        if books.get(tok):
            alive[tok] = s
        else:
            print(f"dropping resolved/dead market: {s['question'][:42]}")
    return alive


def collect(cfg, n_markets, state_file, samples_file):
    """Run one collection session: top up tracked markets, then sample each loop and
    append a row per market to the jsonl history."""
    state = load_state(state_file)
    if state:
        state = _prune_dead(state, read_books(list(state.keys())))
    # (re)fill up to n_markets with fresh live picks (skips ones already tracked)
    if len(state) < n_markets:
        have = set(state)
        for mk in pick_markets(n_markets * 3, cfg.market_query):
            if mk["yes"] in have:
                continue
            state[mk["yes"]] = _new_market_state(mk)
            if len(state) >= n_markets:
                break
    if not state:
        print("No live rewarded markets found.")
        return
    save_state(state, state_file)
    print(f"Tracking {len(state)} markets:")
    for tok, s in state.items():
        print(f"  ${s['daily_reward']:.0f}/day  {s['cat']:11s}  {s['question'][:48]}")
    print()

    logf = open(samples_file, "a")
    for i in range(cfg.loops):
        books = read_books(list(state.keys()))     # ONE batched POST for all tracked markets
        live = 0
        for tok, s in state.items():
            bk = books.get(tok)
            if not bk:
                continue
            live += 1
            mid = bk["mid"]
            o = State(pos=s["pos"], cash=s["cash"], reward=s["reward"])
            simulate_fills(o, s["prev_bid"], s["prev_ask"], cfg.quote_size, bk, cfg.max_inventory)
            jump = s["last_mid"] is not None and abs(mid - s["last_mid"]) > cfg.kill_move
            if jump:
                bid = ask = None
                bsz = asz = 0.0
                share = 0.0
            else:
                bid, ask, bsz, asz = compute_quotes(mid, bk["tick"], o.pos, cfg, s["rmax"])
                share = reward_share(mid, bid, ask, bsz, asz, s["rmax"], s["rmin"], bk)
                o.reward += s["daily_reward"] * share * (cfg.interval / 86400.0)
            dmid = 0.0 if s["last_mid"] is None else mid - s["last_mid"]
            logf.write(json.dumps(dict(
                ts=time.time(), tok=tok, q=s["question"][:42], cat=s["cat"],
                mid=round(mid, 4), dmid=round(dmid, 4), pos=round(o.pos, 1), cash=round(o.cash, 4),
                equity=round(o.cash + o.pos * mid, 4), reward=round(o.reward, 6), share=round(share, 6),
                runrate=round(s["daily_reward"] * share, 3), fills=o.fills, jump=bool(jump))) + "\n")
            s.update(pos=o.pos, cash=o.cash, reward=o.reward, last_mid=mid, samples=s["samples"] + 1,
                     prev_bid=(bid if bsz > 0 else None), prev_ask=(ask if asz > 0 else None))
        save_state(state, state_file)
        logf.flush()
        print(f"sample {i + 1}/{cfg.loops}  ({live} live markets)")
        if i < cfg.loops - 1:
            time.sleep(cfg.interval)
    logf.close()
    print("\nSession done. Run:  python -m rtmde.eval.report")


# ------------------------------------------- real reward reconciliation (OPTIONAL template)
def probe(query, probe_file):
    """Post the tightest in-zone, minimum-size maker orders so the next day's payout
    reveals your ACTUAL reward share. Needs creds + funds (uses the LiveBroker)."""
    print("LIVE PROBE — posts REAL minimal qualifying maker orders to measure ACTUAL reward.")
    if input("Type 'I UNDERSTAND' to place real orders: ").strip() != "I UNDERSTAND":
        print("Aborted.")
        return
    from rtmde.feed.client import pick_market
    from rtmde.eval.strategy import LiveBroker
    mk = pick_market(query)
    if not mk:
        print("no market")
        return
    bk = read_book(mk["yes"])
    mid = bk["mid"]
    tick = mk["tick"]
    bid = round(mid - tick, 2)
    ask = round(mid + tick, 2)
    size = max(mk["rmin"], 5)
    LiveBroker(mk["yes"]).reconcile(bid, ask, size, size)
    json.dump(dict(ts=time.time(), market=mk["question"], yes=mk["yes"], bid=bid, ask=ask, size=size,
                   daily_reward=mk["daily_reward"], funder=os.environ.get("POLY_FUNDER", "")),
              open(probe_file, "w"), indent=1)
    print(f"Placed {size:.0f} @ {bid}/{ask} on '{mk['question'][:40]}'. Leave resting.")
    print("Tomorrow after 00:00 UTC payout, run:  python -m rtmde.eval.harness --reconcile")


def reconcile(probe_file):
    """Compare the actual on-chain reward credited against the projected pool."""
    if not os.path.exists(probe_file):
        print("No probe on record. Run --probe first.")
        return
    p = json.load(open(probe_file))
    user = p.get("funder") or os.environ.get("POLY_FUNDER", "")
    if not user:
        print("Set POLY_FUNDER to your proxy address.")
        return
    hrs = (time.time() - p["ts"]) / 3600.0
    proj = p["daily_reward"]
    try:   # NOTE: verify this endpoint/field against the live data-api for your account
        acts = hget(f"https://data-api.polymarket.com/activity?user={user}&limit=500")
        rewards = [a for a in acts if "reward" in str(a.get("type", "")).lower()]
        actual = sum(float(a.get("usdcSize", 0) or a.get("size", 0) or 0) for a in rewards)
        print(f"probe age {hrs:.1f}h | market '{p['market'][:40]}' (pool ${proj:.0f}/day)")
        print(f"ACTUAL reward credited to {user[:10]}...: ${actual:.4f}  ({len(rewards)} reward events)")
        print(f"-> your realized share of the pool ~ {actual / max(proj, 1) * 100:.3f}% per day")
        print("Compare against the paper run-rate from the report. If actual << projected, the paper")
        print("competition proxy is too optimistic; re-tune size/tightness or pick a thinner-book market.")
    except Exception as e:
        print("reconcile fetch failed (verify the rewards endpoint for your account):", e)


def _main(argv):
    from rtmde.config import ensure_state_dir, load_config, state_paths
    a = argv

    def opt(flag, cast, d):
        return cast(a[a.index(flag) + 1]) if flag in a else d

    cfg_dict = load_config()
    ensure_state_dir(cfg_dict)
    state_file, samples_file, probe_file = state_paths(cfg_dict)

    if "--reset" in a:
        for f in (state_file, samples_file):
            if os.path.exists(f):
                os.remove(f)
        print("wiped state + history.")
        return
    if "--report" in a:
        from rtmde.eval.report import load_rows, render_report
        print(render_report(load_rows(samples_file)))
        return
    if "--reconcile" in a:
        reconcile(probe_file)
        return
    if "--probe" in a:
        probe(opt("--market", str, ""), probe_file)
        return

    cfg = Config.from_config(cfg_dict)
    cfg.market_query = opt("--market", str, cfg.market_query)
    cfg.loops = opt("--loops", int, 60)
    cfg.interval = opt("--interval", float, 5.0)
    cfg.quote_size = opt("--size", float, cfg.quote_size)
    cfg.max_inventory = opt("--maxinv", float, cfg.max_inventory)
    n_markets = opt("--markets", int, cfg_dict["eval"]["markets"])
    collect(cfg, n_markets, state_file, samples_file)


if __name__ == "__main__":
    _main(sys.argv[1:])
