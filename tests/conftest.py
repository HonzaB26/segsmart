"""Shared fixtures. Tests are self-contained: they synthesize their own small
dataset and never need Ollama or the large UCI download."""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def synth_csv(tmp_path_factory):
    """A small synthetic dataset in the partner shop's schema, written once per session."""
    from gen.synth import generate
    out = tmp_path_factory.mktemp("data") / "synth.csv"
    generate(n_customers=200, seed=1, out=str(out))
    return str(out)


@pytest.fixture(scope="session")
def eshop_df(synth_csv):
    from seg.loader import load_eshop
    return load_eshop(synth_csv)


@pytest.fixture(scope="session")
def feat(eshop_df):
    from seg.features import build_features
    from seg.segment import rfm_segments
    return rfm_segments(build_features(eshop_df))


@pytest.fixture
def generic_df():
    """A tiny clean canonical-ish frame for loader/connector tests."""
    return pd.DataFrame({
        "cust": ["a", "a", "b", "c", "c"],
        "ord": ["1", "1", "2", "3", "4"],
        "when": ["2025-01-01", "2025-01-01", "2025-02-01", "2025-03-01", "2025-03-15"],
        "qty": ["2", "1", "3", "1", "2"],
        "price": ["10,50", "5,00", "7,00", "100,00", "9,90"],   # european decimals
        "item": ["X", "Y", "Z", "W", "X"],
    })
