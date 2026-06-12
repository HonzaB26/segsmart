"""AI campaign layer — a LOCAL LLM turns each segment's numbers into a
ready-to-run campaign card. This is the 'AI campaign builder / upsell engine'.

The whole point of the local-friendly product: this runs on the SME's own
machine (gemma via Ollama). The customer database never leaves the building —
the structural advantage no SaaS competitor can match.

Every card has a deterministic fallback so the pipeline never breaks if the
model is slow/offline; the LLM only enriches.
"""
from __future__ import annotations
import json, os, urllib.request

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("SEG_LLM_MODEL", "gemma4:26b")   # quality model for campaign copy

SYSTEM_EN = (
    "You are a pragmatic retention-marketing strategist for a small business. "
    "Given ONE customer segment's statistics, design ONE concrete campaign. "
    "Be specific and realistic for an SME with a small budget. "
    "Respond with STRICT JSON only, no prose, with keys: "
    "objective (<=8 words), channel (Email|SMS|Email+SMS|Push|Direct mail), "
    "offer (<=10 words, concrete), headline (<=8 words, the customer-facing hook), "
    "rationale (<=20 words, why this works for THIS segment), "
    "priority (high|medium|low)."
)
SYSTEM_CS = (
    "Jsi praktický marketingový stratég pro malý e-shop. Pro JEDEN zákaznický "
    "segment navrhni JEDNU konkrétní kampaň. Buď konkrétní a realistický pro malou "
    "firmu s malým rozpočtem. Odpověz POUZE validním JSON, bez úvodu, s klíči: "
    "objective (cíl, max 8 slov), channel (E-mail|SMS|E-mail+SMS|Push|Leták), "
    "offer (nabídka, max 10 slov, konkrétní), headline (titulek pro zákazníka, max 8 slov), "
    "rationale (zdůvodnění, max 20 slov), priority (high|medium|low). "
    "VŠECHNY texty piš v korektní češtině. "
    "Odpověď vrať jako JSON objekt v bloku ```json ... ```."
)

# What each segment generally needs — steers the model + powers the fallback.
PLAYBOOK = {
    "Champions": ("Reward & upsell; protect the relationship", "VIP early access + bundle", "Email"),
    "Loyal":     ("Increase frequency; grow basket", "Loyalty points 2x this month", "Email"),
    "At-risk":   ("Win back before they churn", "15% off next order, 14 days", "Email+SMS"),
    "New":       ("Drive the crucial 2nd purchase", "Free shipping on order #2", "Email"),
    "Dormant":   ("Reactivate or let go cheaply", "We miss you – 20% comeback code", "Email"),
}
PLAYBOOK_CS = {
    "Champions": ("Odměnit a rozšířit nákup; udržet vztah", "VIP přístup k novinkám + dárek", "E-mail"),
    "Loyal":     ("Zvýšit frekvenci; větší košík", "Dvojnásobné věrnostní body", "E-mail"),
    "At-risk":   ("Získat zpět, než odejdou", "15 % sleva na další nákup, 14 dní", "E-mail+SMS"),
    "New":       ("Podpořit klíčový druhý nákup", "Doprava zdarma na druhou objednávku", "E-mail"),
    "Dormant":   ("Reaktivovat, nebo levně opustit", "Chybíte nám – kód na 20 % zpět", "E-mail"),
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


def _extract_json(raw: str) -> dict:
    """Pull a JSON object from model output (handles ```json fences, think tags)."""
    import re
    if not raw:
        raise ValueError("empty")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    blob = m.group(1) if m else raw[raw.index("{"):raw.rindex("}") + 1]
    return json.loads(blob)


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
            c = _extract_json(raw)
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
