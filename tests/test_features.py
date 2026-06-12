import numpy as np
from seg.features import build_features, model_matrix, MODEL_FEATURES


def test_one_row_per_customer(milan_df):
    f = build_features(milan_df)
    assert f["customer_id"].is_unique
    assert len(f) == milan_df["customer_id"].nunique()


def test_rfm_definitions(milan_df):
    f = build_features(milan_df).set_index("customer_id")
    # recency positive (snapshot = day after last order)
    assert (f["recency"] >= 1).all()
    # frequency = distinct orders per customer
    orders = milan_df.groupby("customer_id")["order_id"].nunique()
    assert (f["frequency"] == orders.reindex(f.index)).all()
    # monetary = total net spend
    assert (f["monetary"] > 0).any()


def test_model_matrix_shape_and_finite(milan_df):
    f = build_features(milan_df)
    X, cols = model_matrix(f)
    assert cols == MODEL_FEATURES
    assert X.shape == (len(f), len(MODEL_FEATURES))
    assert np.isfinite(X).all()        # log1p + scaler, no NaN/inf


def test_interpurchase_nan_for_single_order(milan_df):
    f = build_features(milan_df)
    singles = f[f["frequency"] == 1]
    assert singles["interpurchase_days"].isna().all()
