"""Self-check for stats.py on a synthetic DB. Run: python3 test_stats.py"""
import os
import tempfile
from datetime import date, timedelta

import hub
import stats

today = date(2026, 11, 18)
cache = {"through": "2026-11-10", "longest": 90061.0, "tokens": 0,
         "days": {date(2026, 11, 1): 2, date(2026, 11, 2): 3}}

hub.DB = os.path.join(tempfile.mkdtemp(), "s.db")
hub.init_db()
# (days ago, source, model, input, output, cache_read, cache_write, cost, est, session)
for ago, src, model, i, o, cr, cw, cost, est, sess in [
    (0, "anthropic", "claude-opus-5-5", 10, 20, 1000, 100, None, 3.0, "a"),
    (1, "anthropic", "claude-opus-5-5", 10, 20, 1000, 100, None, 3.0, "a"),
    (2, "grok", "grok-4.7-build", 5, 5, 90, 0, 1.0, None, "g"),
    (6, "openrouter", "deepseek/x", 1, 1, 0, 0, 0.5, None, None),
]:
    d = today - timedelta(ago)
    hub.save((f"{d} 12:00:00", src, model, 200, 1, i, o, cr, cw, 0, cost, "t", sess, "/x", 0, est))

r = stats.compute(hub.DB, "all", today=today, cache=cache)
assert r["requests"] == 4 and r["tokens"] == 1130 + 1130 + 100 + 2, r["tokens"]
assert abs(r["cost"] - 7.5) < 1e-9 and r["favorite"] == "claude-opus-5-5"
assert r["active_days"] == 6 and r["longest_streak"] == 3 and r["current_streak"] == 3  # Nov 12-18 minus gaps; cache days add Nov 1-2
assert r["sessions"] == 1 + 1 + 5 + 0, r["sessions"]  # grok "g", Claude "a" is newer than the cache, plus the cache's 2+3 sessions
assert r["most_active_day"] in (date(2026, 11, 17), date(2026, 11, 18)) and r["longest_session"] == 90061.0
assert abs(r["avg_cost_day"] - 7.5 / 6) < 1e-9
assert stats.compute(hub.DB, "7d", today=today, cache=cache)["active_days"] == 4
assert stats.streaks({date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 4)}, date(2026, 1, 4)) == (2, 1)
assert stats.pretty("claude-opus-5-5") == "Opus 5.5" and stats.pretty("claude-haiku-4-5-20251001") == "Haiku 4.5"
assert stats.dur(1301941) == "15d 1h 39m"
assert len(stats.heatmap(r["daily"], today, weeks=4, color=False)) == 7
print("ok")
