"""SegSmart local dashboard server (stdlib only — no cloud, no deps).

GET  /              -> the dashboard
GET  /api/result    -> cached out/result.json (instant)
POST /api/run       -> run the pipeline live; optional CSV upload + column map

Run:  python3 server.py     then open http://localhost:8099
Everything — data, models, AI campaign copy — stays on this machine.
"""
import csv, io, json, os, tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pipeline
from seg.mapping import infer_mapping
from seg.util import NoValidData

PORT = int(os.environ.get("SEG_PORT", "8099"))
# bind localhost by default (data-handling tool); set SEG_HOST=0.0.0.0 in Docker
HOST = os.environ.get("SEG_HOST", "127.0.0.1")
MAX_UPLOAD = int(os.environ.get("SEG_MAX_UPLOAD_MB", "64")) * 1024 * 1024
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
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_UPLOAD:
            return self._send(413, json.dumps(
                {"error": f"upload too large (>{MAX_UPLOAD // (1024*1024)} MB)"}))
        try:
            req = json.loads(self.rfile.read(n) or "{}")
        except json.JSONDecodeError:
            return self._send(400, json.dumps({"error": "invalid JSON body"}))

        # --- onboarding: propose a column mapping for an uploaded CSV ---
        if self.path == "/api/infer_mapping":
            try:
                rows = list(csv.reader(io.StringIO(req.get("csv_text", ""))))
                if not rows:
                    return self._send(400, json.dumps({"error": "empty CSV"}))
                header, samples = rows[0], rows[1:6]
                out = infer_mapping(header, samples, use_llm=req.get("use_llm", True))
                out["header"] = header
                out["preview"] = samples
                return self._send(200, json.dumps(out, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))

        if self.path != "/api/run":
            return self._send(404, json.dumps({"error": "not found"}))
        use_llm = req.get("use_llm", True)
        currency = req.get("currency", "£")
        path = None
        try:
            if req.get("csv_text"):
                with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tf:
                    tf.write(req["csv_text"]); path = tf.name
                # out=None: uploaded customer data is NOT persisted to the shared file
                res = pipeline.run(source="csv", path=path, currency=currency,
                                   use_llm=use_llm, mapping=req.get("mapping"),
                                   decimal=req.get("decimal", "."), lang=req.get("language"),
                                   out=None)
            else:
                res = pipeline.run(source="uci", currency=currency, use_llm=use_llm, out=None)
            return self._send(200, json.dumps(res, ensure_ascii=False))
        except NoValidData as e:
            return self._send(400, json.dumps({"error": str(e)}))
        except Exception as e:
            return self._send(500, json.dumps({"error": str(e)}))
        finally:
            if path and os.path.exists(path):
                os.unlink(path)                       # never leave uploaded CSV on disk


if __name__ == "__main__":
    print(f"SegSmart dashboard → http://{HOST}:{PORT}  (Ctrl-C to stop)")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
