"""HTTP Basic Auth (SEG_AUTH) — exercised over real HTTP on an ephemeral port."""
import base64
import json
import threading
import urllib.request
import urllib.error

import pytest

import server as srv
from http.server import ThreadingHTTPServer


@pytest.fixture
def http(monkeypatch):
    """A live server; returns (base_url, set_auth) so tests flip AUTH."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, lambda v: monkeypatch.setattr(srv, "AUTH", v)
    httpd.shutdown()


def _get(url, user_pass=None):
    req = urllib.request.Request(url)
    if user_pass:
        req.add_header("Authorization",
                       "Basic " + base64.b64encode(user_pass.encode()).decode())
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


def test_no_auth_configured_everything_open(http):
    base, set_auth = http
    set_auth("")
    assert _get(base + "/")[0] == 200
    assert _get(base + "/api/config")[0] == 200


def test_auth_required_when_configured(http):
    base, set_auth = http
    set_auth("owner:tajneheslo")
    for path in ("/", "/setup", "/api/result", "/api/config"):
        code, headers = _get(base + path)
        assert code == 401
        assert "Basic" in headers.get("WWW-Authenticate", "")   # browser prompts


def test_wrong_and_malformed_credentials_rejected(http):
    base, set_auth = http
    set_auth("owner:tajneheslo")
    assert _get(base + "/api/config", "owner:spatne")[0] == 401
    assert _get(base + "/api/config", "owner")[0] == 401
    # malformed base64
    req = urllib.request.Request(base + "/api/config")
    req.add_header("Authorization", "Basic !!!not-base64!!!")
    try:
        code = urllib.request.urlopen(req, timeout=10).status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 401


def test_correct_credentials_accepted(http):
    base, set_auth = http
    set_auth("owner:tajneheslo")
    code, _ = _get(base + "/api/config", "owner:tajneheslo")
    assert code == 200


def test_post_also_guarded(http):
    base, set_auth = http
    set_auth("owner:tajneheslo")
    req = urllib.request.Request(base + "/api/run_config",
                                 data=json.dumps({"save": False}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        code = urllib.request.urlopen(req, timeout=10).status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 401
