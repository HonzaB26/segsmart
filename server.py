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
POST /api/external_impact-> score an uploaded daily external-factors CSV against
                            the current run's daily revenue (no customer data read)
POST /api/refine_card    -> rewrite a campaign card with an owner-set discount
POST /api/update_card    -> save a manager's text edits to a campaign card
                            (headline/body/objective/channel/offer/rationale) into result.json
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
        path = self.path.split("?", 1)[0]           # ignore query string
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path in ("/setup", "/setup.html"):
            with open(os.path.join(HERE, "setup.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path in ("/ad", "/ad.html"):
            with open(os.path.join(HERE, "ad.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path in ("/system-card", "/system-card.html"):
            with open(os.path.join(HERE, "system-card.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path in ("/ad-cs", "/ad.cs.html"):
            with open(os.path.join(HERE, "ad.cs.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/config":
            try:
                return self._send(200, json.dumps(
                    {"path": cfgmod.CONFIG_PATH, "config": cfgmod.load_config()},
                    ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))
        if path == "/api/result":
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
                cfg_now = cfgmod.load_config()
                mailing = build_mailing(card, recipients,
                                        lang=req.get("language", "en"),
                                        currency=req.get("currency", "£"),
                                        signature=(cfg_now.get("output") or {})
                                        .get("signature"))
                # explicit confirmation required — launch can hit a live mailer
                # webhook; one accidental click must never mail a whole segment.
                # The unconfirmed call returns the EXACT e-mail for review
                # (subject + body + counts), saving and delivering nothing.
                if req.get("confirm") is not True:
                    return self._send(200, json.dumps(
                        {"confirm_needed": True,
                         "subject": mailing["subject"],
                         "body_text": mailing["body_text"],
                         "recipients": len(mailing["recipients"]),
                         "deliverable": mailing["deliverable"],
                         "webhook_configured": bool(
                             (cfg_now.get("mailer") or {})
                             .get("webhook_url"))}, ensure_ascii=False))
                path = save_mailing(mailing)
                report = deliver(mailing, cfg_now.get("mailer"))
                return self._send(200, json.dumps(
                    {"saved": path, "recipients": len(mailing["recipients"]),
                     "deliverable": mailing["deliverable"],
                     "delivery": report, "mailing": mailing}, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))

        # --- score an uploaded external-factors CSV against the current run ---
        # privacy-preserving: joins the user's daily factors onto the persisted
        # daily REVENUE aggregate (no customer data needed or read here)
        if self.path == "/api/external_impact":
            try:
                data, fname = _upload_bytes()
                if not data:
                    return self._send(400, json.dumps({"error": "empty upload"}))
                p = os.path.join(HERE, "out/result.json")
                if not os.path.exists(p):
                    return self._send(400, json.dumps(
                        {"error": "no result yet — run a segmentation first"}))
                with open(p, encoding="utf-8") as f:
                    daily = (json.load(f).get("external") or {}).get("daily") or []
                if not daily:
                    return self._send(400, json.dumps(
                        {"error": "the current result has no daily sales series to score against"}))
                from seg.external import impact_from_daily
                out = impact_from_daily(daily, data.decode("utf-8", "replace"))
                return self._send(200, json.dumps(out, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))

        # --- save a campaign manager's edits to a campaign card's TEXT ---
        # only the free-text fields are writable; the deterministic estimate,
        # segment and priority are never touched by this endpoint
        if self.path == "/api/update_card":
            try:
                idx = req.get("index")
                fields = req.get("fields") or {}
                if not isinstance(idx, int) or not isinstance(fields, dict):
                    return self._send(400, json.dumps(
                        {"error": "body must be {index:int, fields:{...}}"}))
                p = os.path.join(HERE, "out/result.json")
                if not os.path.exists(p):
                    return self._send(400, json.dumps(
                        {"error": "no result yet — run a segmentation first"}))
                with open(p, encoding="utf-8") as f:
                    result = json.load(f)
                cards = result.get("campaigns") or []
                if not (0 <= idx < len(cards)):
                    return self._send(400, json.dumps(
                        {"error": f"index {idx} out of range (0..{len(cards)-1})"}))
                # cards are addressed by index, but a re-run can reorder them
                # (revenue ranking + the appended prospect card). If the client
                # sends what it THINKS sits at idx, verify before mutating so an
                # edit can't land on the wrong card. Reject -> client reloads.
                expect = req.get("expect")
                if isinstance(expect, dict):
                    cur = cards[idx]
                    if (cur.get("segment") != expect.get("segment") or
                            (cur.get("audience") or None) != (expect.get("audience") or None)):
                        return self._send(409, json.dumps(
                            {"error": "card changed since load — reload and retry"}))
                allowed = ("headline", "body", "objective", "channel", "offer", "rationale")
                edited = False
                for k in allowed:
                    if k in fields and isinstance(fields[k], str):
                        cards[idx][k] = fields[k].strip()
                        edited = True
                if not edited:
                    return self._send(400, json.dumps(
                        {"error": "no editable text fields supplied"}))
                cards[idx]["edited"] = True             # mark as manager-edited
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False)
                os.replace(tmp, p)                      # atomic write
                return self._send(200, json.dumps(
                    {"saved": True, "index": idx, "card": cards[idx]}, ensure_ascii=False))
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
