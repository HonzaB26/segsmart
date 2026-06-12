from seg.loader import (CANON, load_dataframe, load_milan, _to_num, summary)
import pandas as pd


def test_canonical_columns(milan_df):
    for c in CANON:
        assert c in milan_df.columns


def test_to_num_european():
    s = pd.Series(["1 234,56", "9,90", "1.000,00"])
    out = _to_num(s, decimal=",")
    assert abs(out.iloc[0] - 1234.56) < 1e-6
    assert abs(out.iloc[1] - 9.90) < 1e-6
    assert abs(out.iloc[2] - 1000.0) < 1e-6


def test_to_num_dot_passthrough():
    s = pd.Series([1.5, 2.0])
    assert list(_to_num(s)) == [1.5, 2.0]


def test_load_dataframe_mapping_and_decimal(generic_df):
    m = {"customer_id": "cust", "order_id": "ord", "order_date": "when",
         "quantity": "qty", "unit_price": "price", "product": "item"}
    df = load_dataframe(generic_df, m, decimal=",")
    assert len(df) == 5
    assert df["customer_id"].nunique() == 3
    # line_value = qty * unit_price, parsed from european decimals
    row0 = df.iloc[0]
    assert abs(row0["line_value"] - 2 * 10.50) < 1e-6


def test_milan_drops_structural_and_cancelled(synth_csv):
    raw = pd.read_csv(synth_csv, dtype=str, keep_default_na=False)
    df = load_milan(synth_csv)
    # no BILLING/SHIPPING lines survive
    assert not df["product"].str.upper().str.startswith(("BILLING", "SHIPPING")).any()
    # cancelled/returned orders removed
    assert not df["status"].isin(
        {"Stornována", "Vrácená objednávka", "Platba selhala"}).any()
    # cleaned set is smaller than raw
    assert len(df) < len(raw)


def test_milan_customer_key_is_email(milan_df):
    assert milan_df["customer_id"].str.contains("@").mean() > 0.9


def test_summary_shape(milan_df):
    s = summary(milan_df)
    assert s["customers"] > 0 and s["orders"] > 0
    assert s["revenue"] > 0 and s["avg_order_value"] > 0
    assert s["date_from"] <= s["date_to"]
