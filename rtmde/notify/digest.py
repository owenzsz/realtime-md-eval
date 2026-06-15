#!/usr/bin/env python3
"""rtmde.notify.digest — render the evaluation verdict to stdout or Telegram.

``format_digest(data)`` turns an ``rtmde.eval.report.aggregate`` dict into a compact
English summary. ``send(text, backend)`` delivers it: ``stdout`` (default) just prints;
``telegram`` posts via the Bot API. Credentials are read from environment variables (or
a chmod-600 env file) — NEVER hard-coded, logged, or passed on the command line.

Telegram setup (yours to do, one time):
  1. Message @BotFather -> /newbot -> copy the bot token.
  2. Store it locally (not in the repo):
       umask 077; printf 'TG_BOT_TOKEN=PASTE\\nTG_CHAT_ID=\\n' > ~/.rtmde_tg.env
  3. Message your new bot once, then:  python -m rtmde.notify.digest --getchat --telegram
     put the printed chat_id into ~/.rtmde_tg.env (TG_CHAT_ID=...).
  4. python -m rtmde.notify.digest --test --telegram     # should arrive in Telegram

Usage:
  python -m rtmde.notify.digest --report            # print the digest (backend from config)
  python -m rtmde.notify.digest --report --telegram # force Telegram delivery
  python -m rtmde.notify.digest "any text"
  python -m rtmde.notify.digest --test | --getchat
"""
import json
import os
import sys
import urllib.parse
import urllib.request

from rtmde.eval.report import VERDICTS


def _m(x):
    """Format a signed dollar amount, e.g. +$1.20 / -$3.40."""
    return f"+${x:.2f}" if x >= 0 else f"-${abs(x):.2f}"


def format_digest(data):
    """Render an aggregate dict (from ``rtmde.eval.report.aggregate``) as a compact
    English digest. Safe on ``None`` (no data yet)."""
    if not data:
        return "No data yet — the collector has not accumulated samples."
    out = [
        "rtmde — strategy evaluation digest",
        f"{data['samples']} snapshots / {data['markets']} markets / {data['span_h']:.2f}h",
        "",
        "By category (net-sorted):",
    ]
    for c in sorted(data["cats"].values(), key=lambda c: -c["net"]):
        if c["net"] > 0.02 and c["vol"] >= -0.01:
            mark, read = "[+]", "earns calmly"
        elif c["net"] < -0.01:
            mark, read = "[-]", "adverse selection"
        else:
            mark, read = "[.]", "net+ but bleeds on moves"
        extra = f", vol {_m(c['vol'])}" if c["net"] < -0.01 else ""
        out.append(f"{mark} {c['cat']:11s} net {_m(c['net'])} "
                   f"(reward ${c['reward']:.2f} / inv {_m(c['inv'])}{extra}) - {read}")
    out += [
        "",
        f"TOTAL net {_m(data['tot_net'])} = reward ${data['tot_reward']:.2f} "
        f"- inventory ${abs(data['tot_inv']):.2f} ({data['fills']} fills)",
        f"run-rate ~{_m(data['run_rate_day'])}/day ~ {data['pct_day']:+.1f}%/day (paper, noisy)",
        "",
        f"Verdict: {VERDICTS[data['verdict_code']]}",
    ]
    return "\n".join(out)


def _creds(env_file="~/.rtmde_tg.env"):
    d = {}
    path = os.path.expanduser(env_file)
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    tok = d.get("TG_BOT_TOKEN") or os.environ.get("TG_BOT_TOKEN", "")
    cid = d.get("TG_CHAT_ID") or os.environ.get("TG_CHAT_ID", "")
    return tok, cid


def _api(token, method, params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    return json.load(urllib.request.urlopen(req, timeout=20))


def send(text, backend="stdout", env_file="~/.rtmde_tg.env"):
    """Deliver *text*. ``stdout`` prints it (that is the delivery); ``telegram`` posts
    it via the Bot API. Returns True on success."""
    if backend == "stdout":
        print(text)
        return True
    tok, cid = _creds(env_file)
    if not tok or not cid:
        print(f"Missing Telegram creds — set TG_BOT_TOKEN and TG_CHAT_ID (env or {env_file}).", file=sys.stderr)
        return False
    params = {"chat_id": cid, "text": text[:4096], "disable_web_page_preview": "true"}
    try:
        r = _api(tok, "sendMessage", params)
        ok = bool(r.get("ok"))
        print("sent" if ok else f"telegram error: {r}", file=sys.stderr)
        return ok
    except Exception as e:
        print("send failed:", e, file=sys.stderr)
        return False


def getchat(env_file="~/.rtmde_tg.env"):
    """Print chat ids the bot can see (message your bot first)."""
    tok, _ = _creds(env_file)
    if not tok:
        sys.exit(f"Set TG_BOT_TOKEN in {env_file} (or env) first.")
    try:
        r = _api(tok, "getUpdates", {})
    except Exception as e:
        sys.exit(f"getUpdates failed: {e}")
    seen = set()
    for u in r.get("result", []):
        ch = (u.get("message") or u.get("channel_post") or {}).get("chat") or {}
        if ch.get("id") and ch["id"] not in seen:
            seen.add(ch["id"])
            print(f"chat_id={ch['id']}  type={ch.get('type')}  "
                  f"name={ch.get('title') or ch.get('username') or ch.get('first_name')}")
    if not seen:
        print("No chats yet — message your bot in Telegram first, then re-run --getchat.")


def _main(argv):
    from rtmde.config import load_config
    from rtmde.eval.report import report_data
    cfg = load_config()
    backend = "telegram" if "--telegram" in argv else cfg["notify"]["backend"]
    env_file = cfg["notify"]["telegram_env_file"]
    a = [x for x in argv if x != "--telegram"]
    if not a:
        print("usage: python -m rtmde.notify.digest <text> | --report | --test | --getchat [--telegram]")
        return
    if a[0] == "--getchat":
        getchat(env_file)
    elif a[0] == "--test":
        send("rtmde notify connected.", backend, env_file)
    elif a[0] == "--report":
        send(format_digest(report_data()), backend, env_file)
    else:
        send(" ".join(a), backend, env_file)


if __name__ == "__main__":
    _main(sys.argv[1:])
