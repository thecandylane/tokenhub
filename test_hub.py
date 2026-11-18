"""Self-check: fake upstreams, real hub. Run: python3 test_hub.py"""
import http.client
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import hub

SECRET = "sk-ant-oat-SECRETSECRET"
seen = []  # (path, headers dict) received by fake upstreams


def sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        name = self.server.name
        if name == "anthropic":
            if self.path.startswith("/err"):
                return self._send(401, "application/json", b'{"error":{"message":"bad key"}}')
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for part, pause in ((sse({"type": "message_start", "message": {"model": "claude-x", "usage": {
                    "input_tokens": 10, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 3, "output_tokens": 1}}}), 0.6),
                    (sse({"type": "ping"}, {"type": "message_delta", "usage": {"output_tokens": 42}}, {"type": "message_stop"}), 0)):
                self.wfile.write(part)
                self.wfile.flush()
                time.sleep(pause)
        elif name == "openrouter":
            if b'"stream":true' in body.replace(b" ", b""):
                data = (b'data: {"model":"m/or","choices":[{"delta":{"content":"hi"}}]}\n\n'
                        b'data: {"model":"m/or","choices":[],"usage":{"prompt_tokens":8,"completion_tokens":4,"cost":0.5}}\n\n'
                        b'data: [DONE]\n\n')
                self._send(200, "text/event-stream", data)
            else:
                u = {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0123,
                     "prompt_tokens_details": {"cached_tokens": 30}, "completion_tokens_details": {"reasoning_tokens": 5}}
                self._send(200, "application/json", json.dumps({"model": "m/or", "usage": u}).encode())
        elif name == "xai":
            self._send(200, "application/json", json.dumps({"model": "grok-x", "usage": {
                "prompt_tokens": 7, "completion_tokens": 3, "cost_in_usd_ticks": 25_000_000}}).encode())
        elif name == "ollama":
            lines = [{"model": "qwen", "response": "a", "done": False},
                     {"model": "qwen", "response": "", "done": True, "prompt_eval_count": 26, "eval_count": 259}]
            self._send(200, "application/x-ndjson", "".join(json.dumps(l) + "\n" for l in lines).encode())

    def _send(self, code, ctype, data):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def start(srv):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def fake(name):
    s = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    s.name = name
    return start(s)


def call(port, path, body=b"{}", headers=None, host=None, chunked=False):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Content-Type": "application/json", "User-Agent": "test-client/1.0 extra", **(headers or {})}
    if chunked:
        c.request("POST", path, body=iter([body[:3], body[3:]]), headers=h, encode_chunked=True)
    else:
        c.putrequest("POST", path, skip_host=True)
        c.putheader("Host", host or f"127.0.0.1:{port}")
        for k, v in {**h, "Content-Length": str(len(body))}.items():
            c.putheader(k, v)
        c.endheaders(body)
    return c, c.getresponse()


def rows():
    with sqlite3.connect(hub.DB) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM usage ORDER BY id")]


def main():
    hub.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    fakes = {n: fake(n) for n in ("anthropic", "openrouter", "xai", "ollama")}
    hub.ROUTES = {n: ("http", "127.0.0.1", s.server_address[1], "") for n, s in fakes.items()}
    hub_srv = start(hub.serve(0))
    port = hub_srv.server_address[1]

    # anthropic SSE: streamed unbuffered, headers verbatim, query kept, usage merged across events
    beta = "claude-code-20250219,oauth-2025-04-20,context-management-2025-06-27"
    t0 = time.monotonic()
    c, r = call(port, "/anthropic/v1/messages?beta=true", b'{"model":"claude-x","stream":true}',
                {"anthropic-beta": beta, "Authorization": f"Bearer {SECRET}", "anthropic-version": "2023-06-01",
                 "Accept-Encoding": "gzip", "x-claude-code-session-id": "sess-1"})
    first = r.read1(65536)
    t_first = time.monotonic() - t0
    rest = r.read()
    t_all = time.monotonic() - t0
    assert b"message_start" in first and t_first < 0.45 < 0.6 <= t_all, (t_first, t_all)
    assert b"message_stop" in rest and b"ping" in rest
    path, h, _ = seen[-1]
    assert path == "/v1/messages?beta=true", path
    assert h["anthropic-beta"] == beta and h["authorization"] == f"Bearer {SECRET}" and h["anthropic-version"] == "2023-06-01"
    assert h["accept-encoding"] == "identity" and h["host"].startswith("127.0.0.1:")
    time.sleep(0.2)
    a = rows()[-1]
    assert (a["source"], a["model"], a["input"], a["output"], a["cache_read"], a["cache_write"], a["stream"]) == \
        ("anthropic", "claude-x", 10, 42, 5, 3, 1), a
    assert a["client"] == "test-client/1.0" and a["session"] == "sess-1" and a["path"] == "/v1/messages"

    # upstream error passes through unchanged and is logged
    c, r = call(port, "/anthropic/err", b'{"model":"claude-x"}')
    assert r.status == 401 and json.loads(r.read())["error"]["message"] == "bad key"
    time.sleep(0.2)
    assert rows()[-1]["status"] == 401 and rows()[-1]["output"] == 0

    # openrouter: cached split out, cost + reasoning kept; SSE last-chunk usage
    c, r = call(port, "/openrouter/api/v1/chat/completions", b'{"model":"m/or"}')
    assert json.loads(r.read())["usage"]["cost"] == 0.0123
    c, r = call(port, "/openrouter/api/v1/chat/completions", b'{"model":"m/or","stream":true}')
    assert b"[DONE]" in r.read()
    time.sleep(0.2)
    o1, o2 = rows()[-2], rows()[-1]
    assert (o1["input"], o1["cache_read"], o1["output"], o1["reasoning"], o1["cost_usd"]) == (70, 30, 20, 5, 0.0123), o1
    assert (o2["input"], o2["output"], o2["cost_usd"], o2["stream"]) == (8, 4, 0.5, 1), o2

    # xai ticks -> USD, ollama NDJSON final chunk, chunked request body
    c, r = call(port, "/xai/v1/chat/completions", b'{"model":"grok-x"}')
    r.read()
    c, r = call(port, "/ollama/api/generate", b'{"model":"qwen"}', chunked=True)
    r.read()
    assert json.loads(seen[-1][2]) == {"model": "qwen"} and "content-length" in seen[-1][1]
    time.sleep(0.2)
    x, ol = rows()[-2], rows()[-1]
    assert x["cost_usd"] == 0.0025 and (x["input"], x["output"]) == (7, 3), x
    assert (ol["source"], ol["input"], ol["output"], ol["model"], ol["stream"]) == ("ollama", 26, 259, "qwen", 1), ol

    # guards
    c, r = call(port, "/anthropic/v1/messages", host="evil.example")
    assert r.status == 403
    c, r = call(port, "/nope/x")
    assert r.status == 404
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request("GET", "/_health")
    assert c.getresponse().read() == b"ok"

    # browsers, bad framing, odd bodies, hostile model names
    c, r = call(port, "/ollama/api/chat", headers={"Origin": "https://evil.example"})
    assert r.status == 403
    c, r = call(port, "/ollama/api/chat", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status == 403
    for bad in (b"Content-Length: -5", b"Content-Length: abc", b"Transfer-Encoding: chunked\r\n\r\n-1\r\n"):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sk:
            sk.sendall(b"POST /ollama/x HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n%s\r\n\r\n" % (port, bad))
            assert sk.recv(100).startswith(b"HTTP/1.0 400"), bad
    n0 = len(rows())
    c, r = call(port, "/ollama/api/generate", b"[1,2]")
    r.read()
    c, r = call(port, "/anthropic/err", b'{"model":"a|b c\\nx"}')
    r.read()
    time.sleep(0.2)
    assert len(rows()) == n0 + 2 and rows()[-1]["model"] == "abcx", rows()[-1]  # array body logged, no crash

    # upstream dies mid-stream: client sees an error, not a clean end; row says 502
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead.listen(1)

    def die():
        s, _ = dead.accept()
        s.recv(65536)
        s.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n")
        time.sleep(0.2)
        s.close()
    threading.Thread(target=die, daemon=True).start()
    hub.ROUTES["dead"] = ("http", "127.0.0.1", dead.getsockname()[1], "")
    try:
        c, r = call(port, "/dead/v1/messages", b'{"stream":true}')
        r.read()
        raise AssertionError("truncated stream looked clean")
    except (ConnectionError, http.client.HTTPException):
        pass
    time.sleep(0.3)
    assert rows()[-1]["status"] == 502 and rows()[-1]["source"] == "dead", rows()[-1]

    # accounting edge cases
    ex = lambda src, *objs: hub.extract(src, list(objs))
    assert ex("openrouter", {"model": "m", "usage": {"prompt_tokens": 194, "completion_tokens": 2, "prompt_tokens_details": {
        "cached_tokens": 0, "cache_write_tokens": 100}}}) == [("m", (94, 2, 0, 100, 0, None))]
    assert ex("xai", {"model": "g", "usage": {"prompt_tokens": 32, "completion_tokens": 9, "total_tokens": 151, "completion_tokens_details": {
        "reasoning_tokens": 110}, "prompt_tokens_details": {"cached_tokens": 8}}}) == [("g", (24, 119, 8, 0, 110, None))]
    assert ex("anthropic", {"type": "message", "model": "A", "usage": {"input_tokens": 1, "output_tokens": 2, "iterations": [
        {"type": "message", "input_tokens": 1, "output_tokens": 2}]}}) == [("A", (1, 2, 0, 0, 0, None))]  # plain: top-level wins
    assert sorted(ex("anthropic", {"model": "A", "usage": {"input_tokens": 412, "output_tokens": 264, "iterations": [
        {"type": "message", "model": "B", "input_tokens": 535, "output_tokens": 90},
        {"type": "fallback_message", "model": "A", "input_tokens": 412, "output_tokens": 264}]}})) == \
        [("A", (412, 264, 0, 0, 0, None)), ("B", (535, 90, 0, 0, 0, None))]
    assert ex("anthropic", {"model": "A", "usage": {"input_tokens": 0, "output_tokens": 0, "iterations": [
        {"type": "compaction", "input_tokens": 144, "output_tokens": 276}]}}) == [("A", (144, 276, 0, 0, 0, None))]

    # a credential never reaches disk (main file and WAL)
    for suffix in ("", "-wal"):
        with open(hub.DB + suffix, "rb") as f:
            assert SECRET.encode() not in f.read(), suffix
    print("ok", len(rows()), "rows")


if __name__ == "__main__":
    main()
