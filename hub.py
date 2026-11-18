"""tokenhub: local passthrough proxy that logs LLM token usage to SQLite.

/anthropic, /openrouter, /xai, /grok and /ollama forward to their upstream. Headers and bodies pass
through untouched, so the hub never holds or stores a credential."""
import http.client
import json
import os
import re
import socket
import sqlite3
import struct
import sys
import time
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get("TOKENHUB_DB", os.path.expanduser("~/.tokenhub/usage.db"))
# prefix -> (scheme, host, port, upstream path prefix)
ROUTES = {
    "anthropic": ("https", "api.anthropic.com", 443, ""),
    "openrouter": ("https", "openrouter.ai", 443, ""),
    "xai": ("https", "api.x.ai", 443, ""),
    "grok": ("https", "cli-chat-proxy.grok.com", 443, ""),  # Grok Build subscription login; host read from the grok binary
    "ollama": ("http", "127.0.0.1", 11434, ""),
}
HOP = {"connection", "keep-alive", "proxy-connection", "proxy-authenticate", "proxy-authorization",
       "te", "trailer", "transfer-encoding", "upgrade"}
SKIP_REQ = HOP | {"host", "content-length", "accept-encoding"}
MAX_CAPTURE = 16 << 20  # ponytail: usage lost if a response exceeds this; raise or tail-capture
MAX_BODY = 128 << 20  # request bodies; raise if you proxy large file uploads
TIMEOUT = 600  # long thinking pauses mean long silences between chunks

SCHEMA = """CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY, ts TEXT, source TEXT, model TEXT, status INT, stream INT,
  input INT, output INT, cache_read INT, cache_write INT, reasoning INT, cost_usd REAL,
  client TEXT, session TEXT, path TEXT, ms INT)"""


def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB)), mode=0o700, exist_ok=True)
    with sqlite3.connect(DB) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute(SCHEMA)


def save(row):
    with sqlite3.connect(DB, timeout=10) as c:
        c.execute("INSERT INTO usage(ts,source,model,status,stream,input,output,cache_read,cache_write,"
                  "reasoning,cost_usd,client,session,path,ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)


def parse(ctype, body):
    """JSON objects found in a JSON, SSE or NDJSON body."""
    text = body.decode("utf-8", "replace")
    chunks = [l[5:].strip() for l in text.splitlines() if l.startswith("data:")] if "event-stream" in ctype else [text]
    objs = []
    for s in chunks:
        try:
            objs.append(json.loads(s))
        except ValueError:
            for line in s.splitlines():
                try:
                    objs.append(json.loads(line))
                except ValueError:
                    pass
    return [o for o in objs if isinstance(o, dict)]


def extract(source, objs):
    """[(model, (input, output, cache_read, cache_write, reasoning, cost) or None)], one entry per billed model."""
    u, model = {}, None
    for o in objs:
        msg, resp = (o.get(k) if isinstance(o.get(k), dict) else {} for k in ("message", "response"))  # Ollama's "response" is text
        model = o.get("model") or msg.get("model") or resp.get("model") or model
        for x in (o.get("usage"), msg.get("usage"), resp.get("usage")):
            if isinstance(x, dict):
                u.update({k: v for k, v in x.items() if v is not None})
        if "eval_count" in o or "prompt_eval_count" in o:  # Ollama native
            u.update(prompt_tokens=o.get("prompt_eval_count", 0), completion_tokens=o.get("eval_count", 0),
                     prompt_eval_cached_count=o.get("prompt_eval_cached_count") or 0)
    if not u:
        return [(model, None)]
    n = lambda *ks: next((u[k] for k in ks if isinstance(u.get(k), (int, float))), 0)
    num = lambda d, k: d[k] if isinstance(d.get(k), (int, float)) else 0
    its = [x for x in u.get("iterations") or [] if isinstance(x, dict)] if source == "anthropic" else []
    if any(x.get("type") != "message" for x in its) or len({x.get("model") for x in its if x.get("model")}) > 1:
        # compaction or fallback attempts are billed per entry but absent from the top-level totals
        by = {}
        for x in its:
            r = by.setdefault(x.get("model") or model, [0, 0, 0, 0])
            for i, k in enumerate(("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
                r[i] += num(x, k)
        return [(m, (*r, 0, None)) for m, r in by.items()]
    pd = u.get("prompt_tokens_details") or u.get("input_tokens_details") or {}
    cd = u.get("completion_tokens_details") or u.get("output_tokens_details") or {}
    reasoning = cd.get("reasoning_tokens") or cd.get("thinking_tokens") or 0
    if source == "anthropic" or "cache_read_input_tokens" in u or "cache_creation_input_tokens" in u:
        inp, cr, cw, out = n("input_tokens"), n("cache_read_input_tokens"), n("cache_creation_input_tokens"), n("output_tokens")
    else:  # OpenAI-style counts include cached tokens; store them apart
        cr = pd.get("cached_tokens") or u.get("prompt_eval_cached_count") or 0
        cw = pd.get("cache_write_tokens") or 0  # OpenRouter explicit-cache writes, also inside prompt_tokens
        p, out = n("prompt_tokens", "input_tokens"), n("completion_tokens", "output_tokens")
        if source in ("xai", "grok") and n("total_tokens") >= p + out + reasoning:
            out += reasoning  # xAI completion_tokens is final text only; the others include reasoning
        inp = max(p - cr - cw, 0)
    cost = u.get("cost")
    if cost is None and u.get("cost_in_usd_ticks") is not None:
        cost = u["cost_in_usd_ticks"] / 1e10  # xAI: 1e10 ticks per USD
    return [(model, (inp, out, cr, cw, reasoning, cost))]


class H(BaseHTTPRequestHandler):
    timeout = 60  # request read only; _handle clears it before the upstream phase

    def _body(self):
        """Request body; ValueError on bad framing or more than MAX_BODY bytes."""
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            out = bytearray()
            while True:
                n = int(self.rfile.readline(1024).split(b";")[0].strip() or b"0", 16)
                if n == 0:
                    while self.rfile.readline(1024).strip():
                        pass
                    return bytes(out)
                if n < 0 or len(out) + n > MAX_BODY:
                    raise ValueError("chunk size")
                out += self.rfile.read(n)
                self.rfile.readline(1024)
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 <= n <= MAX_BODY:
            raise ValueError("content length")
        return self.rfile.read(n) if n else b""

    def _reply(self, code, text):
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _abort(self):
        """RST the client so a truncated stream reads as an error, not a clean EOF."""
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            os.close(self.connection.detach())  # close() would wait for rfile/wfile; detach frees the fd now, sending RST
        except OSError:
            pass

    def _handle(self):
        t0 = time.monotonic()
        port = self.server.server_address[1]
        if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return self._reply(403, "bad Host")  # blocks DNS rebinding from web pages
        if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site", "none") not in ("none", "same-origin"):
            return self._reply(403, "browser request")  # blocks cross-origin web pages; CLIs and SDKs send neither
        if self.path == "/_health":
            return self._reply(200, "ok")
        _, prefix, rest = (self.path.split("/", 2) + ["", ""])[:3]
        route = ROUTES.get(prefix)
        if not route:
            return self._reply(404, "unknown route; use /" + ", /".join(ROUTES))
        scheme, host, uport, base = route
        try:
            body = self._body()
        except (ValueError, OverflowError):
            return self._reply(400, "bad request body")
        self.connection.settimeout(None)
        hdrs = {}
        for k, v in self.headers.items():
            if k.lower() not in SKIP_REQ:
                hdrs[k] = hdrs[k] + ("; " if k.lower() == "cookie" else ", ") + v if k in hdrs else v
        hdrs["Host"] = host if uport in (80, 443) else f"{host}:{uport}"
        hdrs["Accept-Encoding"] = "identity"  # plain bytes so usage can be read; local hop, no bandwidth cost
        if body or self.command in ("POST", "PUT", "PATCH"):
            hdrs["Content-Length"] = str(len(body))
        try:
            req_json = json.loads(body) if body else {}
        except ValueError:
            req_json = {}
        if not isinstance(req_json, dict):
            req_json = {}
        conn = (http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection)(host, uport, timeout=TIMEOUT)
        status, ctype, enc, cap, over, sent = 502, "", "", bytearray(), False, False
        try:
            try:
                conn.request(self.command, base + "/" + rest, body=body or None, headers=hdrs)
            except (BrokenPipeError, ConnectionResetError):
                pass  # upstream may have answered early (401, 413) and closed; its reply is next
            r = conn.getresponse()
            status, ctype = r.status, r.getheader("Content-Type", "")
            enc = r.getheader("Content-Encoding", "").lower()
            self.send_response_only(r.status)
            for k, v in r.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            sent = True
            while True:
                chunk = r.read1(65536)
                if not chunk:
                    break
                if len(cap) + len(chunk) <= MAX_CAPTURE:
                    cap += chunk
                else:
                    over = True
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except OSError:
                    break  # client left; still log what arrived. ponytail: a silent upstream keeps running until its next chunk
        except Exception as e:
            sys.stderr.write(f"upstream error {prefix}: {type(e).__name__}: {e}\n")
            if sent:  # headers already out: a second reply would corrupt the body
                status = 502
                self._abort()
            else:
                try:
                    self._reply(502, f"hub: upstream error: {type(e).__name__}")
                except OSError:
                    pass
        finally:
            conn.close()
        if self.command == "POST":
            try:
                self.connection.shutdown(socket.SHUT_WR)  # client sees EOF before the DB write
            except OSError:
                pass
            self._record(prefix, req_json, status, ctype, bytes(cap), enc, over, rest, int((time.monotonic() - t0) * 1000))

    def _record(self, source, req_json, status, ctype, cap, enc, over, rest, ms):
        try:
            if enc == "gzip":
                d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                cap = d.decompress(cap, MAX_CAPTURE)
                over = over or bool(d.unconsumed_tail)
            rows = extract(source, parse(ctype, cap))
        except Exception as e:
            sys.stderr.write(f"usage parse failed ({source}): {type(e).__name__}: {e}\n")
            rows = [(None, None)]
        stream = int("stream" in ctype or "ndjson" in ctype or bool(req_json.get("stream")))
        path = "/" + rest.split("?")[0]
        if rows[0][1] is None and status < 400 and not (stream and ("completions" in path or "responses" in path)):
            return  # no usage (count_tokens, embeddings, pulls); a streamed chat without include_usage still gets a row
        ua = (self.headers.get("User-Agent") or "").split(" ")[0][:60]
        for model, u in rows:
            model = model or req_json.get("model")
            model = re.sub(r"[^\w.:/@+-]", "", model)[:100] if isinstance(model, str) else None  # plugin renders it raw
            inp, out, cr, cw, rs, cost = u or (0, 0, 0, 0, 0, None)
            try:
                save((datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), source, model, status, stream,
                      inp, out, cr, cw, rs, cost, ua, self.headers.get("x-claude-code-session-id"), path, ms))
            except sqlite3.Error as e:
                sys.stderr.write(f"usage not saved: {e}\n")
        if over:
            sys.stderr.write("response over capture cap; usage may be missing\n")

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle

    def log_message(self, *a):
        pass


class Srv(ThreadingHTTPServer):
    request_queue_size = 128  # stdlib default is 5; macOS caps at 128
    daemon_threads = True


def serve(port):
    init_db()
    return Srv(("127.0.0.1", port), H)


if __name__ == "__main__":
    s = serve(int(os.environ.get("TOKENHUB_PORT", "4000")))
    print(f"tokenhub on 127.0.0.1:{s.server_address[1]} -> {DB}", flush=True)
    s.serve_forever()
