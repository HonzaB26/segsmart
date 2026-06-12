"""AI campaign layer — a LOCAL LLM turns each segment's numbers into a
ready-to-run campaign card. This is the 'AI campaign builder / upsell engine'.

The whole point of the local-friendly product: this runs on the SME's own
machine (gemma via Ollama). The customer database never leaves the building —
the structural advantage no SaaS competitor can match.

Every card has a deterministic fallback so the pipeline never breaks if the
model is slow/offline; the LLM only enriches.
"""
from __future__ import annotations
import json, os, re, urllib.request

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("SEG_LLM_MODEL", "gemma4:26b")   # quality model for campaign copy

# The model writes COPY, never commercial terms: no invented voucher codes
# (qwen happily fabricates Czech-lettered codes nobody's shop accepts).
# Discounts/codes are added by the OWNER via apply_discount().
NO_CODES_EN = ("NEVER invent a discount code, voucher code or coupon code, and "
               "NEVER promise a specific discount percentage or amount — the "
               "shop owner sets real discounts separately. Describe the offer "
               "generically (e.g. 'a comeback offer', 'a loyalty reward').")
NO_CODES_CS = ("NIKDY si nevymýšlej slevový kód ani kupón a NIKDY neslibuj "
               "konkrétní výši slevy v procentech či korunách – skutečnou slevu "
               "nastavuje majitel obchodu sám. Nabídku popiš obecně (např. "
               "'návratová nabídka', 'odměna za věrnost').")

SYSTEM_EN = (
    "You are a pragmatic retention-marketing strategist for a small business. "
    "Given ONE customer segment's statistics, design ONE concrete campaign. "
    "Be specific and realistic for an SME with a small budget. "
    + NO_CODES_EN + " "
    "Respond with STRICT JSON only, no prose, with keys: "
    "objective (<=8 words), channel (Email|SMS|Email+SMS|Push|Direct mail), "
    "offer (<=10 words, concrete), headline (<=8 words, the customer-facing hook), "
    "rationale (<=20 words, why this works for THIS segment), "
    "priority (high|medium|low)."
)
SYSTEM_CS = (
    "Jsi praktický marketingový stratég pro malý e-shop. Pro JEDEN zákaznický "
    "segment navrhni JEDNU konkrétní kampaň. Buď konkrétní a realistický pro malou "
    "firmu s malým rozpočtem. " + NO_CODES_CS + " "
    "Odpověz POUZE validním JSON, bez úvodu, s klíči: "
    "objective (cíl, max 8 slov), channel (E-mail|SMS|E-mail+SMS|Push|Leták), "
    "offer (nabídka, max 10 slov, konkrétní), headline (titulek pro zákazníka, max 8 slov), "
    "rationale (zdůvodnění, max 20 slov), priority (high|medium|low). "
    "VŠECHNY texty piš v korektní češtině. "
    "Odpověď vrať jako JSON objekt v bloku ```json ... ```."
)

# What each segment generally needs — steers the model + powers the fallback.
# Offers stay code-free: the owner introduces a real code via apply_discount().
PLAYBOOK = {
    "Champions": ("Reward & upsell; protect the relationship", "VIP early access + bundle", "Email"),
    "Loyal":     ("Increase frequency; grow basket", "Loyalty points 2x this month", "Email"),
    "At-risk":   ("Win back before they churn", "A 14-day comeback offer", "Email+SMS"),
    "New":       ("Drive the crucial 2nd purchase", "Free shipping on order #2", "Email"),
    "Dormant":   ("Reactivate or let go cheaply", "We miss you – a welcome-back gift", "Email"),
}
PLAYBOOK_CS = {
    "Champions": ("Odměnit a rozšířit nákup; udržet vztah", "VIP přístup k novinkám + dárek", "E-mail"),
    "Loyal":     ("Zvýšit frekvenci; větší košík", "Dvojnásobné věrnostní body", "E-mail"),
    "At-risk":   ("Získat zpět, než odejdou", "Návratová nabídka na 14 dní", "E-mail+SMS"),
    "New":       ("Podpořit klíčový druhý nákup", "Doprava zdarma na druhou objednávku", "E-mail"),
    "Dormant":   ("Reaktivovat, nebo levně opustit", "Chybíte nám – dárek k návratu", "E-mail"),
}


# Reasoning models (qwen3, etc.) emit a think channel and go EMPTY under
# format:json — call them via chat without forced JSON and extract the block.
REASONING = "qwen3" in MODEL or os.environ.get("SEG_LLM_REASONING") == "1"


def _ollama(prompt: str, system: str, timeout=420) -> str | None:
    try:
        if REASONING:
            body = {"model": MODEL, "stream": False, "options": {"temperature": 0.3},
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": prompt}]}
            req = urllib.request.Request(f"{OLLAMA}/api/chat",
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=timeout))["message"]["content"]
        body = {"model": MODEL, "prompt": prompt, "system": system,
                "stream": False, "format": "json", "options": {"temperature": 0.4}}
        req = urllib.request.Request(f"{OLLAMA}/api/generate",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=timeout)).get("response")
    except Exception as e:
        print(f"  [campaign LLM unavailable: {e}] -> fallback")
        return None


from seg.util import extract_json as _extract_json  # noqa: E402  shared robust parser

# 'kód JARO15' / 'use code SAVE20' phrases, and bare SHOUTING+digits tokens —
# the model invents codes no shop accepts, despite the prompt. Belt+braces.
_CODE_PHRASE = re.compile(
    r"\s*(?:s\s+)?(?:kódem|kódu|kód|kupónem|kupón|voucher|coupon|(?:use\s+|with\s+)?code)"
    r"\s*[:\"'„]?\s*[A-Z0-9ÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ_-]{3,}[\"'“]?", re.IGNORECASE)
_BARE_CODE = re.compile(r"\b(?=[A-Z0-9ÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ-]*\d)[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]"
                        r"[A-Z0-9ÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ-]{3,}\b")
_TEXT_FIELDS = ("objective", "offer", "headline", "rationale")


def strip_voucher_codes(text: str, keep: str | None = None) -> str:
    """Remove model-invented voucher codes from copy. `keep` survives —
    that's the real code the owner introduced via apply_discount()."""
    if not text:
        return text
    sentinel = "\x00KEEP\x00"
    if keep:
        text = text.replace(keep, sentinel)
    text = _CODE_PHRASE.sub("", text)
    text = _BARE_CODE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,;:–-")
    if keep:
        text = text.replace(sentinel, keep)
    return text


def _sanitize_card(c: dict, keep: str | None = None) -> dict:
    for k in _TEXT_FIELDS:
        if isinstance(c.get(k), str):
            c[k] = strip_voucher_codes(c[k], keep=keep)
    return c


# a concrete discount the owner never set: '15 %', '20% off', '100 Kč sleva',
# and qwen's malformed currency-first 'Kč3000' / '€50'
_INVENTED_DISCOUNT = re.compile(
    r"\d+\s*%|\d+\s*(?:kč|czk|eur|€|\$|£)\b|(?:kč|czk|eur|€|\$|£)\s*\d+",
    re.IGNORECASE)


def has_invented_discount(c: dict) -> bool:
    """True when the model promised a concrete discount value. Surgery on the
    copy would mangle the grammar, so callers fall back to clean rule-based
    text instead."""
    return any(_INVENTED_DISCOUNT.search(c.get(k) or "") for k in _TEXT_FIELDS)


def _fallback(seg: str, lang="en") -> dict:
    book = PLAYBOOK_CS if lang == "cs" else PLAYBOOK
    default = ("Oslovit segment", "Cílená nabídka", "E-mail") if lang == "cs" \
        else ("Engage the segment", "Targeted offer", "Email")
    obj, offer, ch = book.get(seg, default)
    rat = "Pravidlové výchozí nastavení." if lang == "cs" else "Rule-based default."
    return {"objective": obj, "channel": ch, "offer": offer,
            "headline": offer, "rationale": rat,
            "priority": "medium", "_source": "fallback"}


def card_for_segment(seg_name: str, prof_row: dict, hook: dict, use_llm=True,
                     currency="£", lang="en") -> dict:
    """Build one campaign card for a segment from its profile row + seasonal hook."""
    if not use_llm:
        c = _fallback(seg_name, lang)
    else:
        prompt = (
            f"Currency: all money is in {currency}. Use {currency} in any amounts.\n"
            f"Segment: {seg_name}\n"
            f"Customers: {int(prof_row['customers'])} ({prof_row['share_pct']}% of base)\n"
            f"Revenue share: {prof_row['rev_share_pct']}%\n"
            f"Avg days since last purchase: {prof_row['avg_recency']:.0f}\n"
            f"Avg orders per customer: {prof_row['avg_frequency']:.1f}\n"
            f"Avg spend per customer: {currency}{prof_row['avg_monetary']:.0f}\n"
            f"Avg order value: {currency}{prof_row['avg_order_value']:.0f}\n"
            f"Seasonal context: peak month is {hook['peak_month']} "
            f"(+{hook['peak_uplift_pct']}% vs average), low month is {hook['low_month']}.\n"
            f"Design the campaign as JSON."
        )
        raw = _ollama(prompt, SYSTEM_CS if lang == "cs" else SYSTEM_EN)
        try:
            c = _sanitize_card(_extract_json(raw))   # no invented voucher codes
            if has_invented_discount(c):             # nor invented discounts —
                c = _fallback(seg_name, lang)        # those come from the owner
            else:
                c.setdefault("priority", "medium")
                c["_source"] = MODEL
        except Exception:
            c = _fallback(seg_name, lang)

    # deterministic, defensible impact estimate (not hallucinated by the LLM)
    c["estimate"] = _estimate(seg_name, prof_row)
    c["segment"] = seg_name
    c["llm_priority"] = c.get("priority", "medium")   # keep model's view as a hint
    return c


# Rough, transparent uplift assumptions per segment (owner can tune these).
RESPONSE = {"Champions": 0.25, "Loyal": 0.15, "At-risk": 0.08, "New": 0.20, "Dormant": 0.04}


def _estimate(seg: str, p: dict) -> dict:
    n = int(p["customers"])
    resp = RESPONSE.get(seg, 0.05)
    # expected incremental orders ~ responders; value ~ their avg order value
    responders = round(n * resp)
    est_rev = round(responders * float(p["avg_order_value"]))
    return {"assumed_response_rate": resp,
            "expected_responders": responders,
            "est_incremental_revenue": est_rev}


def _discount_phrase(discount: dict, lang: str, currency: str) -> str:
    """Human wording for an owner-specified discount, used by the fallback
    rewrite and shown in the mailing body."""
    kind = discount.get("kind", "percent")
    value = discount.get("value")
    code = discount.get("code", "")
    if lang == "cs":
        if kind == "free_shipping":
            base = "doprava zdarma"
        elif kind == "amount":
            base = f"sleva {value} {currency}"
        else:
            base = f"sleva {value} %"
        return f"{base} s kódem {code}" if code else base
    if kind == "free_shipping":
        base = "free shipping"
    elif kind == "amount":
        base = f"{currency}{value} off"
    else:
        base = f"{value}% off"
    return f"{base} with code {code}" if code else base


def apply_discount(card: dict, discount: dict, lang="en", currency="£",
                   use_llm=True) -> dict:
    """Rewrite a card's copy around an OWNER-specified discount (kind, value,
    code). The code comes from the shop's real system — never from the model.
    LLM rewrite when available, deterministic template otherwise."""
    card = dict(card)
    phrase = _discount_phrase(discount, lang, currency)
    code = discount.get("code") or None
    rewritten = None
    if use_llm:
        sysmsg = SYSTEM_CS if lang == "cs" else SYSTEM_EN
        prompt = (
            f"Rewrite this campaign to feature exactly this offer: {phrase}.\n"
            f"Use the code {code} verbatim if given; do not invent any other code.\n"
            f"Current campaign JSON:\n{json.dumps({k: card.get(k) for k in _TEXT_FIELDS}, ensure_ascii=False)}\n"
            f"Segment: {card.get('segment')}\nChannel stays: {card.get('channel')}\n"
            "Return the same JSON keys."
        ) if lang != "cs" else (
            f"Přepiš tuto kampaň tak, aby nabízela přesně: {phrase}.\n"
            f"Kód {code} použij doslova, žádný jiný kód nevymýšlej.\n"
            f"Současná kampaň (JSON):\n{json.dumps({k: card.get(k) for k in _TEXT_FIELDS}, ensure_ascii=False)}\n"
            f"Segment: {card.get('segment')}\nKanál zůstává: {card.get('channel')}\n"
            "Vrať JSON se stejnými klíči."
        )
        raw = _ollama(prompt, sysmsg)
        if raw:
            try:
                rewritten = _sanitize_card(_extract_json(raw), keep=code)
            except Exception:
                rewritten = None
    if rewritten:
        for k in _TEXT_FIELDS:
            if isinstance(rewritten.get(k), str) and rewritten[k]:
                card[k] = rewritten[k]
        card["_source"] = MODEL
    else:                                           # deterministic rewrite
        card["offer"] = phrase[0].upper() + phrase[1:]
        card["headline"] = card["offer"]
        card["_source"] = "owner-discount"
    # the offer must actually contain the user's code — belt and braces
    if code and code not in (card.get("offer") or "") and code not in (card.get("headline") or ""):
        card["offer"] = (card.get("offer", "").rstrip(". ") +
                         (f" – kód {code}" if lang == "cs" else f" — code {code}"))
    card["discount"] = {"kind": discount.get("kind", "percent"),
                        "value": discount.get("value"), "code": code}
    return card


def all_cards(profiles, hook: dict, use_llm=True, currency="£", lang="en") -> list[dict]:
    cards = []
    for seg, row in profiles.iterrows():
        cards.append(card_for_segment(seg, row.to_dict(), hook, use_llm=use_llm,
                                      currency=currency, lang=lang))
    # priority is set DETERMINISTICALLY by revenue opportunity, not by the LLM:
    # the model over-rates everything 'high'. Rank by est. incremental revenue.
    ranked = sorted(cards, key=lambda c: -c["estimate"]["est_incremental_revenue"])
    for i, c in enumerate(ranked):
        c["priority"] = "high" if i < 2 else ("medium" if i < 4 else "low")
    return ranked


if __name__ == "__main__":
    import sys
    from seg.loader import load_uci
    from seg.features import build_features
    from seg.segment import rfm_segments, segment_profiles
    from seg.seasonality import peak_hook
    d = load_uci()
    f = rfm_segments(build_features(d))
    prof = segment_profiles(f)
    hook = peak_hook(d)
    use_llm = "--no-llm" not in sys.argv
    for c in all_cards(prof, hook, use_llm=use_llm):
        print(f"\n[{c['priority'].upper()}] {c['segment']}  (via {c['_source']})")
        print(f"  objective : {c['objective']}")
        print(f"  channel   : {c['channel']}   offer: {c['offer']}")
        print(f"  headline  : “{c['headline']}”")
        print(f"  rationale : {c['rationale']}")
        e = c["estimate"]
        print(f"  estimate  : ~{e['expected_responders']} responders -> "
              f"~{e['est_incremental_revenue']:,} revenue "
              f"(@{int(e['assumed_response_rate']*100)}% response)")
