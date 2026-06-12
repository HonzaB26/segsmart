"""Per-customer feature engineering: RFM + behavioral.

Input  : canonical line-item frame (seg.loader)
Output : one row per customer with the features segmentation runs on.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def build_features(df: pd.DataFrame, snapshot=None) -> pd.DataFrame:
    """Collapse transactions to a customer feature table.

    snapshot = the 'today' we measure recency against. Default = day after the
    last transaction (so the most recent buyer has recency = 1, not 0).
    """
    if snapshot is None:
        snapshot = df.order_date.max() + pd.Timedelta(days=1)

    order = df.groupby("order_id").agg(
        customer_id=("customer_id", "first"),
        order_date=("order_date", "first"),
        order_value=("line_value", "sum"),
        order_items=("quantity", "sum"),
    ).reset_index()

    g = order.groupby("customer_id")
    feat = pd.DataFrame({
        # --- RFM core ---
        "recency":   (snapshot - g.order_date.max()).dt.days,
        "frequency": g.order_id.nunique(),
        "monetary":  g.order_value.sum(),
        # --- behavioral ---
        "avg_order_value": g.order_value.mean(),
        "avg_basket_items": g.order_items.mean(),
        "tenure_days":     (g.order_date.max() - g.order_date.min()).dt.days,
        "first_seen":       g.order_date.min(),
        "last_seen":        g.order_date.max(),
    })

    # product diversity + dominant country need the line-item frame
    feat["distinct_products"] = df.groupby("customer_id").product.nunique()
    feat["country"] = df.groupby("customer_id").country.agg(
        lambda s: s.value_counts().idxmax())

    # avg gap between purchases (NaN -> single-order customers, fill with tenure window)
    feat["interpurchase_days"] = np.where(
        feat["frequency"] > 1,
        feat["tenure_days"] / (feat["frequency"] - 1).clip(lower=1),
        np.nan,
    )
    feat = feat.reset_index().rename(columns={"index": "customer_id"})
    return feat


# Features fed to the unsupervised model (KMeans). Log-damp the heavy tails.
MODEL_FEATURES = ["recency", "frequency", "monetary",
                  "avg_order_value", "avg_basket_items", "distinct_products"]


def model_matrix(feat: pd.DataFrame):
    """Return (X_scaled, used_columns). log1p on skewed counts/money, then z-score."""
    from sklearn.preprocessing import StandardScaler
    X = feat[MODEL_FEATURES].copy()
    for c in ["frequency", "monetary", "avg_order_value", "avg_basket_items", "distinct_products"]:
        X[c] = np.log1p(X[c].clip(lower=0))
    Xs = StandardScaler().fit_transform(X.fillna(X.median()))
    return Xs, MODEL_FEATURES


if __name__ == "__main__":
    from seg.loader import load_uci
    f = build_features(load_uci())
    print("customers:", len(f))
    print(f[["recency", "frequency", "monetary", "avg_order_value",
             "avg_basket_items", "distinct_products", "interpurchase_days"]]
          .describe().round(1).to_string())
