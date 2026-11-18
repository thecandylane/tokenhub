#!/opt/homebrew/bin/python3
# <xbar.title>tokenhub</xbar.title>
# <xbar.desc>Token usage and cost across Claude Code, Grok Build, omp, OpenRouter and Ollama</xbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # the plugin is symlinked into SwiftBar's folder
import stats
from stats import k, money, pretty, dur

MONO = "| font=Menlo"


def period_menu(r, title):
    """Menu lines for one period; `--` prefixes nest items as SwiftBar submenus."""
    if not r or not r["requests"]:
        return [f"{title} | size=12", "--no usage in this period"]
    mad = r["most_active_day"]
    tok = r["tokens_cc"] or r["tokens"]
    out = [f"{title}: {k(tok)} tokens, {money(r['cost'])}",
           f"--Total tokens: {k(tok)}" + (f" (Claude Code's own count; {k(r['tokens'])} unique)" if r["tokens_cc"] else "") + f" {MONO}",
           f"--Input {k(r['input'])} · Output {k(r['output'])} · Cache read {k(r['cache_read'])} · Cache write {k(r['cache_write'])} {MONO}",
           "-----",
           f"--Favorite model: {pretty(r['favorite'])} {MONO}",
           f"--Sessions: {r['sessions']:,} · Longest: {dur(r['longest_session'])} {MONO}",
           f"--Active days: {r['active_days']}/{r['span_days']} {MONO}",
           f"--Longest streak: {r['longest_streak']} days · Current: {r['current_streak']} days {MONO}",
           f"--Most active day: {mad:%b} {mad.day} {MONO}" if mad else "--Most active day: -",
           "-----",
           f"--Cost {money(r['cost'])}",
           f"----Reported by APIs (OpenRouter, Grok): {money(r['cost_reported'])} {MONO}",
           f"----Claude list-price estimate: {money(r['cost_est'])} (subscription is flat) {MONO}",
           f"----Avg {money(r['avg_cost_day'])}/active day · {money(r['avg_cost_session'])}/session · {money(r['avg_cost_request'])}/request {MONO}",
           "-----",
           "--By source"]
    for src, (t, cost) in r["by_source"].items():
        out.append(f"----{src}: {k(t)} · {money(cost)} {MONO}")
    out.append("--Models")
    for m in r["models"][:12]:
        out.append(f"----{pretty(m['model'])} {m['share']:.1%} · {money(m['cost'])} {MONO}")
        out.append(f"------In {k(m['input'])} · Out {k(m['output'])} · Cache {k(m['cache_read'])} read / {k(m['cache_write'])} write {MONO}")
    if len(r["models"]) > 12:
        out.append(f"----+{len(r['models']) - 12} more (run stats.py) {MONO}")
    return out


def main():
    allr = stats.compute(period="all")
    if allr is None:
        print("tokens: -\n---\nno usage yet; start hub.py")
        return
    today = allr["daily"].get(allr["today"], 0)
    print(f"tokens {k(allr['tokens_cc'] or allr['tokens'])}")
    print("---")
    print(f"Today: {k(today)} tokens · 30 days {stats.spark(allr['daily'], allr['today'])} {MONO}")
    print("---")
    for r, title in ((allr, "All time"), (stats.compute(period="30d"), "Last 30 days"), (stats.compute(period="7d"), "Last 7 days")):
        print("\n".join(period_menu(r, title)))
    print("Activity")
    for i, row in enumerate(stats.heatmap(allr["daily"], allr["today"], weeks=26)):
        print(f"--{('Mon', '', 'Wed', '', 'Fri', '', '')[i].ljust(4)}{row} | font=Menlo ansi=true")
    print("---")
    print("Refresh | refresh=true")


main()
