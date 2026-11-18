"""Import past usage into the tokenhub DB from local session logs:
Claude Code (~/.claude/projects/**/*.jsonl), omp (~/.omp/agent/sessions) and Grok Build (~/.grok/sessions/*/usage.json).

Claude: one row per unique requestId (resumed sessions replay old lines, so raw sums overcount).
Nothing newer than the first live hub row is imported, so live traffic is never counted twice.
Safe to rerun: rows already imported are skipped; --rebuild deletes all imported rows first (use after pricing or logic changes).
Usage: python3 backfill.py [--dry-run] [--rebuild]"""
import glob
import json
import os
import sqlite3
import sys

import hub
from prices import estimate

LOGS = os.path.expanduser("~/.claude/projects/**/*.jsonl")
OMP = os.path.expanduser("~/.omp/agent/sessions/**/*.jsonl")
GROK = os.path.expanduser("~/.grok/sessions/*/*/usage.json")


def collect():
    last = {}  # requestId -> (ts, model, usage, session); the last line wins, usage grows while streaming
    for f in glob.glob(LOGS, recursive=True):
        for line in open(f, errors="replace"):
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message")
            if not isinstance(m, dict) or not isinstance(m.get("usage"), dict) or m.get("model") == "<synthetic>":
                continue
            k = d.get("requestId") or m.get("id")
            if k and d.get("timestamp"):
                last[k] = (d["timestamp"], m.get("model"), m["usage"], d.get("sessionId"))
    return last


def num_of(d, k):
    return d[k] if isinstance(d.get(k), (int, float)) else 0


def ts_of(s):
    return s[:19].replace("T", " ")


def omp_rows():
    """(key, ts, source, model, session, in, out, cache_read, cache_write, reasoning, cost)"""
    for f in glob.glob(OMP, recursive=True):
        for line in open(f, errors="replace"):
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message")
            u = m.get("usage") if isinstance(m, dict) else None
            if isinstance(u, dict) and d.get("id") and d.get("timestamp"):
                cost = (u.get("cost") or {}).get("total")
                yield (f"omp/{d['id']}", ts_of(d["timestamp"]), m.get("provider") or "omp", m.get("model"), os.path.basename(f),
                       u.get("input") or 0, u.get("output") or 0, u.get("cacheRead") or 0, u.get("cacheWrite") or 0,
                       u.get("reasoningTokens") or 0, cost)


def grok_rows():
    for f in glob.glob(GROK):
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        for t in d.get("turns") or []:
            for model, u in (t.get("modelUsage") or {}).items():
                cr = u.get("cachedReadTokens") or 0  # Grok inputTokens includes cached reads
                ticks = u.get("costUsdTicks")
                yield (f"grok/{d.get('sessionId')}/{t.get('turnNumber')}/{model}", ts_of(t.get("endedAt") or d["updatedAt"]), "grok",
                       model, d.get("sessionId"), max((u.get("inputTokens") or 0) - cr, 0), u.get("outputTokens") or 0, cr,
                       u.get("cacheCreationTokens") or 0, u.get("reasoningTokens") or 0, ticks / 1e10 if ticks else None)


def main():
    last = collect()
    hub.init_db()
    with sqlite3.connect(hub.DB, timeout=10) as c:
        if "--rebuild" in sys.argv and "--dry-run" not in sys.argv:
            print("rebuild: deleted", c.execute("DELETE FROM usage WHERE client='backfill'").rowcount, "imported rows")
        have = {r[0] for r in c.execute("SELECT path FROM usage WHERE client='backfill'")}
        live = c.execute("SELECT MIN(ts) FROM usage WHERE client!='backfill'").fetchone()[0] or "9999"
        rows = []
        for k, (ts, model, u, sess) in last.items():
            if f"/backfill/{k}" in have or ts_of(ts) >= live:
                continue
            num = lambda key: u[key] if isinstance(u.get(key), (int, float)) else 0
            cd = u.get("output_tokens_details") or {}
            cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
            w1 = min(num_of(cc, "ephemeral_1h_input_tokens"), num("cache_creation_input_tokens"))  # no breakdown means 5m
            est = estimate(model, num("input_tokens"), num("output_tokens"), num("cache_read_input_tokens"),
                           num("cache_creation_input_tokens") - w1, w1)
            rows.append((ts_of(ts), "anthropic", model, 200, 1, num("input_tokens"), num("output_tokens"),
                         num("cache_read_input_tokens"), num("cache_creation_input_tokens"),
                         cd.get("thinking_tokens") or 0, None, "backfill", sess, f"/backfill/{k}", 0, est))
        for k, ts, src, model, sess, i, o, cr, cw, rs, cost in [*omp_rows(), *grok_rows()]:
            if f"/backfill/{k}" not in have and ts < live:
                rows.append((ts, src, model, 200, 1, i, o, cr, cw, rs, cost, "backfill", sess, f"/backfill/{k}", 0, None))
        by = {}
        for r in rows:
            b = by.setdefault(r[1], [0, 0, 0, 0, 0])
            b[0] += 1
            for j, i in enumerate((5, 6, 7, 8), 1):
                b[j] += r[i]
        for src, b in sorted(by.items()):
            print(f"{src}: {b[0]:,} new rows; in {b[1]:,} out {b[2]:,} cache_read {b[3]:,} cache_write {b[4]:,}")
        print(f"({len(have)} rows already imported)")
        if "--dry-run" not in sys.argv:
            c.executemany("INSERT INTO usage(ts,source,model,status,stream,input,output,cache_read,cache_write,reasoning,"
                          "cost_usd,client,session,path,ms,est_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            print("imported")


if __name__ == "__main__":
    main()
