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

# someone has to sign the e-mail; the owner sets the real one in
# config output.signature (or the /setup text box) — this is the
# obviously-edit-me placeholder
DEFAULT_SIGNATURE = {"cs": "Váš tým", "en": "Your team"}


def _clean(s: str) -> str:
    """Strip CR/LF (and surrounding space) from a recipient field — a name or
    e-mail carrying a newline could inject SMTP/CSV headers in whatever mailer
    consumes the artifact. Real names and addresses never contain them."""
    return s.replace("\r", " ").replace("\n", " ").strip()


def _body(card: dict, lang: str, currency: str, signature: str) -> str:
    greet = "Dobrý den," if lang == "cs" else "Hello,"
    lines = [greet, "", card.get("headline", "")]
    if card.get("offer") and card["offer"] != card.get("headline"):
        lines += ["", card["offer"]]
    d = card.get("discount") or {}
    if d.get("code"):
        lines += ["", (f"Váš kód: {d['code']}" if lang == "cs"
                       else f"Your code: {d['code']}")]
    lines += ["", ("Děkujeme, že u nás nakupujete." if lang == "cs"
                   else "Thank you for shopping with us."),
              "", signature]
    return "\n".join(lines)


def build_mailing(card: dict, recipients: list, lang="en", currency="£",
                  signature: str | None = None) -> dict:
    """Campaign card + recipient rows -> a self-contained mailing artifact a
    mailer can consume directly (recipients carry e-mail + name, not just ids)."""
    rows = []
    for r in recipients:
        cid = _clean(str(r.get("id") or r.get("customer_id") or ""))
        if not cid:
            continue
        email = _clean(str(r.get("email") or ""))
        if not email and "@" in cid:               # the id IS the e-mail
            email = cid
        rows.append({"customer_id": cid, "email": email,
                     "name": _clean(str(r.get("name") or ""))})
    signature = (signature or "").strip() or DEFAULT_SIGNATURE.get(
        lang, DEFAULT_SIGNATURE["en"])
    return {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "segment": card.get("segment"),
        "channel": card.get("channel"),
        "subject": card.get("headline", ""),
        "signature": signature,
        "body_text": _body(card, lang, currency, signature),
        "discount": card.get("discount"),
        "estimate": card.get("estimate"),
        "language": lang,
        "currency": currency,
        "recipients": rows,
        "deliverable": sum(1 for r in rows if r["email"]),
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
    if not re.match(r"https?://", url, re.IGNORECASE):
        # only HTTP(S) — block file://, gopher:// etc. that urllib would honour
        return {"delivered": False, "via": "webhook",
                "error": "mailer.webhook_url must be an http(s) URL"}
    try:
        req = urllib.request.Request(url, data=json.dumps(mailing).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"delivered": True, "via": "webhook", "status": r.status}
    except Exception as e:
        return {"delivered": False, "via": "webhook", "error": str(e)}
