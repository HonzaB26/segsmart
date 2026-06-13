from seg.campaigns import (all_cards, card_for_prospects, _estimate, _fallback,
                           RESPONSE)
from seg.segment import segment_profiles
from seg.seasonality import peak_hook


_PSTATS = {"for_segment": "Champions", "customers": 15, "share_pct": 5.0,
           "rev_share_pct": 4.0, "avg_recency": 40, "avg_frequency": 7,
           "avg_monetary": 3000, "avg_order_value": 450}


def test_fallback_cards_no_llm(feat, eshop_df):
    prof = segment_profiles(feat)
    hook = peak_hook(eshop_df)
    cards = all_cards(prof, hook, use_llm=False, currency="Kč", lang="cs")
    assert len(cards) == len(prof)
    for c in cards:
        assert c["_source"] == "fallback"
        for k in ("objective", "channel", "offer", "headline", "rationale", "estimate"):
            assert k in c


def test_priority_is_deterministic_by_revenue(feat, eshop_df):
    prof = segment_profiles(feat)
    cards = all_cards(prof, peak_hook(eshop_df), use_llm=False)
    revs = [c["estimate"]["est_incremental_revenue"] for c in cards]
    assert revs == sorted(revs, reverse=True)          # sorted high->low
    assert cards[0]["priority"] == "high"
    assert cards[-1]["priority"] == "low"


def test_estimate_uses_response_rate():
    p = {"customers": 100, "avg_order_value": 500}
    e = _estimate("Champions", p)
    assert e["assumed_response_rate"] == RESPONSE["Champions"]
    assert e["expected_responders"] == round(100 * RESPONSE["Champions"])
    assert e["est_incremental_revenue"] == e["expected_responders"] * 500


def test_czech_fallback_is_czech():
    c = _fallback("Dormant", lang="cs")
    assert "E-mail" in c["channel"] or "SMS" in c["channel"]
    assert c["rationale"] == "Pravidlové výchozí nastavení."


def test_prospect_card_shape():
    c = card_for_prospects("Champions", _PSTATS, {"peak_month": "Dec",
        "peak_uplift_pct": 20, "low_month": "Jan"}, use_llm=False, lang="en")
    assert c["audience"] == "prospects"
    assert c["segment"] == "Prospects"
    assert c["seed_segment"] == "Champions"
    assert "Champions" in c["objective"]               # frames the goal as upgrade
    assert c["estimate"]["assumed_response_rate"] == RESPONSE["Prospects"]
    assert c["estimate"]["est_incremental_revenue"] == round(15 * RESPONSE["Prospects"]) * 450


def test_prospect_card_czech_fallback():
    c = card_for_prospects("Champions", _PSTATS, {"peak_month": "Pro",
        "peak_uplift_pct": 20, "low_month": "Led"}, use_llm=False, lang="cs")
    assert c["rationale"] == "Pravidlové výchozí nastavení."
    assert "E-mail" in c["channel"]


def test_all_cards_appends_prospect_card(feat, eshop_df):
    prof = segment_profiles(feat)
    hook = peak_hook(eshop_df)
    base = all_cards(prof, hook, use_llm=False)
    grown = all_cards(prof, hook, use_llm=False, prospects=_PSTATS)
    assert len(grown) == len(base) + 1                 # exactly one extra
    pros = [c for c in grown if c.get("audience") == "prospects"]
    assert len(pros) == 1 and pros[0]["priority"] in ("high", "medium", "low")


def test_no_prospect_card_when_empty(feat, eshop_df):
    prof = segment_profiles(feat)
    cards = all_cards(prof, peak_hook(eshop_df), use_llm=False,
                      prospects={"for_segment": "Champions", "customers": 0})
    assert not any(c.get("audience") == "prospects" for c in cards)
