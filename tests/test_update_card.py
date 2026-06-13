"""/api/update_card — managers edit campaign card TEXT; deterministic fields
(estimate/segment/priority) stay read-only. Exercised over real HTTP, with
result.json isolated to a tmp dir so the test never touches the real demo."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import server as srv


@pytest.fixture
def http(monkeypatch, tmp_path):
    (tmp_path / "out").mkdir()
    result = {"campaigns": [
        {"segment": "Champions", "headline": "Old", "body": "", "objective": "o",
         "channel": "E-mail", "offer": "x", "rationale": "r", "priority": "high",
         "estimate": {"expected_responders": 5}},
    ]}
    (tmp_path / "out" / "result.json").write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(srv, "HERE", str(tmp_path))
    monkeypatch.setattr(srv, "AUTH", "")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()


def _post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_saves_editable_text(http):
    base, tmp = http
    code, body = _post(base + "/api/update_card",
                       {"index": 0, "fields": {"headline": "Nový titulek", "body": "Ahoj"}})
    assert code == 200 and body["saved"] is True
    saved = json.loads((tmp / "out" / "result.json").read_text(encoding="utf-8"))
    card = saved["campaigns"][0]
    assert card["headline"] == "Nový titulek" and card["body"] == "Ahoj"
    assert card["edited"] is True


def test_deterministic_fields_are_not_writable(http):
    base, tmp = http
    # only protected fields supplied -> nothing editable -> 400, estimate untouched
    code, _ = _post(base + "/api/update_card",
                    {"index": 0, "fields": {"estimate": {"x": 1}, "segment": "Hacked"}})
    assert code == 400
    saved = json.loads((tmp / "out" / "result.json").read_text(encoding="utf-8"))
    assert saved["campaigns"][0]["segment"] == "Champions"
    assert saved["campaigns"][0]["estimate"] == {"expected_responders": 5}


def test_bad_index_and_shape_rejected(http):
    base, _ = http
    assert _post(base + "/api/update_card", {"index": 99, "fields": {"headline": "x"}})[0] == 400
    assert _post(base + "/api/update_card", {"index": "0", "fields": {}})[0] == 400


def test_identity_mismatch_rejected(http):
    base, tmp = http
    # client thinks index 0 is the prospect 'grow' card, but on disk it's the
    # Champions segment card -> reordered since load -> reject, don't mis-save
    code, body = _post(base + "/api/update_card",
                       {"index": 0, "fields": {"headline": "Should not stick"},
                        "expect": {"segment": "Prospects", "audience": "prospects"}})
    assert code == 409 and "reload" in body["error"]
    saved = json.loads((tmp / "out" / "result.json").read_text(encoding="utf-8"))
    assert saved["campaigns"][0]["headline"] == "Old"      # untouched


def test_identity_match_saves(http):
    base, tmp = http
    code, body = _post(base + "/api/update_card",
                       {"index": 0, "fields": {"headline": "Matched"},
                        "expect": {"segment": "Champions", "audience": None}})
    assert code == 200 and body["saved"] is True
    saved = json.loads((tmp / "out" / "result.json").read_text(encoding="utf-8"))
    assert saved["campaigns"][0]["headline"] == "Matched"
