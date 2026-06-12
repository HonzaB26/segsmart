"""Launch: turn an approved campaign card into a mailer-ready artifact.

SegSmart never sends e-mail itself ("human gate" — nothing goes out
automatically). Launch produces a mailing artifact:
  - always: a JSON file under out/mailings/ (subject, body, recipients) that
    any mailer integration can consume,
  - optionally: an HTTP POST of the same JSON to config "mailer.webhook_url"
    (n8n / Zapier / a 10-line SMTP script — whatever the shop wires in).

The body is assembled deterministically from the approved card — the LLM has
already had its say in the card copy; launching adds nothing hallucinated.
"""
from __future__ import annotations
import json, os, re, time, urllib.request

from seg.util import atomic_write_json

MAILINGS_DIR = "out/mailings"


def _body(card: dict, lang: str, currency: str) -> str:
    greet = "Dobrý den," if lang == "cs" else "Hello,"
    lines = [greet, "", card.get("headline", "")]
    if card.get("offer") and card["offer"] != card.get("headline"):
        lines += ["", card["offer"]]
    d = card.get("discount") or {}
    if d.get("code"):
        lines += ["", (f"Váš kód: {d['code']}" if lang == "cs"
                       else f"Your code: {d['code']}")]
    lines += ["", ("Děkujeme, že u nás nakupujete." if lang == "cs"
                   else "Thank you for shopping with us.")]
    return "\n".join(lines)


def build_mailing(card: dict, recipients: list, lang="en", currency="£") -> dict:
    """Campaign card + recipient rows -> a self-contained mailing artifact."""
    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "segment": card.get("segment"),
        "channel": card.get("channel"),
        "subject": card.get("headline", ""),
        "body_text": _body(card, lang, currency),
        "discount": card.get("discount"),
        "estimate": card.get("estimate"),
        "language": lang,
        "currency": currency,
        "recipients": [{"customer_id": str(r.get("id") or r.get("customer_id") or "")}
                       for r in recipients
                       if (r.get("id") or r.get("customer_id"))],
    }


def save_mailing(mailing: dict, out_dir: str = MAILINGS_DIR) -> str:
    seg = re.sub(r"\W+", "_", (mailing.get("segment") or "segment")).strip("_").lower()
    stamp, n = time.strftime("%Y%m%d-%H%M%S"), 1
    path = os.path.join(out_dir, f"mailing-{stamp}-{seg}.json")
    while os.path.exists(path):
        n += 1
        path = os.path.join(out_dir, f"mailing-{stamp}-{seg}-{n}.json")
    return atomic_write_json(path, mailing)


def deliver(mailing: dict, mailer_cfg: dict | None, timeout=30) -> dict:
    """POST the mailing JSON to the configured webhook, if any. Returns a
    delivery report; never raises (launch must succeed even if the hook is
    down — the artifact on disk is the source of truth)."""
    url = (mailer_cfg or {}).get("webhook_url", "")
    if not url:
        return {"delivered": False, "via": None,
                "note": "no mailer webhook configured (config: mailer.webhook_url)"}
    try:
        req = urllib.request.Request(url, data=json.dumps(mailing).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"delivered": True, "via": "webhook", "status": r.status}
    except Exception as e:
        return {"delivered": False, "via": "webhook", "error": str(e)}
