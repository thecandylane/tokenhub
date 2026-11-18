
<img width="1201" height="472" alt="Screenshot 2026-11-18 at 3 27 46 PM" src="https://github.com/user-attachments/assets/c64c9831-e6df-422c-bcd2-eefa5e8c91a2" />

-Since August 2026 Macbook Pro M1 Max


# tokenhub

Local proxy on `127.0.0.1:4000` that logs token usage to `~/.tokenhub/usage.db` (SQLite). Headers and bodies pass through untouched, so it holds no keys: your Claude subscription login, OpenRouter key and so on still go straight upstream.

| Route | Upstream |
|---|---|
| `/anthropic` | api.anthropic.com |
| `/openrouter` | openrouter.ai |
| `/xai` | api.x.ai (untested, no API key) |
| `/grok` | cli-chat-proxy.grok.com (Grok Build subscription login) |
| `/ollama` | 127.0.0.1:11434 |
| `/openai` | api.openai.com (clients use `/openai/v1`) |
| `/chatgpt` | chatgpt.com/backend-api/codex (Codex with a ChatGPT login) |
| `/gemini` | generativelanguage.googleapis.com |

Add or replace routes for any other endpoint in `~/.tokenhub/routes.json`: `{"deepseek": "https://api.deepseek.com/v1"}` makes `/deepseek` forward there. Names are lowercase letters, digits and dashes.

Grok Build is routed with `GROK_MODELS_BASE_URL`; it reports a cost per request. Grok limits and reset times (not per-request tokens) still come from CodexBar.

## Run

    python3 hub.py              # TOKENHUB_PORT, TOKENHUB_DB override defaults
    python3 test_hub.py         # self-check, no network

Start at login: copy `launchd.plist.example` to `~/Library/LaunchAgents/dev.tokenhub.plist`, replace the two placeholder paths, then `launchctl load` it.

## Supported tools

| Tool | Live logging | History import |
|---|---|---|
| Claude Code | `claude` wrapper sets `ANTHROPIC_BASE_URL` (`/anthropic`) | yes |
| Codex CLI | `codex` wrapper uses a custom provider on `/chatgpt` with WebSockets off (tested with a ChatGPT login) | yes |
| Grok Build | `grok` wrapper sets `GROK_MODELS_BASE_URL` (`/grok/v1`) | yes |
| omp | `omp-models.yml.example` | yes |
| pi | `~/.pi/agent/models.json`: set `providers.<name>.baseUrl` (per the pi docs; untested here) | yes, same format as omp (untested) |
| Gemini CLI | `GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:4000/gemini` with an API key (per the Gemini CLI docs; untested) | no |
| OpenCode | provider `settings.baseURL` (per the OpenCode docs; untested) | no |
| Aider | `OPENAI_API_BASE=http://127.0.0.1:4000/openai/v1` (per the Aider docs; untested) | no |
| Zed | `language_models.<provider>.api_url` in settings (per the Zed docs; untested) | no |
| Cursor | not possible: Cursor sends requests through its own servers, so a local proxy never sees them | no |

Anything OpenAI-compatible can use a `routes.json` entry. The hub reads OpenAI chat and responses, Anthropic, Gemini and Ollama usage formats.

## Point clients at it

Claude Code, Codex and Grok Build: `tokenhub.zsh` (sourced from `~/.zshrc`) defines `claude`, `codex` and `grok` functions that use the hub when `/_health` answers and run untouched when it does not. By hand:

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

`python3 backfill.py` (add `--dry-run` to preview) imports past usage from local Claude Code, Codex, omp/pi and Grok Build logs. Codex reports no cost, so its cost shows as "no cost data". The menu bar plugin shows Claude Code's own all-time figure from `~/.claude/stats-cache.json` next to the unique-request count.

## Stats

`python3 stats.py [all|30d|7d]` prints a Claude Code /stats-style report over every source: activity heatmap, favorite model, sessions, longest session, active days, streaks, most active day, token split, per-model share, and cost. The menu bar plugin shows the same under All time, Last 30 days and Last 7 days.

- Every source is counted the same way, from the hub's DB. The one exception: Claude Code's past sessions can't be rebuilt from its logs, so sessions and active days also read `~/.claude/stats-cache.json`.
- Cost = what providers report (OpenRouter, Grok), plus a list-price estimate for usage that reports no cost (Anthropic models, `prices.py`). On a subscription plan that estimate is API-equivalent value, not what you pay. Run `python3 backfill.py --rebuild` after changing prices.

## Sharing

Each person runs their own hub; nothing leaves your machine and no keys are stored. macOS and Python 3.9+, stdlib only.
