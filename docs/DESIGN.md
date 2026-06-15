# Design notes: a real-time ingestion + offline strategy-evaluation system

This is an engineering write-up of `realtime-md-eval` — what it does, why it's built
this way, and where the interesting decisions were. It is a personal, paper-trading
project; there are no profit claims and no production-scale claims. The value is in the
systems work: low-latency ingestion, a cheap fault-tolerant deployment, and a disciplined
evaluation harness that reaches a decision before any capital or fast-path infra is built.

## 1. The problem

Prediction-market order books (Polymarket's CLOB) are the data source. Two needs:

1. **Ingest the book in real time.** REST polling at 10 s is far too slow to see how a
   book actually behaves around news. I need a push feed with sub-second latency and a
   correct in-memory book reconstruction.
2. **Decide whether a strategy is worth running — before building the fast path.** A
   market-making strategy *looks* attractive (the venue subsidizes makers), but its real
   risk is adverse selection. Building a low-latency executor for an unproven edge is
   wasted work. So: measure first, cheaply, for days.

## 2. Architecture

A small Python package, `rtmde`, split by responsibility into an acyclic dependency graph:

```
                         ┌─────────────────────────────────────┐
                         │  feed.client (REST)                  │
                         │  discovery + batch /books + fee model │
                         └───────────────┬──────────────────────┘
            token_ids                    │
        ┌────────────────────────────────┼───────────────────────────┐
        ▼                                 ▼                            ▼
┌──────────────────┐          ┌────────────────────┐      ┌────────────────────┐
│ feed.ws          │          │ eval.strategy      │      │ scanner.arb        │
│ WebSocket push   │ on_update│ quotes / inventory │      │ depth+fee-aware    │
│ ~90 ms, LiveBook │─────────▶│ kill-switch (under │      │ arb detector       │
│ reconnect/wdog   │          │ evaluation)        │      │ (bonus tool)       │
└──────────────────┘          └─────────┬──────────┘      └────────────────────┘
                                        ▼
                         ┌────────────────────────────────┐
                         │ eval.harness (multi-day)        │
                         │ paper-simulate fills, persist    │
                         │ state, append samples.jsonl      │
                         └───────────────┬─────────────────┘
                                         ▼
                         ┌────────────────────┐   ┌────────────────────┐
                         │ eval.report        │──▶│ notify.digest      │
                         │ aggregate + verdict │   │ stdout | Telegram  │
                         └────────────────────┘   └────────────────────┘
```

```
feed.client → (stdlib)            eval.report  → config
feed.ws     → feed.client         eval.harness → feed.client, eval.strategy, config
eval.strategy → feed.client       notify.digest→ eval.report
scanner.arb → feed.client
```

`feed.client` is the single source of truth for HTTP access, the CLOB-v2 fee model, event
categorization, and book normalization. Everything else depends inward toward it; nothing
depends on `notify` or `harness`. This keeps each module holdable in your head and
independently testable.

## 3. Engineering highlights

### Real-time feed (`feed.ws`)
- Async WebSocket client (`websockets`) on the CLOB market channel. Maintains a `LiveBook`
  per token: full-snapshot on `book`, absolute-size level updates on `price_change`
  (size 0 removes a level).
- **~90 ms** observed update latency vs **10 s** REST polling — about **100× fresher**.
- Robust to the channel's quirks: there are no sequence numbers and the message hash is
  unreliable, so the book is rebuilt on each snapshot and a **watchdog forces a reconnect
  after 120 s of silence** (the documented "silent freeze"). Keepalive is a literal
  `PING` every 10 s; reconnects use capped exponential backoff.

### ~$0/month, fault-tolerant deployment (`deploy/`)
- Single GCP free-tier `e2-micro`, **IPv6-only** (no external IPv4 → avoids the ~$3/mo
  charge; the API is reachable over IPv6 via Cloudflare), reached over an **IAP SSH
  tunnel** (no public port).
- **`systemd Restart=always`** with 1-day sessions: each new day prunes resolved markets,
  tops back up, and keeps accumulating state; it self-heals across crashes and reboots.
- A `systemd` timer pushes a daily verdict digest.

### Cheap egress (`feed.client.read_books`)
- Each polling round fetches *all* tracked books with **one batched `POST /books`** (50
  tokens/chunk) instead of N requests, keeping egress comfortably under the 1 GB/mo free
  cap. The same batching backs the scanner.

## 4. Evaluation methodology (`eval/`)

The point of the harness is an honest go/no-go decision, not a flattering number.

- **Fills model adverse selection on purpose.** `simulate_fills` fills a resting bid only
  when price falls *to/through* it and a resting ask only when price rises *to/through* it
  — i.e. you trade exactly when the market moves against you. Paper PnL is therefore
  conservative on direction (and optimistic on queue position — see Limitations).
- **Reward is modeled as a competition-weighted share**, quadratic in closeness to mid,
  two-sided only — a deliberately rough proxy that the live `--probe`/`--reconcile` path is
  meant to calibrate against the real payout.
- **Attribution by volatility regime.** Every per-sample change in (reward + inventory
  PnL) is bucketed by the size of the mid move. This is what surfaces *where* the strategy
  earns vs bleeds, instead of one blended number.
- **A single verdict function** (`report.aggregate`) turns the accumulated samples into
  `insufficient | thin | positive | negative` with explicit thresholds, so the English
  report and the Telegram digest can never disagree.

## 5. Refactor decisions

This repo is a clean re-implementation of a working research prototype. Notable cleanups:

- **One fee model + one categorizer.** The prototype had two copies (the strategy labeler
  and the scanner); they're unified in `feed.client` so the fee curve and category rules
  have a single source of truth.
- **I/O split from logic.** REST access (`feed.client`) is separated from the pure
  quoting/inventory logic (`eval.strategy`), so the strategy math is testable without a
  network.
- **Reporting made pure.** `aggregate(rows)` is a pure function and `render_report(rows)`
  returns a string instead of printing, which makes both unit-testable and reusable by the
  notifier.
- **State is gitignored and path-configurable.** Runtime `state/` (json + jsonl) never
  touches version control.
- A focused **pytest suite** covers the book state machine, the quoting/inventory/fill
  math (ported from the prototype's self-test), the fee curve, the depth walk, the verdict
  aggregation, and config loading.

## 6. Limitations (honest)

- **Paper, not a backtest.** Fills assume top-of-queue and ignore partial-fill dynamics;
  treat paper PnL as directional intuition. A real backtest needs full L2 + trade prints
  and queue-position modeling.
- **The reward share is a proxy.** The real program samples every minute over a week and
  normalizes against all makers' hidden size; only a live probe tells you your true share.
- **Single VM, personal scale.** No HA, no horizontal scaling, no claim of either. The
  goal was a cheap, reliable collector and a trustworthy evaluation — not a trading system
  at scale.
- **Live order placement is an untested template** (`eval.strategy.LiveBroker`), kept
  off by default and behind an explicit confirmation; it exists to document the path, not
  as a vetted execution engine.
