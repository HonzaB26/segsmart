"""SegSmart local dashboard server (stdlib only — no cloud, no deps).

GET  /                   -> the dashboard (results only)
GET  /setup              -> data setup: upload a file or configure a connector
GET  /api/result         -> cached out/result.json (instant)
GET  /api/config         -> the local config file (config/segsmart.json)
POST /api/config         -> save the config file
POST /api/infer_mapping  -> propose a column mapping for an uploaded file
POST /api/preview_source -> fetch a few rows from a configured source + mapping
POST /api/run            -> ad-hoc run on an uploaded file (NOT persisted)
POST /api/run_config     -> run from the saved config (persisted -> dashboard)
POST /api/refine_card    -> rewrite a campaign card with an owner-set discount
POST /api/launch         -> approved card -> mailing artifact in out/mailings/
                            (+ optional POST to config mailer.webhook_url)

Run:  python3 server.py     then open http://localhost:8099
Everything — data, config, models, AI campaign copy — stays on this machine.
"""
import base64, hmac, json, os, re, tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pipeline
from seg import config as cfgmod
from seg.mapping import infer_mapping
from seg.sniff import read_table
from seg.util import NoValidData

PORT = int(os.environ.get("SEG_PORT", "8099"))
# bind localhost by default (data-handling tool); set SEG_HOST=0.0.0.0 in Docker
HOST = os.environ.get("SEG_HOST", "127.0.0.1")
MAX_UPLOAD = int(os.environ.get("SEG_MAX_UPLOAD_MB", "64")) * 1024 * 1024
# SEG_AUTH="user:password" puts the WHOLE app behind HTTP Basic Auth — the
# results are customer revenue data, not just the config. Off by default
# (localhost bind); set it whenever the port is reachable beyond this machine.
# Basic auth is plaintext over plain HTTP: fine on a trusted LAN, put a
# TLS-terminating reverse proxy in front for anything more.
AUTH = os.environ.get("SEG_AUTH", "")
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

    def _authorized(self) -> bool:
        if not AUTH:
            return True
        got = self.headers.get("Authorization", "")
        if got.startswith("Basic "):
            try:
                supplied = base64.b64decode(got[6:]).decode()
            except Exception:
                return False
            return hmac.compare_digest(supplied.encode(), AUTH.encode())
        return False

    def _deny(self):
        body = json.dumps({"error": "authentication required"}).encode()
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="SegSmart"')
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._authorized():
            return self._deny()
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path in ("/setup", "/setup.html"):
            with open(os.path.join(HERE, "setup.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/api/config":
            try:
                return self._send(200, json.dumps(
                    {"path": cfgmod.CONFIG_PATH, "config": cfgmod.load_config()},
                    ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))
        if self.path == "/api/result":
            p = os.path.join(HERE, "out/result.json")
            if not os.path.exists(p):
                return self._send(404, json.dumps({"error": "no result yet — run the pipeline"}))
            with open(p, "rb") as f:
                return self._send(200, f.read())
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if not self._authorized():
            return self._deny()
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_UPLOAD:
            return self._send(413, json.dumps(
                {"error": f"upload too large (>{MAX_UPLOAD // (1024*1024)} MB)"}))
        try:
            req = json.loads(self.rfile.read(n) or "{}")
        except json.JSONDecodeError:
            return self._send(400, json.dumps({"error": "invalid JSON body"}))

        # uploaded file arrives as base64 bytes (file_b64 + filename) so odd
        # encodings and Excel survive the browser; csv_text kept for compat
        def _upload_bytes():
            if req.get("file_b64"):
                return base64.b64decode(req["file_b64"]), req.get("filename", "")
            if req.get("csv_text"):
                return req["csv_text"].encode("utf-8"), "upload.csv"
            return None, ""

        # --- onboarding: propose a column mapping for an uploaded file ---
        if self.path == "/api/infer_mapping":
            try:
                data, fname = _upload_bytes()
                if not data:
                    return self._send(400, json.dumps({"error": "empty upload"}))
                raw, info = read_table(data, filename=fname)
                header = list(raw.columns)
                samples = raw.head(5).astype(str).values.tolist()
                out = infer_mapping(header, samples, use_llm=req.get("use_llm", True),
                                    delimiter=info.get("delimiter"))
                out["header"] = header
                out["preview"] = samples
                out["sniff"] = info
                return self._send(200, json.dumps(out, ensure_ascii=False))
            except NoValidData as e:
                return self._send(400, json.dumps({"error": str(e)}))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))

        # --- rewrite a campaign card around an owner-specified discount ---
        if self.path == "/api/refine_card":
            try:
                from seg.campaigns import apply_discount
                card = req.get("card") or {}
                discount = req.get("discount") or {}
                code = str(discount.get("code") or "").strip()
                if code and not re.fullmatch(r"[\w-]{2,32}", code):
                    return self._send(400, json.dumps(
                        {"error": "discount code: letters/digits/dash only, 2-32 chars"}))
                out = apply_discount(card, discount, lang=req.get("language", "en"),
                                     currency=req.get("currency", "£"),
                                     use_llm=req.get("use_llm", True))
                return self._send(200, json.dumps(out, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))

        # --- launch: approved card -> mailing artifact (+ optional webhook) ---
        if self.path == "/api/launch":
            try:
                from seg.mailer import build_mailing, save_mailing, deliver
                card = req.get("card") or {}
                recipients = req.get("recipients") or []
                if not recipients:
                    return self._send(400, json.dumps(
                        {"error": "no recipients — run a segmentation first"}))
                mailing = build_mailing(card, recipients,
                                        lang=req.get("language", "en"),
                                        currency=req.get("currency", "£"))
                path = save_mailing(mailing)
                report = deliver(mailing, (cfgmod.load_config().get("mailer")))
                return self._send(200, json.dumps(
                    {"saved": path, "recipients": len(mailing["recipients"]),
                     "delivery": report, "mailing": mailing}, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))

        # --- save the local config file ---
        if self.path == "/api/config":
            cfg = req.get("config")
            if not isinstance(cfg, dict):
                return self._send(400, json.dumps({"error": "body must be {config: {...}}"}))
            try:
                p = cfgmod.save_config(cfg)
                return self._send(200, json.dumps({"saved": p}))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))

        # --- preview a configured source: a few rows + a proposed mapping ---
        if self.path == "/api/preview_source":
            try:
                # API-supplied source: file paths confined to data/ (no
                # arbitrary-file read via preview)
                raw = cfgmod.fetch_raw(req.get("source") or {}, trusted_paths=False)
                head = raw.head(5).astype(str)
                header = list(raw.columns)
                samples = head.values.tolist()
                out = infer_mapping(header, samples, use_llm=req.get("use_llm", True))
                out["header"] = header
                out["preview"] = samples
                out["total_rows"] = int(len(raw))
                return self._send(200, json.dumps(out, ensure_ascii=False))
            except (NoValidData, ValueError) as e:      # bad config / non-SELECT
                return self._send(400, json.dumps({"error": str(e)}))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))

        # --- run from config (this installation's own data -> persisted) ---
        if self.path == "/api/run_config":
            try:
                cfg = req.get("config")
                # config straight from the local disk is the user's own;
                # config in the request body is API input -> confined paths
                from_disk = cfg is None
                if from_disk:
                    cfg = cfgmod.load_config()
                # wizard upload being adopted as THE data source: store the
                # file locally so future runs (and hand edits) can point at it
                if req.get("file_b64"):
                    os.makedirs(os.path.join(HERE, "data/uploads"), exist_ok=True)
                    safe = re.sub(r"[^\w.\-]", "_", os.path.basename(
                        req.get("filename") or "upload.csv")) or "upload.csv"
                    dest = os.path.join("data/uploads", safe)
                    with open(os.path.join(HERE, dest), "wb") as f:
                        f.write(base64.b64decode(req["file_b64"]))
                    cfg.setdefault("source", {})
                    cfg["source"].update({"type": "file", "path": dest})
                if req.get("save", True):
                    cfgmod.save_config(cfg)
                res = pipeline.run_config(cfg, trusted_paths=from_disk)
                return self._send(200, json.dumps(res, ensure_ascii=False))
            except (NoValidData, ValueError) as e:      # bad config / non-SELECT
                return self._send(400, json.dumps({"error": str(e)}))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))

        if self.path != "/api/run":
            return self._send(404, json.dumps({"error": "not found"}))
        use_llm = req.get("use_llm", True)
        currency = req.get("currency", "£")
        path = None
        try:
            data, fname = _upload_bytes()
            if data:
                suffix = os.path.splitext(fname)[1] or ".csv"
                with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as tf:
                    tf.write(data); path = tf.name
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
    if HOST not in ("127.0.0.1", "localhost", "::1") and not AUTH:
        print("  ⚠ WARNING: binding a non-localhost address WITHOUT authentication.\n"
              "    Anyone who can reach this port sees customer revenue data and\n"
              "    can reconfigure the data source. Set SEG_AUTH=user:password\n"
              "    (in Docker the compose file maps the port to 127.0.0.1 by default).")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
