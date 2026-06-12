from seg.seasonality import monthly_curve, seasonality_index, peak_hook


def test_index_centered_near_100(eshop_df):
    si = seasonality_index(eshop_df)
    assert len(si) >= 1
    # index is normalised so the mean month ~= 100
    assert 80 <= si["index"].mean() <= 120
    assert si["month_num"].between(1, 12).all()


def test_peak_hook_consistency(eshop_df):
    hook = peak_hook(eshop_df)
    assert hook["peak_index"] >= hook["low_index"]
    assert hook["peak_uplift_pct"] == hook["peak_index"] - 100


def test_monthly_curve_columns(eshop_df):
    m = monthly_curve(eshop_df)
    for c in ("month", "revenue", "orders"):
        assert c in m.columns
    assert (m["revenue"] >= 0).all()


def test_synthetic_has_winter_peak(eshop_df):
    # the generator encodes a Vánoce peak — Nov or Dec should top the index
    si = seasonality_index(eshop_df).set_index("month")
    if {"Nov", "Dec"}.issubset(si.index):
        assert si.loc[["Nov", "Dec"], "index"].max() == si["index"].max()
