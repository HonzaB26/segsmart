"""Lookalikes / segment-expansion (seg.lookalikes) — cosine-kNN on scaled RFM."""
import numpy as np
import pytest

from seg.lookalikes import lookalikes, expand_segment


def test_lookalikes_count_and_no_self(feat):
    anchor = feat["customer_id"].iloc[0]
    out = lookalikes(feat, anchor, k=5)
    assert len(out) == 5
    assert anchor not in set(out["customer_id"])           # never returns self
    assert "similarity" in out.columns


def test_lookalikes_sorted_descending(feat):
    out = lookalikes(feat, feat["customer_id"].iloc[0], k=8)
    s = out["similarity"].to_numpy()
    assert np.all(np.diff(s) <= 1e-9)                       # non-increasing
    assert s.max() <= 1.0 + 1e-6                            # cosine bound


def test_lookalikes_unknown_id_raises(feat):
    with pytest.raises(KeyError):
        lookalikes(feat, "definitely-not-a-customer")


def test_lookalikes_k_clamped_to_population(feat):
    out = lookalikes(feat, feat["customer_id"].iloc[0], k=10_000)
    assert len(out) == len(feat) - 1                        # everyone but self


def test_expand_excludes_members(feat):
    seg = feat["segment"].mode().iloc[0]                    # a segment that exists
    members = set(feat.loc[feat["segment"] == seg, "customer_id"])
    out = expand_segment(feat, seg, k=10)
    assert members.isdisjoint(set(out["customer_id"]))      # only non-members
    assert (out["segment"] != seg).all()


def test_expand_missing_segment_is_empty(feat):
    out = expand_segment(feat, "NoSuchSegment")
    assert len(out) == 0
    assert list(out.columns)[:2] == ["customer_id", "similarity"]


def test_expand_deterministic(feat):
    seg = feat["segment"].mode().iloc[0]
    a = expand_segment(feat, seg, k=8)["customer_id"].tolist()
    b = expand_segment(feat, seg, k=8)["customer_id"].tolist()
    assert a == b
