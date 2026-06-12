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
    """Tie-aware quintile 1..5: equal values always get the SAME score (no
    arbitrary row-order splitting). Degenerate columns collapse to a neutral 3.
    reverse=True for recency (fewer days = better)."""
    pct = series.rank(pct=True, method="average")     # ties share a percentile
    q = np.ceil(pct * 5).clip(1, 5).astype(int)
    return (6 - q) if reverse else q


def rfm_segments(feat: pd.DataFrame) -> pd.DataFrame:
    """Add R/F/M scores and a named 5-bucket `segment` column.

    Rules use all three of R/F/M plus tenure, in priority order:
      Champions  recent + frequent + high-spend
      Loyal      frequent OR high-spend, still reasonably recent
      New        recent, few orders, short tenure (genuinely new)
      At-risk    lapsed but was frequent/valuable (win-back)
      Dormant    lapsed and low value
    """
    f = feat.copy()
    f["R"] = _score(f["recency"], reverse=True)   # recent -> 5
    f["F"] = _score(f["frequency"])
    f["M"] = _score(f["monetary"])

    def assign(r):
        R, F, M, tenure = r["R"], r["F"], r["M"], r["tenure_days"]
        if R >= 4 and F >= 4 and M >= 4:
            return "Champions"
        if R >= 3 and (F >= 4 or M >= 4):
            return "Loyal"
        if R >= 4 and F <= 2 and tenure <= 60:
            return "New"
        if R <= 2 and (F >= 3 or M >= 3):
            return "At-risk"          # were frequent/valuable, gone quiet
        if R <= 2:
            return "Dormant"
        # middle band (R==3, modest F/M): newcomer settling in vs steady regular
        return "New" if (F <= 2 and tenure <= 90) else "Loyal"

    f["segment"] = f.apply(assign, axis=1)
    f["rfm_cell"] = f["R"].astype(str) + f["F"].astype(str) + f["M"].astype(str)
    return f


def kmeans_segments(feat: pd.DataFrame, k=5, seed=42):
    """Unsupervised clustering on scaled features. Returns (labels, silhouette, model).
    Adapts k to small datasets and returns silhouette=None when it's undefined."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from seg.features import model_matrix
    n = len(feat)
    X, cols = model_matrix(feat)
    if n < 3:                                          # too few to cluster meaningfully
        return np.zeros(n, dtype=int), None, None
    k = max(2, min(k, n - 1))                          # k must be < n
    km = KMeans(n_clusters=k, random_state=seed, n_init=10).fit(X)
    n_clusters = len(set(km.labels_))
    sil = (float(silhouette_score(X, km.labels_, sample_size=min(3000, n),
                                  random_state=seed))
           if 2 <= n_clusters < n else None)           # silhouette needs 2..n-1 clusters
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
