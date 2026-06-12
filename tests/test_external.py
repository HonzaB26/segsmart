"""External factors layer — all offline, no network calls ever.

Tests cover: CSV parsing, impact calculations (flag lift + numeric correlation),
and the end-to-end manual-upload path (`impact_from_daily`).
"""
import pandas as pd

from seg.external import (
    load_external_csv,
    factor_impact,
    combine,
    daily_sales,
    impact_from_daily,
)


def test_load_external_csv_autodetects_date_and_numeric():
    text = "den,ad_spend,promo\n2025-01-01,1000,1\n2025-01-02,500,0\n"
    df = load_external_csv(text, is_text=True)
    assert "date" in df.columns
    assert df["ad_spend"].tolist() == [1000, 500]
    assert df["promo"].tolist() == [1, 0]
    assert df["date"].dt.normalize().tolist() == [
        pd.Timestamp("2025-01-01"),
        pd.Timestamp("2025-01-02"),
    ]


def test_load_external_csv_empty_returns_empty():
    df = load_external_csv("date\n", is_text=True)
    assert df.empty


def _daily_with_flag():
    """60-day frame: revenue = 200 on promo days (every 5th), 100 otherwise."""
    days = pd.date_range("2025-01-01", periods=60, freq="D")
    flag = [(1 if i % 5 == 0 else 0) for i in range(60)]
    rev = [200 if f else 100 for f in flag]
    return pd.DataFrame(
        {"date": days, "revenue": rev, "orders": [1] * 60, "promo": flag}
    )


def test_factor_impact_flag_lift_positive():
    imp = factor_impact(_daily_with_flag())
    promo = next(f for f in imp if f["factor"] == "promo")
    assert promo["type"] == "flag"
    assert promo["direction"] == "raises"
    assert promo["effect_pct"] > 0


def test_factor_impact_numeric_correlation():
    days = pd.date_range("2025-01-01", periods=60, freq="D")
    temp = list(range(60))
    rev = [t * 10 + 50 for t in temp]          # perfect positive correlation
    daily = pd.DataFrame(
        {"date": days, "revenue": rev, "orders": [1] * 60, "temp": temp}
    )
    imp = factor_impact(daily)
    t = next(f for f in imp if f["factor"] == "temp")
    assert t["type"] == "numeric"
    assert t["correlation"] > 0.99


def test_factor_impact_too_few_days_returns_empty():
    days = pd.date_range("2025-01-01", periods=10, freq="D")
    df = pd.DataFrame({"date": days, "revenue": range(10), "orders": [1] * 10,
                       "x": range(10)})
    assert factor_impact(df, min_days=30) == []


def test_impact_from_daily_end_to_end():
    daily = _daily_with_flag()
    records = [
        {"date": d.strftime("%Y-%m-%d"), "revenue": r, "orders": o}
        for d, r, o in zip(daily["date"], daily["revenue"], daily["orders"])
    ]
    csv = "date,promo\n" + "\n".join(
        f"{d.strftime('%Y-%m-%d')},{p}"
        for d, p in zip(daily["date"], daily["promo"])
    )
    out = impact_from_daily(records, csv)
    assert out["factors"] == ["promo"]
    assert out["matched_days"] == 60
    assert out["n_days"] == 60
    assert any(
        f["factor"] == "promo" and f["direction"] == "raises"
        for f in out["impact"]
    )


def test_impact_from_daily_empty_records():
    out = impact_from_daily([], "date,x\n2025-01-01,1\n")
    assert out["n_days"] == 0
    assert out["matched_days"] == 0


def test_daily_sales_aggregates_to_day(eshop_df):
    ds = daily_sales(eshop_df)
    assert "date" in ds.columns
    assert "revenue" in ds.columns
    assert "orders" in ds.columns
    assert len(ds) <= len(eshop_df)         # at most one row per day
    assert (ds["revenue"] > 0).all()
