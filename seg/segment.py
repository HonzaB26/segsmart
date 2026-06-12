"""Two segmentation algorithms + their agreement.

1. RFM quantile scoring  -> interpretable, rule-named segments (the product's
   default; an SME owner can read *why* a customer is 'At-risk').
2. KMeans on scaled behavioral features -> unsupervised cross-check.

We report both, plus how much they agree (adjusted Rand index) — that
comparison is the 'different models & algorithms' + validation material.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

# The 5 dashboard segments (match the SegSmart mockup donut).
SEGMENTS = ["Champions", "Loyal", "At-risk", "New", "Dormant"]


def _score(series: pd.Series, reverse=False) -> pd.Series:
    """Quintile 1..5. reverse=True for recency (fewer days = better)."""
    try:
        q = pd.qcut(series.rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
    except ValueError:
        q = pd.qcut(series.rank(method="first"), 5, labels=False, duplicates="drop") + 1
    q = q.astype(int)
    return (6 - q) if reverse else q


def rfm_segments(feat: pd.DataFrame) -> pd.DataFrame:
    """Add R/F/M scores and a named 5-bucket `segment` column."""
    f = feat.copy()
    f["R"] = _score(f["recency"], reverse=True)   # recent -> 5
    f["F"] = _score(f["frequency"])
    f["M"] = _score(f["monetary"])

    young = f["tenure_days"] <= 30                  # first & last order within a month

    def assign(r):
        R, F = r["R"], r["F"]
        if R >= 4 and F >= 4:
            return "Champions"
        if F >= 4 or (R >= 3 and F >= 3):
            return "Loyal"
        if R >= 4 and F <= 2 and (r["tenure_days"] <= 30):
            return "New"
        if R <= 2 and F >= 3:
            return "At-risk"          # were frequent, gone quiet -> win-back
        if R <= 2:
            return "Dormant"
        # middling recency, low frequency: recent-ish newcomers vs fading
        return "New" if R >= 4 else "At-risk"

    f["segment"] = f.apply(assign, axis=1)
    f["rfm_cell"] = f["R"].astype(str) + f["F"].astype(str) + f["M"].astype(str)
    return f


def kmeans_segments(feat: pd.DataFrame, k=5, seed=42):
    """Unsupervised clustering on scaled features. Returns (labels, silhouette, model)."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from seg.features import model_matrix
    X, cols = model_matrix(feat)
    km = KMeans(n_clusters=k, random_state=seed, n_init=10).fit(X)
    sil = float(silhouette_score(X, km.labels_, sample_size=min(3000, len(X)), random_state=seed))
    return km.labels_, sil, km


def agreement(rfm_labels, km_labels) -> float:
    """Adjusted Rand index between the two segmentations (1 = identical structure)."""
    from sklearn.metrics import adjusted_rand_score
    return float(adjusted_rand_score(rfm_labels, km_labels))


def segment_profiles(f: pd.DataFrame) -> pd.DataFrame:
    """Per-segment aggregate profile — the rows behind the donut + revenue bars."""
    prof = f.groupby("segment").agg(
        customers=("customer_id", "count"),
        revenue=("monetary", "sum"),
        avg_recency=("recency", "mean"),
        avg_frequency=("frequency", "mean"),
        avg_monetary=("monetary", "mean"),
        avg_order_value=("avg_order_value", "mean"),
    )
    prof["share_pct"] = (prof["customers"] / prof["customers"].sum() * 100).round(1)
    prof["rev_share_pct"] = (prof["revenue"] / prof["revenue"].sum() * 100).round(1)
    prof = prof.reindex([s for s in SEGMENTS if s in prof.index])
    return prof.round(1)


if __name__ == "__main__":
    from seg.loader import load_uci
    from seg.features import build_features
    f = rfm_segments(build_features(load_uci()))
    km, sil, _ = kmeans_segments(f)
    print("=== RFM segment profiles ===")
    print(segment_profiles(f).to_string())
    print(f"\nKMeans silhouette: {sil:.3f}")
    print(f"RFM vs KMeans agreement (ARI): {agreement(f['segment'], km):.3f}")
