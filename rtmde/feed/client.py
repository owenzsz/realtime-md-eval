#!/usr/bin/env python3
"""rtmde.feed.client — REST data access, market discovery, and the fee model.

Single source of truth for:
  * HTTP helpers (``hget`` / ``hpost``) against the Gamma + CLOB public REST APIs
  * the CLOB-v2 taker-fee curve (``FEE`` / ``fee_share``) and event categorization
    (``categorize``) — imported by both the strategy labeler and the arb scanner
  * order-book normalization (``_normalize_book`` / ``read_book`` / ``read_books``)
  * reward-bearing market discovery (``rewarded_candidates`` / ``pick_market``)

Everything here is keyless and read-only (public endpoints). ``read_books`` issues
ONE ``POST /books`` per 50-token chunk, which keeps egress tiny — important when
running 7x24 on a metered free-tier VM.
"""
import json
import time
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
UA = {"User-Agent": "rtmde/0.1", "Content-Type": "application/json"}


def hget(url):
    """GET *url* and parse the JSON response."""
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))


def hpost(url, body):
    """POST *body* as JSON to *url* and parse the JSON response."""
    data = json.dumps(body).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA), timeout=30))


# --------------------------------------------------------------- fee model + categories
# CLOB-v2 taker-fee curve (since 2026-04-28): fee/share = rate * (p*(1-p))**exp.
# Makers pay 0; the curve peaks at p=0.5. Geopolitics is 0% (the cleanest category).
# This is the canonical copy; strategy labeling and the arb scanner both import it.
FEE = {
    "crypto": (0.07, 1.0), "economics": (0.05, 0.5), "finance": (0.04, 1.0),
    "politics": (0.04, 1.0), "tech": (0.04, 1.0), "culture": (0.05, 1.0),
    "weather": (0.05, 0.5), "sports": (0.03, 1.0), "geopolitics": (0.0, 1.0),
    "mentions": (0.04, 2.0), "default": (0.04, 1.0),
}


def fee_share(p, cat):
    """Per-share taker fee at price *p* for category *cat* (maker fee is always 0)."""
    r, e = FEE.get(cat, FEE["default"])
    p = min(max(p, 1e-6), 1 - 1e-6)
    return r * (p * (1 - p)) ** e


def categorize(ev):
    """Classify a Gamma event into a fee category from its tags/title, with keyword
    fallbacks for the common geopolitics / econ / politics / sports / crypto cases."""
    blob = " ".join((t.get("slug") or t.get("label", "")).lower() for t in (ev.get("tags") or [])) \
        + " " + ev.get("title", "").lower()
    for c in ["geopolitics", "crypto", "sports", "economics", "politics", "finance", "tech", "culture", "weather"]:
        if c in blob:
            return c
    if any(k in blob for k in ["war", "iran", "israel", "ukraine", "ceasefire", "nato", "peace",
                               "nuclear", "gaza", "hostage", "invasion", "strait", "airspace", "warship"]):
        return "geopolitics"
    if any(k in blob for k in ["fed", "cpi", "gdp", "rate", "inflation", "recession", "unemployment", "interest"]):
        return "economics"
    if any(k in blob for k in ["election", "president", "prime minister", "nominee", "senate", "governor", "parliament"]):
        return "politics"
    if any(k in blob for k in ["world cup", "nba", "nfl", "mlb", "ufc", "vs.", "champions"]):
        return "sports"
    if any(k in blob for k in ["bitcoin", "ethereum", "btc", "eth", "solana"]):
        return "crypto"
    return "default"


# --------------------------------------------------------------------------- book reads
def _normalize_book(b):
    """Normalize a raw CLOB book into a dict with best bid/ask, sizes, full ladders,
    mid, and tick. Returns None if the input is falsy or either side is empty."""
    if not b:
        return None
    asks = sorted(((float(a["price"]), float(a["size"])) for a in (b.get("asks") or [])), key=lambda x: x[0])
    bids = sorted(((float(a["price"]), float(a["size"])) for a in (b.get("bids") or [])), key=lambda x: -x[0])
    if not asks or not bids:
        return None
    return dict(bid=bids[0][0], ask=asks[0][0], bid_sz=bids[0][1], ask_sz=asks[0][1],
                bids=bids, asks=asks, mid=(bids[0][0] + asks[0][0]) / 2.0,
                tick=float(b.get("tick_size") or 0.01))


def read_book(token):
    """Fetch + normalize a single token's order book. None on any error or empty side."""
    try:
        return _normalize_book(hget(f"{CLOB}/book?token_id={token}"))
    except Exception:
        return None


def read_books(tokens):
    """Batch-fetch order books via ONE ``POST /books`` per 50-token chunk (egress-cheap).
    Returns ``{token: normalized_book | None}``."""
    out = {}
    chunk_size = 50
    for i in range(0, len(tokens), chunk_size):
        chunk = tokens[i:i + chunk_size]
        try:
            res = hpost(f"{CLOB}/books", [{"token_id": t} for t in chunk])
        except Exception:
            res = []
        got = {str(e.get("asset_id")): e for e in (res or [])}
        for t in chunk:
            out[t] = _normalize_book(got.get(str(t)))
        time.sleep(0.05)
    return out


# ------------------------------------------------------------------- market discovery
def rewarded_candidates(query=""):
    """Rank reward-bearing markets by metadata only (no book probe).

    Returns a list of tuples sorted best-first::

        (score, daily_reward, category, question, event_title, [yes_tok, no_tok], rmin, rmax)

    Prefers 0%-fee geopolitics. Shared by ``pick_market`` and the evaluation harness
    so both see the same market universe.
    """
    evs = []
    for off in range(0, 300, 100):
        evs += hget(f"{GAMMA}/events?closed=false&active=true&limit=100&offset={off}&order=volume24hr&ascending=false")
        time.sleep(0.1)
    cands = []
    for e in evs:
        cat = categorize(e)
        for m in e.get("markets") or []:
            if not m.get("enableOrderBook") or m.get("closed") or m.get("acceptingOrders") is False:
                continue
            q = m.get("question", "")
            if query and query.lower() not in q.lower() and query.lower() not in e.get("title", "").lower():
                continue
            cr = m.get("clobRewards")
            daily = sum(float(x.get("rewardsDailyRate", 0) or 0) for x in cr) if isinstance(cr, list) else 0.0
            if daily <= 0 and not query:
                continue
            try:
                cl = json.loads(m["clobTokenIds"])
            except Exception:
                continue
            score = daily + (1e6 if cat == "geopolitics" else 0)   # prefer 0%-fee geopolitics
            cands.append((score, daily, cat, q, e.get("title", ""), cl,
                          float(m.get("rewardsMinSize") or 0), float(m.get("rewardsMaxSpread") or 5) / 100.0))
    cands.sort(key=lambda x: -x[0])
    return cands


def pick_market(query):
    """Find ONE reward-bearing market with a LIVE two-sided book (skips stale/dead
    reward configs whose book is empty). Probes only the top candidates."""
    for _score, daily, cat, q, et, cl, rmin, rmax in rewarded_candidates(query)[:30]:
        bk = read_book(cl[0])
        if not bk or bk["bid"] is None or bk["ask"] is None:
            continue
        return dict(question=q, event=et, cat=cat, yes=cl[0], no=cl[1],
                    tick=bk["tick"], daily_reward=daily, rmin=rmin, rmax=rmax)
    return None
