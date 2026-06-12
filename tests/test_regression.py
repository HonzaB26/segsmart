"""Regression tests pinning the fixes from the GPT-5.5 code review."""
import numpy as np
import pandas as pd
import pytest

from seg.loader import _to_num, load_dataframe
from seg.segment import _score, rfm_segments, kmeans_segments
from seg.features import build_features
from seg.util import extract_json, NoValidData
from seg.connectors import _assert_readonly


# --- RFM ties: equal values must get the SAME score (was row-order split) ---
def test_score_is_tie_aware():
    s = pd.Series([1, 1, 1, 1, 5, 5, 9, 9, 9, 100])
    sc = _score(s)
    # all the 1s share a score; all the 9s share a score
    assert sc[s == 1].nunique() == 1
    assert sc[s == 9].nunique() == 1
    assert sc.between(1, 5).all()


def test_score_degenerate_column():
    sc = _score(pd.Series([7, 7, 7, 7]))      # all identical -> neutral, no crash
    assert sc.between(1, 5).all()
    assert sc.nunique() == 1


# --- currency / separator parsing ---
@pytest.mark.parametrize("raw,dec,expected", [
    ("€1.234,56", ",", 1234.56),
    ("1 000,00", ",", 1000.0),
    ("$1,234.56", ".", 1234.56),
    ("9.90", ".", 9.90),
    ("Kč 49,90", ",", 49.90),
])
def test_to_num_currency_and_separators(raw, dec, expected):
    assert abs(_to_num(pd.Series([raw]), decimal=dec).iloc[0] - expected) < 1e-6


# --- small / degenerate datasets ---
def _toy(n):
    return pd.DataFrame({
        "customer_id": [f"c{i}" for i in range(n)],
        "order_id": [str(i) for i in range(n)],
        "order_date": pd.to_datetime(["2025-01-01"] * n),
        "quantity": [1] * n, "unit_price": [10.0] * n,
        "line_value": [10.0] * n, "product": ["x"] * n, "country": ["CZ"] * n,
    })


def test_kmeans_tiny_data_no_crash():
    feat = rfm_segments(build_features(_toy(2)))
    labels, sil, km = kmeans_segments(feat, k=5)   # k auto-clamped below n
    assert len(labels) == 2
    assert sil is None                              # silhouette undefined for n<3


def test_kmeans_clamps_k_below_n():
    feat = build_features(_toy(4))
    labels, sil, km = kmeans_segments(feat, k=5)
    assert len(set(labels)) <= 3                    # k <= n-1


def test_pipeline_rejects_too_few_customers(tmp_path):
    import pipeline
    p = tmp_path / "one.csv"
    p.write_text("customer_id,order_id,order_date,quantity,unit_price\n"
                 "a,1,2025-01-01,1,10\n")
    with pytest.raises(NoValidData):
        pipeline.run(source="csv", path=str(p), use_llm=False, out=None)


def test_load_dataframe_empty_raises_or_empty():
    empty = pd.DataFrame({"customer_id": [], "order_id": [], "order_date": [],
                          "quantity": [], "unit_price": []})
    out = load_dataframe(empty, {"customer_id": "customer_id"})
    assert out.empty


# --- is_product: coupons/gifts don't inflate product diversity ---
def test_is_product_excludes_coupons():
    df = pd.DataFrame({
        "customer_id": ["a", "a", "a"],
        "order_id": ["1", "1", "1"],
        "order_date": pd.to_datetime(["2025-01-01"] * 3),
        "quantity": [1, 1, 1], "unit_price": [10.0, 20.0, -5.0],
        "line_value": [10.0, 20.0, -5.0],
        "product": ["SKU1", "SKU2", "COUPON9"],
        "country": ["CZ"] * 3,
        "is_product": [True, True, False],
    })
    f = build_features(df).set_index("customer_id")
    assert f.loc["a", "distinct_products"] == 2     # coupon not counted


# --- shared JSON extractor ---
@pytest.mark.parametrize("raw", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    'Sure, here it is:\n{"a": 1}\nhope that helps',
    '<think>reasoning...</think>\n{"a": 1}',
])
def test_extract_json_variants(raw):
    assert extract_json(raw)["a"] == 1


def test_extract_json_empty_raises():
    with pytest.raises(ValueError):
        extract_json("")


# --- connector read-only guard ---
@pytest.mark.parametrize("q", [
    "DROP TABLE orders", "DELETE FROM orders", "SELECT 1; DROP TABLE x",
    "UPDATE orders SET x=1", "INSERT INTO orders VALUES (1)",
])
def test_connector_rejects_writes(q):
    with pytest.raises(ValueError):
        _assert_readonly(q)


def test_connector_allows_select():
    _assert_readonly("SELECT * FROM order_lines")
    _assert_readonly("WITH t AS (SELECT 1) SELECT * FROM t")


# --- segment rules now use M (Champions must be high-spend) ---
def test_champions_require_high_monetary(feat):
    champs = feat[feat["segment"] == "Champions"]
    if len(champs):
        assert (champs["M"] >= 4).all()
