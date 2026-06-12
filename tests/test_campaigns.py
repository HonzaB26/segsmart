from seg.campaigns import all_cards, _estimate, _fallback, RESPONSE
from seg.segment import segment_profiles
from seg.seasonality import peak_hook


def test_fallback_cards_no_llm(feat, milan_df):
    prof = segment_profiles(feat)
    hook = peak_hook(milan_df)
    cards = all_cards(prof, hook, use_llm=False, currency="Kč", lang="cs")
    assert len(cards) == len(prof)
    for c in cards:
        assert c["_source"] == "fallback"
        for k in ("objective", "channel", "offer", "headline", "rationale", "estimate"):
            assert k in c


def test_priority_is_deterministic_by_revenue(feat, milan_df):
    prof = segment_profiles(feat)
    cards = all_cards(prof, peak_hook(milan_df), use_llm=False)
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
