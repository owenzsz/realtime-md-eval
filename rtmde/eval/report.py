#!/usr/bin/env python3
"""rtmde.eval.report — aggregate the sample log into a go/no-go verdict.

``aggregate(rows)`` is a PURE function (the single source of the verdict math).
``report_data()`` loads rows from the configured jsonl log and aggregates them.
``render_report(rows)`` returns the full human-readable report as a string (no
print side-effects), so it is reusable by the notify digest and unit-testable.

    python -m rtmde.eval.report          # print the report for the current state
"""
import json
import os
import sys

VERDICTS = {
    "insufficient": "too little data — collect >= a full day (ideally 3-5) spanning calm AND news periods.",
    "positive":     "edge looks POSITIVE — rewards dominate, inventory bleed contained. Probe live (cautiously).",
    "thin":         "net positive but inventory losses eat most of the reward — thin. Retune before scaling.",
    "negative":     "NET NEGATIVE — inventory / adverse-selection swamps rewards. Do NOT scale: try a wider "
                    "kill_move, slower markets, smaller size, or tighter requote.",
}


def aggregate(rows):
    """Aggregate sample rows into structured numbers + a verdict code. Pure; returns
    None when there are no rows. Shared by ``render_report`` and the notify digest so
    the English report and the digest can never diverge."""
    if not rows:
        return None
    by = {}
    for r in rows:
        by.setdefault(r["tok"], []).append(r)
    span_h = (max(r["ts"] for r in rows) - min(r["ts"] for r in rows)) / 3600.0
    tot_r = tot_e = tot_f = 0.0
    cats = {}
    for tok, rs in by.items():
        rs.sort(key=lambda x: x["ts"])
        cat = rs[-1].get("cat", "?")
        reward = rs[-1]["reward"]
        equity = rs[-1]["equity"]
        tot_r += reward
        tot_e += equity
        tot_f += sum(x["fills"] for x in rs)
        c = cats.setdefault(cat, {"cat": cat, "mkts": 0, "reward": 0.0, "inv": 0.0,
                                  "net": 0.0, "calm": 0.0, "vol": 0.0})
        c["mkts"] += 1
        c["reward"] += reward
        c["inv"] += equity
        c["net"] += reward + equity
        for a, b in zip(rs[:-1], rs[1:]):
            dnet = (b["reward"] - a["reward"]) + (b["equity"] - a["equity"])
            c["calm" if abs(b["dmid"]) < 0.005 else "vol"] += dnet
    tot_net = tot_r + tot_e
    avg_mid = sum(r["mid"] for r in rows) / len(rows)
    cap = 3 * 200 * avg_mid                       # ~2 sides quoting + some inventory at size 200
    perday = tot_net / max(span_h, 1e-9) * 24
    if span_h < 2:
        vc = "insufficient"
    elif tot_net > 0 and tot_e > -tot_r * 0.5:
        vc = "positive"
    elif tot_net > 0:
        vc = "thin"
    else:
        vc = "negative"
    return {"samples": len(rows), "markets": len(by), "span_h": span_h, "fills": int(tot_f),
            "tot_reward": tot_r, "tot_inv": tot_e, "tot_net": tot_net, "cats": cats,
            "cap": cap, "run_rate_day": perday, "pct_day": perday / max(cap, 1) * 100,
            "verdict_code": vc}


def load_rows(samples_file=None):
    """Read sample rows from the jsonl log (default: the configured state path)."""
    if samples_file is None:
        from rtmde.config import load_config, state_paths
        _, samples_file, _ = state_paths(load_config())
    if not os.path.exists(samples_file):
        return []
    with open(samples_file) as f:
        return [json.loads(line) for line in f if line.strip()]


def report_data(samples_file=None):
    """Load rows from disk and aggregate them. None if there is no data."""
    return aggregate(load_rows(samples_file))


def render_report(rows):
    """Render the full human-readable evaluation report as a string. Empty-data safe."""
    if not rows:
        return "No history yet. Run a collect session first."
    data = aggregate(rows)
    by = {}
    for r in rows:
        by.setdefault(r["tok"], []).append(r)

    w = 94
    out = []
    out.append("=" * w)
    out.append(f"MM EDGE VALIDATION  —  {data['samples']} samples across {data['markets']} markets, "
               f"{data['span_h']:.2f}h wall-clock")
    out.append("=" * w)
    out.append(f"{'market':40s} {'reward$':>8} {'invPnL$':>8} {'net$':>8} {'fills':>5} {'rwd$/day':>8}")
    out.append("-" * w)

    vbuckets = {"calm |d|<0.5c": [0, 0.0], "med 0.5-2c": [0, 0.0], "vol >2c": [0, 0.0]}
    tot_r = tot_e = tot_f = 0.0
    for tok, rs in by.items():
        rs.sort(key=lambda x: x["ts"])
        reward = rs[-1]["reward"]
        equity = rs[-1]["equity"]
        net = reward + equity
        fills = sum(x["fills"] for x in rs)
        rr = sum(x.get("runrate", 0.0) for x in rs) / len(rs)
        tot_r += reward
        tot_e += equity
        tot_f += fills
        out.append(f"{rs[0]['q'][:40]:40s} {reward:8.3f} {equity:8.3f} {net:8.3f} {int(fills):5d} {rr:8.1f}")
        for a, b in zip(rs[:-1], rs[1:]):
            dnet = (b["reward"] - a["reward"]) + (b["equity"] - a["equity"])
            v = abs(b["dmid"])
            k = "calm |d|<0.5c" if v < 0.005 else ("med 0.5-2c" if v < 0.02 else "vol >2c")
            vbuckets[k][0] += 1
            vbuckets[k][1] += dnet
    out.append("-" * w)
    out.append(f"{'TOTAL':40s} {tot_r:8.3f} {tot_e:8.3f} {data['tot_net']:8.3f} {int(tot_f):5d}")

    out.append("")
    out.append("PER-CATEGORY  (calm$ = PnL in quiet samples, vol$ = PnL when price moved):")
    out.append(f"  {'category':12s} {'mkts':>4} {'reward$':>8} {'invPnL$':>8} {'net$':>8} {'calm$':>7} {'vol$':>7}  read")
    for cat, c in sorted(data["cats"].items(), key=lambda kv: kv[1]["net"]):
        if c["net"] >= 0 and c["vol"] >= -1e-9:
            read = "earns calmly, no bleed yet"
        elif c["net"] >= 0:
            read = "net+ but bleeds on moves"
        else:
            read = "net- (adverse selection)"
        out.append(f"  {cat:12s} {c['mkts']:>4} {c['reward']:8.3f} {c['inv']:8.3f} {c['net']:8.3f} "
                   f"{c['calm']:7.3f} {c['vol']:7.3f}  {read}")

    out.append("")
    out.append("WHERE THE PnL COMES FROM overall (net delta per sample, bucketed by price move):")
    out.append(f"  {'regime':18s} {'samples':>8} {'net $':>10}   interpretation")
    for k, (cnt, s) in vbuckets.items():
        tag = "rewards earned calmly" if (k.startswith("calm") and s >= 0) else \
              ("adverse-selection bleed" if s < 0 else "")
        out.append(f"  {k:18s} {cnt:8d} {s:10.4f}   {tag}")

    out.append("")
    out.append("-" * w)
    out.append(f"projected reward: ${data['tot_reward']:.3f} | simulated inventory PnL: ${data['tot_inv']:+.3f} | "
               f"NET: ${data['tot_net']:+.3f} over {data['span_h']:.2f}h")
    out.append(f"run-rate: ${data['run_rate_day']:+.2f}/day on ~${data['cap']:.0f} deployed  "
               f"~ {data['pct_day']:+.2f}%/day (paper, rough)")
    out.append(f"VERDICT: {VERDICTS[data['verdict_code']]}")
    out.append("note: paper fills are simplified (no queue position; fills on the adverse move). "
               "Confirm with a small real-money probe.")
    return "\n".join(out)


def _main(argv):
    print(render_report(load_rows()))


if __name__ == "__main__":
    _main(sys.argv[1:])
