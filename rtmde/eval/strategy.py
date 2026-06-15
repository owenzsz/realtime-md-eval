#!/usr/bin/env python3
"""rtmde.eval.strategy — the market-making strategy under evaluation.

Pure, dependency-light quoting + inventory logic, plus a paper/live run loop. The
evaluation harness (``rtmde.eval.harness``) drives this same logic across many
markets and many days; nothing here assumes a profit — it is the thing being
measured.

Pieces:
  Config / State          parameters + signed inventory / cash / reward accounting
  snap / compute_quotes   tick-snapped two-sided quotes, skewed against inventory,
                          kept inside the reward zone, pulled at the inventory cap
  reward_share            rough per-snapshot liquidity-reward share (competition proxy)
  simulate_fills          paper fills that DELIBERATELY model adverse selection
  PaperBroker/LiveBroker  paper no-op vs real GTC maker orders (py-clob-client, lazy)
  run                     a single-market quoting loop (paper by default, --live opt-in)

Paper PnL is directional intuition, NOT a backtest (see RISK NOTES at the bottom).

    python -m rtmde.eval.strategy --market hormuz       # paper, auto-pick a market
"""
import math
import os
import sys
import time
from dataclasses import dataclass

from rtmde.feed.client import FEE, pick_market, read_book


@dataclass
class Config:
    market_query: str = ""        # substring to pick the market ("" = auto best)
    quote_size: float = 200.0     # shares quoted per side (keep >= reward min size)
    half_spread_ticks: int = 2    # quote this many ticks from mid on each side
    max_inventory: float = 800.0  # hard cap on |net YES position| (shares)
    skew_strength: float = 1.0    # how hard to lean quotes against inventory (0 = off)
    kill_move: float = 0.04       # mid jump between loops that pulls quotes
    interval: float = 5.0         # seconds between loops
    loops: int = 40               # number of loops then stop
    live: bool = False

    @classmethod
    def from_config(cls, cfg):
        """Build from a loaded config dict (its ``strategy`` + ``live`` sections)."""
        s = cfg.get("strategy", {})
        return cls(
            quote_size=float(s.get("quote_size", 200.0)),
            half_spread_ticks=int(s.get("half_spread_ticks", 2)),
            max_inventory=float(s.get("max_inventory", 800.0)),
            skew_strength=float(s.get("skew_strength", 1.0)),
            kill_move=float(s.get("kill_move", 0.04)),
            interval=float(s.get("interval_s", 5.0)),
            loops=int(s.get("loops", 40)),
            live=bool(cfg.get("live", {}).get("enabled", False)),
        )


def snap(price, tick, direction):
    """Snap *price* to the tick grid (round down/up per *direction*), clamped to
    ``[tick, 1-tick]``."""
    n = price / tick
    n = math.floor(n + 1e-9) if direction == "down" else math.ceil(n - 1e-9)
    return min(max(round(n * tick, 6), tick), round(1 - tick, 6))


def compute_quotes(mid, tick, pos, cfg, vmax):
    """Return ``(bid_px, ask_px, bid_sz, ask_sz)``. Quotes stay within +/- *vmax* of
    *mid* (the reward zone), are skewed against inventory, and the inventory-increasing
    side is pulled to size 0 when the cap is hit."""
    half = min(cfg.half_spread_ticks * tick, vmax)
    if half < tick:
        half = tick
    skew = cfg.skew_strength * (pos / cfg.max_inventory) * half if cfg.max_inventory > 0 else 0.0
    center = mid - skew                              # long -> shift down (sell faster)
    bid = snap(center - half, tick, "down")
    ask = snap(center + half, tick, "up")
    if ask <= bid:
        ask = snap(bid + tick, tick, "up")
    bid_sz = 0.0 if pos >= cfg.max_inventory else cfg.quote_size
    ask_sz = 0.0 if pos <= -cfg.max_inventory else cfg.quote_size
    return bid, ask, bid_sz, ask_sz


def reward_share(mid, bid, ask, bid_sz, ask_sz, vmax, rmin, book):
    """Rough per-snapshot liquidity-reward share. Two-sided only; quadratic in
    closeness to mid; competition proxied by visible resting size within the zone."""
    if bid_sz < max(rmin, 1) or ask_sz < max(rmin, 1):
        return 0.0
    s_bid = mid - bid
    s_ask = ask - mid
    if not (0 <= s_bid <= vmax and 0 <= s_ask <= vmax):
        return 0.0
    score = ((vmax - s_bid) / vmax) ** 2 * bid_sz + ((vmax - s_ask) / vmax) ** 2 * ask_sz
    comp = sum(sz for px, sz in (book["bids"] + book["asks"]) if abs(px - mid) <= vmax)
    return score / (score + comp) if score + comp > 0 else 0.0


@dataclass
class State:
    """Signed inventory + cash + estimated reward, with mark-to-market helpers."""
    pos: float = 0.0       # signed net YES shares (negative = short YES ~ long NO)
    cash: float = 0.0      # cumulative cash flow (buys negative)
    reward: float = 0.0    # estimated rewards accrued (USDC)
    fills: int = 0

    def buy(self, qty, px):
        self.pos += qty
        self.cash -= qty * px
        self.fills += 1

    def sell(self, qty, px):
        self.pos -= qty
        self.cash += qty * px
        self.fills += 1

    def equity(self, mid):
        return self.cash + self.pos * mid        # mark-to-market

    def pnl(self, mid):
        return self.equity(mid) + self.reward


def simulate_fills(st, prev_bid, prev_ask, qsize, book, max_inv):
    """Paper fills: a resting bid fills when price falls to/through it; a resting ask
    fills when price rises to/through it. This INTENTIONALLY models adverse selection
    (you are filled exactly when the market moves against you)."""
    if prev_bid is not None and book["ask"] <= prev_bid and st.pos < max_inv:
        q = min(qsize, max_inv - st.pos, book["ask_sz"])
        if q > 0:
            st.buy(q, prev_bid)
    if prev_ask is not None and book["bid"] >= prev_ask and st.pos > -max_inv:
        q = min(qsize, st.pos + max_inv, book["bid_sz"])
        if q > 0:
            st.sell(q, prev_ask)


class PaperBroker:
    """No-op broker; fills are modeled by ``simulate_fills``."""
    name = "PAPER"

    def reconcile(self, bid, ask, bsz, asz):
        pass

    def cancel_all(self):
        pass


class LiveBroker:
    """Real GTC maker orders via py-clob-client. OPTIONAL + UNTESTED TEMPLATE — verify
    against the current py-clob-client API before trusting it. py-clob-client is
    imported lazily so the rest of the package stays keyless."""
    name = "LIVE"

    def __init__(self, token):
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL
        from rtmde.feed.client import CLOB
        self.OrderArgs, self.OrderType, self.BUY, self.SELL = OrderArgs, OrderType, BUY, SELL
        self.token = token
        self.c = ClobClient(CLOB, key=os.environ["POLY_PK"], chain_id=137,
                            funder=os.environ["POLY_FUNDER"], signature_type=2)
        self.c.set_api_creds(self.c.create_or_derive_api_creds())

    def reconcile(self, bid, ask, bsz, asz):
        self.c.cancel_all()                          # simplest: cancel + repost each loop
        if bsz > 0:
            self.c.post_order(self.c.create_order(
                self.OrderArgs(price=bid, size=bsz, side=self.BUY, token_id=self.token)), self.OrderType.GTC)
        if asz > 0:
            self.c.post_order(self.c.create_order(
                self.OrderArgs(price=ask, size=asz, side=self.SELL, token_id=self.token)), self.OrderType.GTC)

    def cancel_all(self):
        try:
            self.c.cancel_all()
        except Exception as e:
            print("cancel_all error:", e)


def run(cfg):
    """Single-market quoting loop. Paper by default; ``cfg.live`` posts real orders
    after explicit confirmation."""
    mk = pick_market(cfg.market_query)
    if not mk:
        print("No live rewarded market matched. Try a different --market.")
        return
    rate, exp = FEE.get(mk["cat"], FEE["default"])
    print(f"MARKET : {mk['question']}")
    print(f"event  : {mk['event']}  | cat={mk['cat']} (taker fee peak {rate * 0.25 * 100:.2f}% , maker 0%)")
    print(f"reward : ${mk['daily_reward']:.0f}/day | min size {mk['rmin']:.0f} | "
          f"max spread {mk['rmax'] * 100:.1f}c | tick {mk['tick']}")
    print(f"mode   : {'LIVE (real orders!)' if cfg.live else 'PAPER (simulated)'}  | "
          f"size/side {cfg.quote_size:.0f} | maxInv {cfg.max_inventory:.0f}\n")

    if cfg.live:
        ans = input("LIVE mode will place REAL maker orders with your funds. Type 'I UNDERSTAND' to proceed: ")
        if ans.strip() != "I UNDERSTAND":
            print("Aborted.")
            return
        broker = LiveBroker(mk["yes"])
    else:
        broker = PaperBroker()

    st = State()
    prev_bid = prev_ask = None
    last_mid = None
    hdr = f"{'loop':>4} {'mid':>6} {'bid':>6} {'ask':>6} {'pos':>6} {'cash':>9} {'reward':>7} {'PnL':>8}  note"
    print(hdr)
    print("-" * len(hdr))
    try:
        for i in range(cfg.loops):
            book = read_book(mk["yes"])
            if not book:
                broker.cancel_all()
                print(f"{i:>4} {'--':>6}  book empty -> pulled quotes")
                time.sleep(cfg.interval)
                continue
            mid = book["mid"]
            note = ""

            jump = last_mid is not None and abs(mid - last_mid) > cfg.kill_move
            if not cfg.live:
                simulate_fills(st, prev_bid, prev_ask, cfg.quote_size, book, cfg.max_inventory)

            if jump:
                broker.cancel_all()
                prev_bid = prev_ask = None
                note = f"KILL jump {(mid - last_mid) * 100:+.1f}c -> flat quotes"
                bid = ask = None
            else:
                bid, ask, bsz, asz = compute_quotes(mid, book["tick"], st.pos, cfg, mk["rmax"])
                share = reward_share(mid, bid, ask, bsz, asz, mk["rmax"], mk["rmin"], book)
                st.reward += mk["daily_reward"] * share * (cfg.interval / 86400.0)
                broker.reconcile(bid, ask, bsz, asz)
                prev_bid, prev_ask = (bid if bsz > 0 else None), (ask if asz > 0 else None)
                if bsz == 0:
                    note = "inv cap: bid pulled"
                if asz == 0:
                    note = "inv cap: ask pulled"
                note = note or f"share~{share * 100:.2f}% ~ ${mk['daily_reward'] * share:.1f}/day reward run-rate"

            last_mid = mid
            b = f"{bid:.3f}" if bid else "  -- "
            a = f"{ask:.3f}" if ask else "  -- "
            print(f"{i:>4} {mid:6.3f} {b:>6} {a:>6} {st.pos:6.0f} {st.cash:9.2f} "
                  f"{st.reward:7.3f} {st.pnl(mid):8.3f}  {note}")
            time.sleep(cfg.interval)
    except KeyboardInterrupt:
        print("\n^C — stopping.")
    finally:
        broker.cancel_all()
        if last_mid is not None:
            print(f"\nFINAL  pos={st.pos:.0f} shares | cash={st.cash:.2f} | reward~{st.reward:.3f} | "
                  f"fills={st.fills} | PnL~{st.pnl(last_mid):.3f} USDC")
            print("note: paper PnL = cash + pos*mid + estimated rewards. Reward share is a rough proxy.")


def _main(argv):
    from rtmde.config import load_config
    a = argv
    cfg = Config.from_config(load_config())
    cfg.live = "--live" in a

    def opt(flag, cast, default):
        return cast(a[a.index(flag) + 1]) if flag in a else default

    cfg.market_query = opt("--market", str, cfg.market_query)
    cfg.loops = opt("--loops", int, cfg.loops)
    cfg.interval = opt("--interval", float, cfg.interval)
    cfg.quote_size = opt("--size", float, cfg.quote_size)
    cfg.max_inventory = opt("--maxinv", float, cfg.max_inventory)
    run(cfg)


if __name__ == "__main__":
    _main(sys.argv[1:])

# ============================================================================ RISK NOTES
# - PAPER fills are optimistic on queue position (assume you are filled when price
#   merely reaches your quote) and pessimistic on direction (you fill on the adverse
#   move). Treat paper PnL as directional intuition, NOT a backtest. A real backtest
#   needs the full L2 feed + trade prints (the WebSocket feed) and queue modeling.
# - reward_share is a crude proxy (the real program samples every minute over a week
#   and normalizes against ALL makers' quadratic-weighted size). Measure ACTUAL reward
#   from the daily payout once live.
# - LIVE mode: verify py-clob-client calls against current docs; start with one market,
#   tiny size, wide kill_move; add per-market max exposure + a global daily-loss kill
#   switch before scaling. Never run unattended until watched through a news spike.
