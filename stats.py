"""Usage stats over every source in the tokenhub DB: activity, streaks, sessions, models, tokens and cost.

CLI: python3 stats.py [all|7d|30d]    Library: compute(db, period) -> dict, used by the menu bar plugin."""
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta

DB = os.environ.get("TOKENHUB_DB", os.path.expanduser("~/.tokenhub/usage.db"))
TOK = "(input+output+cache_read+cache_write)"
LEVELS = [(90, 66, 60), (139, 94, 80), (176, 113, 92), (204, 125, 95)]  # light to heavy


def k(n):
    n = float(n or 0)
    for lim, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= lim:
            return f"{n / lim:.1f}{suf}"
    return str(int(n))


def money(x):
    return f"${x:,.2f}" if x < 100 else f"${x:,.0f}"


def pretty(model):
    """claude-opus-5-5 -> Opus 5.5; other names unchanged."""
    m = re.match(r"claude-([a-z]+)-([\d-]+?)(?:-\d{8})?$", model or "")
    return f"{m[1].title()} {m[2].replace('-', '.')}" if m else (model or "?")


def dur(seconds):
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    return " ".join(p for p in (f"{d}d" if d else "", f"{h}h" if h or d else "", f"{r // 60}m") if p)


def streaks(days, today):
    """(longest, current) runs of consecutive active dates; current ends today, or yesterday if today is idle."""
    ds = sorted(days)
    best = run = 0
    prev = None
    for d in ds:
        run = run + 1 if prev and d - prev == timedelta(1) else 1
        best, prev = max(best, run), d
    cur, d = 0, today if today in days else today - timedelta(1)
    while d in days:
        cur, d = cur + 1, d - timedelta(1)
    return best, cur


def claude_cache(path=None):
    """Claude Code's stats cache: its session history is not recoverable from the DB, so sessions and active days come from here."""
    try:
        d = json.load(open(path or os.path.expanduser("~/.claude/stats-cache.json")))
        return {"through": d["lastComputedDate"], "longest": d["longestSession"]["duration"] / 1000,
                "days": {date.fromisoformat(x["date"]): x.get("sessionCount", 0) for x in d["dailyActivity"]},
                "tokens": sum(u.get(x, 0) for u in d["modelUsage"].values() for x in (
                    "inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"))}
    except (OSError, ValueError, KeyError):
        return None


def compute(db=None, period="all", today=None, cache=None):
    """Stats for 'all', '7d' or '30d'; None when the DB is missing."""
    db = db or DB
    if not os.path.exists(db):
        return None
    today = today or date.today()
    since = {"all": "0001-01-01", "7d": str(today - timedelta(6)), "30d": str(today - timedelta(29))}[period]
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    where = "status<400 AND date(ts,'localtime')>=?"
    q = lambda sql: c.execute(sql, (since,)).fetchall()
    r = {"period": period, "today": today}
    tot = c.execute(f"SELECT COUNT(*),COALESCE(SUM(input),0),COALESCE(SUM(output),0),COALESCE(SUM(cache_read),0),"
                    f"COALESCE(SUM(cache_write),0),COALESCE(SUM(cost_usd),0),COALESCE(SUM(est_usd),0) FROM usage WHERE {where}",
                    (since,)).fetchone()
    r.update(zip(("requests", "input", "output", "cache_read", "cache_write", "cost_reported", "cost_est"), tot))
    r["tokens"] = sum(tot[1:5])
    r["cost"] = r["cost_reported"] + r["cost_est"]
    daily = {date.fromisoformat(d): t for d, t in q(f"SELECT date(ts,'localtime'), SUM{TOK} FROM usage WHERE {where} GROUP BY 1")}
    r["daily"] = daily
    cc = claude_cache() if cache is None else cache
    lo = date.fromisoformat(since)
    cc_days = {d: n for d, n in (cc or {}).get("days", {}).items() if d >= lo}
    active = set(daily) | set(cc_days)
    r["active_days"] = len(active)
    first = min(active) if active else today
    r["span_days"] = (today - (first if period == "all" else lo)).days + 1
    r["longest_streak"], r["current_streak"] = streaks(active, today)
    r["most_active_day"] = max(daily, key=daily.get) if daily else None
    # DB sessions: every source except Anthropic, plus Anthropic rows newer than the cache
    sess = c.execute(f"SELECT session, MIN(ts), MAX(ts) FROM usage WHERE {where} AND session IS NOT NULL AND session!='' "
                     f"AND (source!='anthropic' OR date(ts,'localtime')>?) GROUP BY session",
                     (since, cc["through"] if cc else "0000-01-01")).fetchall()
    spans = [(datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() for _, a, b in sess]
    r["sessions"] = len(sess) + sum(cc_days.values())
    r["longest_session"] = max([*spans, cc["longest"] if cc and period == "all" else 0])
    r["models"] = [dict(zip(("model", "input", "output", "cache_read", "cache_write", "requests", "cost"), row)) for row in q(
        f"SELECT model, SUM(input), SUM(output), SUM(cache_read), SUM(cache_write), COUNT(*), "
        f"COALESCE(SUM(cost_usd),0)+COALESCE(SUM(est_usd),0) FROM usage WHERE {where} GROUP BY model ORDER BY SUM{TOK} DESC")]
    for m in r["models"]:
        m["tokens"] = sum(m[x] or 0 for x in ("input", "output", "cache_read", "cache_write"))
        m["share"] = m["tokens"] / r["tokens"] if r["tokens"] else 0
    r["favorite"] = r["models"][0]["model"] if r["models"] else None
    r["by_source"] = {s: (t, cost) for s, t, cost in q(
        f"SELECT source, SUM{TOK}, COALESCE(SUM(cost_usd),0)+COALESCE(SUM(est_usd),0) FROM usage WHERE {where} GROUP BY source ORDER BY 2 DESC")}
    # Claude Code's own all-time token total counts replayed history twice; tokens_cc swaps it in for the DB's unique-request figure
    r["tokens_cc"] = None
    if cc and period == "all":
        mine = r["by_source"].get("anthropic", (0, 0))[0]
        after = c.execute(f"SELECT COALESCE(SUM{TOK},0) FROM usage WHERE {where} AND source='anthropic' AND date(ts,'localtime')>?",
                          (since, cc["through"])).fetchone()[0]
        r["tokens_cc"] = r["tokens"] - mine + cc["tokens"] + after
    n_cost = r["cost"]
    r["avg_cost_day"] = n_cost / r["active_days"] if r["active_days"] else 0
    r["avg_cost_session"] = n_cost / r["sessions"] if r["sessions"] else 0
    r["avg_cost_request"] = n_cost / r["requests"] if r["requests"] else 0
    return r


def heatmap(daily, today, weeks=26, color=True):
    """Seven rows (Mon..Sun), one column per week, intensity by quartile of daily tokens."""
    start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
    vals = sorted(v for v in daily.values() if v)
    cut = [vals[int(len(vals) * p)] for p in (0, .25, .5, .75)] if vals else [0] * 4
    rows = []
    for wd in range(7):
        cells = []
        for w in range(weeks):
            d = start + timedelta(days=wd + 7 * w)
            v = daily.get(d, 0)
            if d > today:
                cells.append(" ")
            elif not v:
                cells.append("\x1b[38;2;70;72;80m·\x1b[0m" if color else "·")
            else:
                lvl = sum(v >= x for x in cut) - 1
                cells.append("\x1b[38;2;%d;%d;%dm■\x1b[0m" % LEVELS[lvl] if color else "■")
        rows.append("".join(cells))
    return rows


def spark(daily, today, days=30):
    vs = [daily.get(today - timedelta(days - 1 - i), 0) for i in range(days)]
    top = max(vs) or 1
    return "".join(" ▁▂▃▄▅▆▇█"[round(v / top * 8)] for v in vs)


def lines(r, weeks=53, color=True):
    """Plain-text report for the terminal."""
    if r is None or not r["requests"]:
        return ["no usage in this period"]
    out = [*[("Mon", "", "Wed", "", "Fri", "", "")[i].ljust(4) + row for i, row in enumerate(heatmap(r["daily"], r["today"], weeks, color))], ""]
    fav = pretty(r["favorite"])
    mad = r["most_active_day"]
    out += [f"Period: {r['period']}   Favorite model: {fav}   Total tokens: {k(r['tokens_cc'] or r['tokens'])}",
            f"Sessions: {r['sessions']:,}   Longest session: {dur(r['longest_session'])}",
            f"Active days: {r['active_days']}/{r['span_days']}   Longest streak: {r['longest_streak']} days",
            f"Most active day: {mad:%b} {mad.day}   Current streak: {r['current_streak']} days",
            f"Input {k(r['input'])} · Output {k(r['output'])} · Cache read {k(r['cache_read'])} · Cache write {k(r['cache_write'])}",
            "",
            f"Cost {money(r['cost'])}  (provider-reported {money(r['cost_reported'])} + list-price estimate {money(r['cost_est'])})",
            f"Avg {money(r['avg_cost_day'])}/active day · {money(r['avg_cost_session'])}/session · {money(r['avg_cost_request'])}/request",
            "", "Last 30 days  " + spark(r["daily"], r["today"]), "", "Models"]
    for m in r["models"]:
        out += [f"  {pretty(m['model'])} ({m['share']:.1%})  {money(m['cost'])}",
                f"    In: {k(m['input'])} · Out: {k(m['output'])} · Cache: {k(m['cache_read'])} read · {k(m['cache_write'])} write"]
    if r["tokens_cc"]:
        out += ["", f"Note: total includes Claude Code's replayed-history count; {k(r['tokens'])} tokens are unique requests."]
    return out


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("\n".join(lines(compute(period=p))))
