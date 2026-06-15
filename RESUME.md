# Resume bullets — realtime-md-eval

Copy-ready bullets for a systems / MLSys / infra SWE resume. All metrics are
infrastructure facts (latency, cost, throughput, fault-tolerance) — **no profit or
return claims**.

**One-liner (project header):**
> Real-time market-data ingestion + offline strategy-evaluation system — async WebSocket
> ingestion, a ~$0/month self-healing deployment, and a paper-evaluation harness that
> reaches a validate-before-you-build verdict. Python. [github.com/…/realtime-md-eval]

**Bullets (pick 4–6):**

- Built a **real-time order-book ingestion service** (async WebSocket, in-memory snapshot
  + delta reconstruction, watchdog reconnect) delivering **~90 ms** update latency —
  **~100× fresher** than the 10 s REST-polling baseline.

- Ran it **7×24 at ~$0/month** on a GCP free-tier `e2-micro`: **IPv6-only** networking to
  avoid the external-IPv4 charge, **IAP-tunneled** SSH (no public port), and **systemd
  `Restart=always`** self-healing with daily sessions that prune resolved markets and
  resume automatically across crashes and reboots.

- Cut each polling round to a **single batched `POST /books`** request, keeping egress
  under the 1 GB/month free-tier cap while tracking multiple live markets.

- Designed an **offline strategy-evaluation harness** that paper-simulates fills (modeling
  adverse selection), persists multi-day state, and **attributes PnL by price-volatility
  regime**, emitting a deterministic go/no-go verdict via a single pure aggregation
  function.

- Used the evaluation to make a **validate-before-deploy** call: measured that the
  candidate strategy is net-negative under news-driven volatility (adverse selection) and
  net-positive in calm, low-competition conditions — capturing a live geopolitical news
  spike as the decisive case study — *before* committing to a low-latency executor.

- Refactored a research prototype into a **tested, importable Python package** (I/O split
  from pure logic, single-source fee model + categorization, pure report aggregation) with
  a `pytest` suite covering the book state machine, quoting/inventory math, fee curve,
  depth-walk, and verdict logic.

**Tech:** Python, asyncio, `websockets`, systemd, GCP (free-tier e2-micro, IAP), pytest.
