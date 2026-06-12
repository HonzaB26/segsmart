"""SegSmart local dashboard server (stdlib only — no cloud, no deps).

GET  /              -> the dashboard
GET  /api/result    -> cached out/result.json (instant)
POST /api/run       -> run the pipeline live; optional CSV upload + column map

Run:  python3 server.py     then open http://localhost:8099
Everything — data, models, AI campaign copy — stays on this machine.
"""
import json, os, tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pipeline

PORT = int(os.environ.get("SEG_PORT", "8099"))
HERE = os.path.dirname(os.path.abspath(__file__))


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/api/result":
            p = os.path.join(HERE, "out/result.json")
            if not os.path.exists(p):
                return self._send(404, json.dumps({"error": "no result yet — run the pipeline"}))
            with open(p, "rb") as f:
                return self._send(200, f.read())
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path != "/api/run":
            return self._send(404, json.dumps({"error": "not found"}))
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or "{}")
        use_llm = req.get("use_llm", True)
        currency = req.get("currency", "£")
        try:
            if req.get("csv_text"):
                with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tf:
                    tf.write(req["csv_text"]); path = tf.name
                res = pipeline.run(source="csv", path=path, currency=currency,
                                   use_llm=use_llm, mapping=req.get("mapping"))
            else:
                res = pipeline.run(source="uci", currency=currency, use_llm=use_llm)
            return self._send(200, json.dumps(res, ensure_ascii=False))
        except Exception as e:
            return self._send(500, json.dumps({"error": str(e)}))


if __name__ == "__main__":
    print(f"SegSmart dashboard → http://localhost:{PORT}  (Ctrl-C to stop)")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
