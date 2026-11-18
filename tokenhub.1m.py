#!/opt/homebrew/bin/python3
# <xbar.title>tokenhub</xbar.title>
# <xbar.desc>Token usage from the local tokenhub proxy</xbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
import json
import os
import sqlite3

DB = os.environ.get("TOKENHUB_DB", os.path.expanduser("~/.tokenhub/usage.db"))


def k(n):
    n = int(n or 0)
    return f"{n / 1e9:.1f}B" if n >= 1e9 else f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)


def claude_stats(c):
    """Claude Code's own all-time total: its stats cache (through lastComputedDate) plus hub rows after that date."""
    try:
        d = json.load(open(os.path.expanduser("~/.claude/stats-cache.json")))
        base = sum(u.get(x, 0) for u in d["modelUsage"].values()
                   for x in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"))
        after = c.execute("SELECT COALESCE(SUM(input+output+cache_read+cache_write),0) FROM usage WHERE source='anthropic' "
                          "AND date(ts,'localtime') > ?", (d["lastComputedDate"],)).fetchone()[0]
        return base + after
    except (OSError, ValueError, KeyError):
        return None


def main():
    if not os.path.exists(DB):
        print("tokens: -")
        print("---")
        print("no usage yet; start hub.py")
        return
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    q = ("SELECT source, COUNT(*), SUM(input), SUM(output), SUM(cache_read), SUM(cost_usd), SUM(cache_write) FROM usage "
         "WHERE date(ts,'localtime') >= date('now','localtime',?) GROUP BY source ORDER BY SUM(input+output+cache_read+cache_write) DESC")
    today, week, ever = (c.execute(q, (d,)).fetchall() for d in ("+0 days", "-6 days", "-36500 days"))
    full = lambda r: (r[2] or 0) + (r[3] or 0) + (r[4] or 0) + (r[6] or 0)
    cc = claude_stats(c)  # Claude Code's own figure counts replayed history twice; the DB rows are unique requests
    tot = sum(full(r) for r in ever if not (cc and r[0] == "anthropic")) + (cc or 0)
    print(f"tokens {k(tot)}")
    print("---")
    for title, rows in (("Today", today), ("Last 7 days", week), ("All time", ever)):
        print(f"{title} | size=11")
        for s, n, i, o, cr, cost, cw in rows:
            extra = f"  ${cost:.2f}" if cost else ""
            if title == "All time" and s == "anthropic" and cc:
                print(f"anthropic: {k(cc)} (Claude Code's own count; {k(full((s, n, i, o, cr, cost, cw)))} unique requests) | font=Menlo")
                continue
            print(f"{s}: {k((i or 0) + (o or 0) + (cr or 0) + (cw or 0))} ({n} req, in {k(i)}, out {k(o)}, cache r {k(cr)} w {k(cw)}){extra} | font=Menlo")
        if not rows:
            print("nothing yet")
        print("---")
    print("Top models today")
    for m, t in c.execute("SELECT model, SUM(input+output+cache_read+cache_write) FROM usage WHERE date(ts,'localtime')=date('now','localtime') "
                          "GROUP BY model ORDER BY 2 DESC LIMIT 5"):
        print(f"{m or '?'}: {k(t)} | font=Menlo")
    print("---")
    print("Refresh | refresh=true")


main()
