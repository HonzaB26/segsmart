from seg.segment import (rfm_segments, kmeans_segments, agreement,
                         segment_profiles, SEGMENTS)


def test_segments_are_named(feat):
    assert set(feat["segment"]).issubset(set(SEGMENTS))


def test_rfm_scores_in_range(feat):
    for c in ("R", "F", "M"):
        assert feat[c].between(1, 5).all()


def test_profiles_shares_sum_100(feat):
    prof = segment_profiles(feat)
    assert abs(prof["share_pct"].sum() - 100) < 1.0
    assert abs(prof["rev_share_pct"].sum() - 100) < 1.0
    assert list(prof.index) == [s for s in SEGMENTS if s in prof.index]


def test_champions_are_recent_and_frequent(feat):
    prof = segment_profiles(feat)
    if "Champions" in prof.index and "Dormant" in prof.index:
        # champions buy more often and more recently than dormant
        assert prof.loc["Champions", "avg_frequency"] > prof.loc["Dormant", "avg_frequency"]
        assert prof.loc["Champions", "avg_recency"] < prof.loc["Dormant", "avg_recency"]


def test_kmeans_and_agreement(feat):
    labels, sil, km = kmeans_segments(feat, k=5)
    assert len(labels) == len(feat)
    assert -1.0 <= sil <= 1.0
    ari = agreement(feat["segment"], labels)
    assert -1.0 <= ari <= 1.0
