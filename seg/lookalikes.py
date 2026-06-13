"""Lookalikes — cosine nearest-neighbours on the scaled RFM feature space.

The product answer to "find me more customers like my best ones". Uses the
exact six features KMeans already runs on (seg.features.model_matrix), L2-
normalised for cosine. No new dependencies, no training, milliseconds.

(A 2D-attention / SAINT-lite encoder was tried first — see the experiment on
the experiment/2d-attention branch — and lost to this on both clustering and
lookalike quality while dragging in torch. Simple won.)

  lookalikes(feat, id)              -> the k customers most like this one
  expand_segment(feat, "Champions") -> non-members ranked by similarity to the
                                       segment's centre ("nudge them up" list)
"""
from __future__ import annotations
import numpy as np
import pandas as pd

_COLS = ["customer_id", "recency", "frequency", "monetary", "avg_order_value", "segment"]


def _unit_space(feat: pd.DataFrame) -> np.ndarray:
    """Scaled feature matrix (as KMeans sees it), row-normalised for cosine."""
    from seg.features import model_matrix
    X, _ = model_matrix(feat)
    return X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)


def _frame(feat: pd.DataFrame, rows: np.ndarray, sims: np.ndarray) -> pd.DataFrame:
    cols = [c for c in _COLS if c in feat.columns]
    out = feat.iloc[rows][cols].copy()
    out.insert(1, "similarity", np.round(sims, 3))
    return out.reset_index(drop=True)


def lookalikes(feat: pd.DataFrame, customer_id, k: int = 8) -> pd.DataFrame:
    """The k customers most similar to `customer_id` by cosine on scaled RFM."""
    pos = feat.index[feat["customer_id"] == customer_id]
    if len(pos) == 0:
        raise KeyError(f"unknown customer_id {customer_id!r}")
    Xn = _unit_space(feat)
    a = feat.index.get_loc(pos[0])
    sims = Xn @ Xn[a]
    sims[a] = -1.0                                  # never return self
    k = max(0, min(k, len(feat) - 1))
    top = np.argsort(-sims)[:k]
    return _frame(feat, top, sims[top])


def expand_segment(feat: pd.DataFrame, segment: str = "Champions",
                   k: int = 15) -> pd.DataFrame:
    """Customers NOT in `segment`, ranked by mean cosine similarity to the
    segment's members. The 'find more like my best' / 'nudge them up' list.
    Returns an empty frame when the segment has no members."""
    mask = (feat["segment"] == segment).to_numpy()
    if not mask.any():
        return _frame(feat, np.array([], dtype=int), np.array([]))
    Xn = _unit_space(feat)
    sims = Xn @ Xn[mask].mean(0)                    # similarity to the seed centre
    sims[mask] = -1.0                               # exclude existing members
    k = max(0, min(k, int((~mask).sum())))
    top = np.argsort(-sims)[:k]
    return _frame(feat, top, sims[top])
