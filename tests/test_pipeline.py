import pipeline


def test_pipeline_end_to_end_no_llm(synth_csv, tmp_path):
    out = tmp_path / "result.json"
    r = pipeline.run(source="milan", path=synth_csv, currency="Kč",
                     use_llm=False, out=str(out))
    # top-level structure the dashboard depends on
    for k in ("meta", "kpis", "segments", "seasonality", "campaigns", "validation"):
        assert k in r
    assert r["meta"]["currency"] == "Kč"
    assert 0 < len(r["segments"]) <= 5
    assert len(r["campaigns"]) == len(r["segments"])
    assert out.exists()


def test_pipeline_lang_auto_czech(synth_csv, tmp_path):
    r = pipeline.run(source="milan", path=synth_csv, currency="Kč",
                     use_llm=False, out=str(tmp_path / "r.json"))
    # Czech currency -> Czech fallback copy
    assert any("E-mail" in c["channel"] or "SMS" in c["channel"] for c in r["campaigns"])


def test_kpis_present(synth_csv, tmp_path):
    r = pipeline.run(source="milan", path=synth_csv, currency="Kč",
                     use_llm=False, out=str(tmp_path / "r.json"))
    k = r["kpis"]
    assert k["total_customers"] > 0
    assert 0 <= k["repeat_rate_pct"] <= 100
    assert k["avg_order_value"] > 0
