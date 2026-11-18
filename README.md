# tokenhub

Local proxy on `127.0.0.1:4000` that logs token usage to `~/.tokenhub/usage.db` (SQLite). Headers and bodies pass through untouched, so it holds no keys: your Claude subscription login, OpenRouter key and so on still go straight upstream.

| Route | Upstream |
|---|---|
| `/anthropic` | api.anthropic.com |
| `/openrouter` | openrouter.ai |
| `/xai` | api.x.ai (untested, no API key) |
| `/grok` | cli-chat-proxy.grok.com (Grok Build subscription login) |
| `/ollama` | 127.0.0.1:11434 |

Grok Build is routed with `GROK_MODELS_BASE_URL`; it reports a cost per request. Grok limits and reset times (not per-request tokens) still come from CodexBar.

## Run

    python3 hub.py              # TOKENHUB_PORT, TOKENHUB_DB override defaults
    python3 test_hub.py         # self-check, no network

Start at login: copy `launchd.plist.example` to `~/Library/LaunchAgents/dev.tokenhub.plist`, replace the two placeholder paths, then `launchctl load` it.

## Point clients at it

Claude Code and Grok Build: `tokenhub.zsh` (sourced from `~/.zshrc`) defines `claude` and `grok` functions that use the hub when `/_health` answers and run untouched when it does not. By hand:

    ANTHROPIC_BASE_URL=http://127.0.0.1:4000/anthropic claude
    GROK_MODELS_BASE_URL=http://127.0.0.1:4000/grok/v1 grok

Editor or desktop launches do not read `~/.zshrc`, so they bypass the hub.

omp: see `omp-models.yml.example` (copy into `~/.omp/agent/models.yml`). If the hub is down, those providers fail, so run it at login first.
For scripted runs use `omp -p ... < /dev/null`. omp loads the MCP servers from `~/.omp/agent/mcp.json` even with `--no-tools`; a small local model used them to write to a note store during testing, so never point a weak model at omp with write-capable MCP servers enabled.

Browser requests (an `Origin` or cross-site `Sec-Fetch-Site` header) are refused with 403; CLIs and SDKs send neither.

## Menu bar

Install SwiftBar, then symlink the plugin into its plugin folder:

    ln -s "$PWD/tokenhub.1m.py" "<SwiftBar plugin folder>/tokenhub.1m.py"

The title shows today's input+output tokens; the menu lists per-source and per-model totals. Cached tokens are listed apart because they dominate Claude Code traffic.

## Columns

`input` and `cache_read` never overlap: for OpenAI-style APIs the hub subtracts cached tokens from `prompt_tokens`. `cost_usd` is only filled when the upstream reports it (OpenRouter, xAI).

## Limits

- Responses over 16 MB lose their usage row.
- The hub forces `Accept-Encoding: identity` upstream so it can read usage.

## History import

`python3 backfill.py` (add `--dry-run` to preview) imports past usage from local Claude Code, omp and Grok Build logs. The menu bar plugin shows Claude Code's own all-time figure from `~/.claude/stats-cache.json` next to the unique-request count.

## Stats

`python3 stats.py [all|30d|7d]` prints a Claude Code /stats-style report over every source: activity heatmap, favorite model, sessions, longest session, active days, streaks, most active day, token split, per-model share, and cost. The menu bar plugin shows the same under All time, Last 30 days and Last 7 days.

- Sessions, active days, longest session and busiest day use Claude Code's own `~/.claude/stats-cache.json` for Claude Code history, so they match its Stats tab. Everything else comes from the hub's DB.
- Cost = what OpenRouter and Grok report, plus a list-price estimate for Anthropic models (`prices.py`, from Anthropic's pricing page). On a subscription that estimate is API-equivalent value, not what you pay. Run `python3 backfill.py --rebuild` after changing prices.

## Sharing

Each person runs their own hub; nothing leaves your machine and no keys are stored. macOS and Python 3.9+, stdlib only.
