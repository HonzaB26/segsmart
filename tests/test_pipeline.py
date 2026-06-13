import pipeline


def test_pipeline_end_to_end_no_llm(synth_csv, tmp_path):
    out = tmp_path / "result.json"
    r = pipeline.run(source="eshop", path=synth_csv, currency="Kč",
                     use_llm=False, out=str(out))
    # top-level structure the dashboard depends on
    for k in ("meta", "kpis", "segments", "seasonality", "campaigns", "validation"):
        assert k in r
    assert r["meta"]["currency"] == "Kč"
    assert 0 < len(r["segments"]) <= 5
    assert len(r["campaigns"]) == len(r["segments"])
    assert out.exists()


def test_pipeline_lang_auto_czech(synth_csv, tmp_path):
    r = pipeline.run(source="eshop", path=synth_csv, currency="Kč",
                     use_llm=False, out=str(tmp_path / "r.json"))
    # Czech currency -> Czech fallback copy
    assert any("E-mail" in c["channel"] or "SMS" in c["channel"] for c in r["campaigns"])


def test_kpis_present(synth_csv, tmp_path):
    r = pipeline.run(source="eshop", path=synth_csv, currency="Kč",
                     use_llm=False, out=str(tmp_path / "r.json"))
    k = r["kpis"]
    assert k["total_customers"] > 0
    assert 0 <= k["repeat_rate_pct"] <= 100
    assert k["avg_order_value"] > 0


def test_currency_code_normalized_to_symbol(synth_csv, tmp_path):
    # a config/source may hand us "CZK" instead of "Kč"; the pipeline must
    # normalise it so the code never leaks into the UI or the language heuristic
    # (this caused a Czech card wrapped in an English "Hello,"/"Your team" body)
    r = pipeline.run(source="eshop", path=synth_csv, currency="CZK",
                     use_llm=False, out=str(tmp_path / "r.json"))
    assert r["meta"]["currency"] == "Kč"
    assert r["meta"]["language"] == "cs"      # CZK -> Kč -> Czech content


def test_tracked_demo_result_is_consistent():
    """The committed demo drives the dashboard and a one-click launch, so it
    must not ship in a broken state: language set, currency a symbol (not an ISO
    code), and no campaign card promising a discount the owner never approved."""
    import json
    import os
    from seg.campaigns import has_invented_discount
    p = os.path.join(os.path.dirname(__file__), "..", "out", "result.json")
    if not os.path.exists(p):
        return
    d = json.load(open(p, encoding="utf-8"))
    assert d["meta"].get("language") in ("cs", "en", "de"), \
        "demo meta.language unset — launch would mix UI/content languages"
    assert d["meta"].get("currency") not in ("CZK", "EUR", "USD", "GBP"), \
        "demo currency is an ISO code, not a display symbol"
    bad = [c.get("segment") for c in d.get("campaigns", []) if has_invented_discount(c)]
    assert not bad, f"demo cards promise invented discounts: {bad}"
