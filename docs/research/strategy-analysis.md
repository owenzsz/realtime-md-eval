# Research note: what strategy is even worth evaluating?

> This note explains *why* the system evaluates the strategy it does. It is a research
> write-up, not investment advice and not a profit claim. All numbers are from a
> read-only live scan of public Polymarket data on 2026-06-14.

## Question

Before building real-time infrastructure, I wanted to know whether there is any
**non-directional, individually-reachable edge** on a mature prediction market — and if
so, what its risk actually is. Two candidates:

1. **Riskless structural arbitrage** (buy a complete set of mutually-exclusive outcomes
   for less than their guaranteed payout).
2. **Liquidity-reward market-making** (the venue pays makers who quote near mid).

## Method

I wrote a read-only, keyless scanner ([`rtmde/scanner/arb.py`](../../rtmde/scanner/arb.py))
that, for every live market, **walks the order book (VWAP)** and applies the **CLOB-v2
per-category taker-fee curve** before declaring an edge "actionable." The discipline that
matters: every "looks like +50c" candidate is re-checked against real depth and fees —
most evaporate as illiquid legs or stale top-of-book.

## Finding 1 — riskless arbitrage is effectively gone for a slow individual

Across **300 events / 5,237 markets / 8,247 order books**, after depth-walking and fees:
**0 actionable arbitrages.** The market is efficient to roughly a 0.5–1c overround.

The structural reason: the **CLOB v2 fee change (2026-04-28)** introduced a per-category
taker fee `fee/share = rate · (p·(1−p))^exp`, peaking at p=0.5. That 1–1.8% taker cost
swamps the sub-cent gross edges that thin baskets used to offer. Makers still pay 0%, and
**geopolitics is 0% taker** — the only corner where anything survives, and even there the
liquid opportunities are taken by sub-second bots.

The detailed per-method verdicts (neg-risk YES/NO baskets, binary merge, date-ladders,
cross-venue) all reduce to the same conclusion: the math still exists, but it lives in
slow, illiquid, capital-locked corners — an engineering/capital business, not free money.

## Finding 2 — the only structurally positive-EV non-directional play is reward MM

The venue pays **>$5M/month** in liquidity rewards to two-sided makers quoting near mid
(reward score is quadratic in closeness to mid). On the scan date, **229 markets** carried
active reward pools. This is the play that on-chain "high-turnover, low-margin" wallets
actually run.

**The honest caveat — and the whole reason this repo exists as an *evaluation* system:**
rewards are an *external subsidy* that makes market-making positive-EV in principle, **not
free money**. Your realized return depends on (a) the share of the pool you actually win
(competition is fierce) and (b) **inventory / adverse-selection control** — you tend to get
filled on the side the market is about to move against. Whether the subsidy beats the bleed
is an empirical question that depends on *which* markets and *when*.

## So: evaluate, don't assume

That empirical question is exactly what [`rtmde/eval`](../../rtmde/eval) measures: it
paper-runs the market-making strategy across many markets for days, models fills so that
adverse selection shows up, and attributes PnL by volatility regime to produce a go/no-go
verdict. The headline result it produced is in the top-level README ("What the data
showed"): naive MM is **net-negative on news-driven markets** and **net-positive on slow,
low-competition ones**. The system is built to reach that judgment *before* risking
capital or building a low-latency executor — which is the point.
