#!/opt/homebrew/bin/python3
# <xbar.title>tokenhub</xbar.title>
# <xbar.desc>Token usage and cost across every AI tool that goes through the local tokenhub proxy</xbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # the plugin is symlinked into SwiftBar's folder
import stats
from stats import k, money, pretty, dur

# SwiftBar greys out any line with no action and no color param. Data lines stay grey (MONO); lines that open a submenu get a color (PLAIN, MENU) so they read as clickable.
INK = "color=#1d1d1f,#f5f5f7"
MONO = "| font=Menlo"
MENU = f"| font=Menlo {INK}"
PLAIN = f"| {INK}"


def c(x):
    return money(x) if x else "no cost data"
NAMES = {"anthropic": "Anthropic", "grok": "Grok", "openrouter": "OpenRouter", "ollama": "Ollama", "xai": "xAI",
         "chatgpt": "Codex (ChatGPT)", "openai": "OpenAI", "gemini": "Gemini"}


def sources(r):
    """[(name, tokens, cost)] for one period; the all-time total uses Claude Code's own count so the lines add up to the title."""
    extra = (r["tokens_cc"] - r["tokens"]) if r["tokens_cc"] else 0
    return [(NAMES.get(s, s), t + (extra if s == "anthropic" else 0), cost) for s, (t, cost) in r["by_source"].items()]


def period_menu(r, title):
    """Menu lines for one period; `--` prefixes nest items as SwiftBar submenus."""
    if not r or not r["requests"]:
        return [f"{title} {PLAIN}", f"--no usage in this period {PLAIN}"]
    mad = r["most_active_day"]
    tok = r["tokens_cc"] or r["tokens"]
    out = [f"{title}: {k(tok)} tokens, {money(r['cost'])} {PLAIN}",
           f"--Total tokens: {k(tok)} {MONO}",
           f"--Input {k(r['input'])} · Output {k(r['output'])} · Cache read {k(r['cache_read'])} · Cache write {k(r['cache_write'])} {MONO}",
           "-----",
           f"--By source {PLAIN}"]
    for name, t, cost in sources(r):
        out.append(f"----{name}: {k(t)} · {c(cost)} {MONO}")
    out += [f"--Models {PLAIN}"]
    for m in r["models"][:12]:
        out.append(f"----{pretty(m['model'])} {m['share']:.1%} · {c(m['cost'])} {MENU}")
        out.append(f"------In {k(m['input'])} · Out {k(m['output'])} · Cache {k(m['cache_read'])} read / {k(m['cache_write'])} write {MONO}")
    if len(r["models"]) > 12:
        out.append(f"----+{len(r['models']) - 12} more (run stats.py) {MONO}")
    out += ["-----",
            f"--Favorite model: {pretty(r['favorite'])} {MONO}",
            f"--Sessions: {r['sessions']:,} · Longest: {dur(r['longest_session'])} {MONO}",
            f"--Active days: {r['active_days']}/{r['span_days']} {MONO}",
            f"--Longest streak: {r['longest_streak']} days · Current: {r['current_streak']} days {MONO}",
            f"--Most active day: {mad:%b} {mad.day} {MONO}" if mad else f"--Most active day: - {PLAIN}",
            "-----",
            f"--Cost {money(r['cost'])} {PLAIN}",
            f"----Provider-reported: {money(r['cost_reported'])} {MONO}",
            f"----Estimated at list price: {money(r['cost_est'])} (for plans that report no cost) {MONO}",
            f"----Avg {money(r['avg_cost_day'])}/active day · {money(r['avg_cost_session'])}/session · {money(r['avg_cost_request'])}/request {MONO}"]
    if r["tokens_cc"]:
        out.append(f"--Note: includes Claude Code's replayed-history count; {k(r['tokens'])} tokens are unique requests {MONO}")
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
    for name, t, cost in sources(allr):
        print(f"{name}: {k(t)} · {c(cost)} {MONO}")
    print("---")
    for r, title in ((allr, "All time"), (stats.compute(period="30d"), "Last 30 days"), (stats.compute(period="7d"), "Last 7 days")):
        print("\n".join(period_menu(r, title)))
    print(f"Activity {PLAIN}")
    for i, row in enumerate(stats.heatmap(allr["daily"], allr["today"], weeks=26)):
        print(f"--{('Mon', '', 'Wed', '', 'Fri', '', '')[i].ljust(4)}{row} | font=Menlo ansi=true")
    print("---")
    print("Refresh | refresh=true")


main()
