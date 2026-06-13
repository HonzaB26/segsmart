"""Tests for seg.products — offline, no Ollama, no external data."""
import pandas as pd
import pytest

from seg.products import _heuristic_category, assign_categories, product_mix


# ── heuristic categoriser ──────────────────────────────────────────────────

def test_heuristic_returns_capitalized_first_word():
    assert _heuristic_category("laptop stand adjustable") == "Laptop"


def test_heuristic_skips_short_and_stop_words():
    # "A" < 3 chars; "pro" is a stop word → returns next meaningful word
    assert _heuristic_category("a pro display") == "Display"


def test_heuristic_empty_string_returns_other():
    assert _heuristic_category("") == "Other"


def test_heuristic_digits_only_skipped():
    # "100" is a digit → skipped; "Kč" has only 2 chars → skipped; next word wins
    assert _heuristic_category("100 Kč dárkový poukaz") == "Dárkový"


# ── assign_categories ─────────────────────────────────────────────────────

def _make_df(products, categories=None):
    """Minimal canonical-ish frame for testing."""
    data = {"customer_id": ["c1"] * len(products),
            "product": products,
            "line_value": [10.0] * len(products)}
    if categories is not None:
        data["category"] = categories
    return pd.DataFrame(data)


def test_assign_categories_uses_existing_column():
    df = _make_df(["Widget A", "Widget B"], categories=["Electronics", "Electronics"])
    result = assign_categories(df, use_llm=False)
    assert list(result) == ["Electronics", "Electronics"]


def test_assign_categories_replaces_empty_with_other():
    df = _make_df(["Widget"], categories=[""])
    result = assign_categories(df, use_llm=False)
    assert result.iloc[0] == "Other"


def test_assign_categories_heuristic_no_llm():
    df = _make_df(["Coffee mug", "Tea pot"])
    result = assign_categories(df, use_llm=False)
    assert result.iloc[0] == "Coffee"
    assert result.iloc[1] == "Tea"


def test_assign_categories_length_matches_df():
    df = _make_df(["A product", "B product", "C product"])
    result = assign_categories(df, use_llm=False)
    assert len(result) == 3


# ── product_mix ────────────────────────────────────────────────────────────

@pytest.fixture
def mix_df():
    """Minimal canonical frame: 3 customers, 2 product categories."""
    return pd.DataFrame({
        "customer_id": ["c1", "c1", "c2", "c3", "c3"],
        "order_id":    ["o1", "o1", "o2", "o3", "o3"],
        "order_date":  pd.to_datetime(["2025-01-01"] * 5),
        "quantity":    [1.0] * 5,
        "unit_price":  [50.0, 30.0, 20.0, 80.0, 10.0],
        "line_value":  [50.0, 30.0, 20.0, 80.0, 10.0],
        "product":     ["Laptop stand", "Laptop bag", "Coffee mug", "Laptop stand", "Coffee mug"],
        "country":     ["CZ"] * 5,
    })


@pytest.fixture
def mix_feat():
    """Matching feature frame with segment labels."""
    return pd.DataFrame({
        "customer_id": ["c1", "c2", "c3"],
        "segment":     ["Champions", "New", "Loyal"],
        "recency":     [5, 10, 7],
        "frequency":   [3, 1, 2],
        "monetary":    [80.0, 20.0, 90.0],
    })


def test_product_mix_returns_list(mix_df, mix_feat):
    result = product_mix(mix_df, mix_feat, use_llm=False)
    assert isinstance(result, list)
    assert len(result) > 0


def test_product_mix_record_fields(mix_df, mix_feat):
    result = product_mix(mix_df, mix_feat, use_llm=False)
    for rec in result:
        assert {"segment", "category", "revenue", "customers"} <= rec.keys()
        assert isinstance(rec["revenue"], float)
        assert isinstance(rec["customers"], int)
        assert rec["revenue"] > 0
        assert rec["customers"] > 0


def test_product_mix_revenue_sums_match(mix_df, mix_feat):
    result = product_mix(mix_df, mix_feat, use_llm=False)
    total = sum(r["revenue"] for r in result)
    # total from cross-tab must match total from df (all products in top_n)
    assert abs(total - mix_df["line_value"].sum()) < 0.01


def test_product_mix_empty_df_returns_empty(mix_feat):
    empty = pd.DataFrame(columns=["customer_id", "product", "line_value",
                                   "order_id", "order_date", "quantity",
                                   "unit_price", "country"])
    assert product_mix(empty, mix_feat, use_llm=False) == []


def test_product_mix_no_product_column(mix_feat):
    df = pd.DataFrame({"customer_id": ["c1"], "line_value": [10.0]})
    assert product_mix(df, mix_feat, use_llm=False) == []


def test_product_mix_top_n_limits_categories(mix_df, mix_feat):
    # top_n=1 should return only one category across all segments
    result = product_mix(mix_df, mix_feat, use_llm=False, top_n=1)
    cats = {r["category"] for r in result}
    assert len(cats) == 1


def test_product_mix_sorted(mix_df, mix_feat):
    result = product_mix(mix_df, mix_feat, use_llm=False)
    keys = [(r["segment"], r["category"]) for r in result]
    assert keys == sorted(keys)


def test_product_mix_with_synth_data(feat):
    """Smoke test on the full synthetic fixture — must not crash."""
    from seg.loader import load_eshop
    import os
    # feat fixture already loaded from synth_csv (session scope in conftest)
    # we need the raw df; reload from the same path isn't possible here, so
    # construct a minimal df from feat to avoid fixture dependency on synth_csv path
    df = pd.DataFrame({
        "customer_id": feat["customer_id"].tolist() * 2,
        "order_id":    [f"o{i}" for i in range(len(feat) * 2)],
        "order_date":  pd.to_datetime(["2025-06-01"] * len(feat) * 2),
        "quantity":    [1.0] * len(feat) * 2,
        "unit_price":  feat["monetary"].tolist() + feat["avg_order_value"].tolist(),
        "line_value":  feat["monetary"].tolist() + feat["avg_order_value"].tolist(),
        "product":     (["Electronics item"] * len(feat) +
                        ["Clothing item"] * len(feat)),
        "country":     ["CZ"] * len(feat) * 2,
    })
    result = product_mix(df, feat, use_llm=False)
    assert isinstance(result, list)
