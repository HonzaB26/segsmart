"""SegSmart pipeline — data -> features -> segments -> seasonality -> AI campaigns.

Runs entirely locally. Emits one out/result.json the dashboard renders.
Swap the loader call to point at a real export; nothing else changes.
"""
from __future__ import annotations
import json, os, time
import pandas as pd

from seg.loader import load_uci, load_csv, load_milan, summary
from seg.util import NoValidData
from seg.features import build_features
from seg.segment import (rfm_segments, kmeans_segments, agreement,
                         segment_profiles, SEGMENTS)
from seg.seasonality import monthly_curve, seasonality_index, peak_hook
from seg.campaigns import all_cards

# segment -> dashboard colour (matches the SegSmart mockup donut)
COLORS = {"Champions": "#2dd4bf", "Loyal": "#a78bfa", "At-risk": "#f59e0b",
          "New": "#84cc16", "Dormant": "#f87171"}


def run(source="uci", path=None, currency="£", use_llm=True, out="out/result.json",
        mapping=None, lang=None, decimal="."):
    if source == "uci":
        df = load_uci(path or "data/online_retail.parquet")
    elif source == "milan":
        df = load_milan(path or "data/bq_export.csv")
    else:
        df = load_csv(path, mapping, decimal=decimal)
    return analyze(df, currency=currency, use_llm=use_llm, out=out,
                   lang=lang, source_label=source)


def run_config(cfg=None, out="out/result.json"):
    """Run from the local config file (config/segsmart.json) — the configured
    data source IS this installation's data, so the result persists and the
    dashboard serves it on next load."""
    from seg import config as cfgmod
    cfg = cfg if cfg is not None else cfgmod.load_config()
    src = cfg.get("source") or {}
    if not src:
        raise NoValidData("no data source configured — open /setup or edit "
                          f"{cfgmod.CONFIG_PATH}")
    df = cfgmod.fetch_dataframe(src)
    o, ai = cfg.get("output", {}), cfg.get("ai", {})
    return analyze(df, currency=o.get("currency", "£"),
                   use_llm=ai.get("use_llm", True), out=out,
                   lang=o.get("language"), source_label=src.get("type"))


def analyze(df, currency="£", use_llm=True, out="out/result.json",
            lang=None, source_label=None):
    t0 = time.time()
    if lang is None:
        lang = "cs" if source_label == "milan" or currency == "Kč" else "en"

    if df is None or df.empty:
        raise NoValidData("no usable rows after cleaning — check the file or the column mapping")
    if df["customer_id"].nunique() < 2:
        raise NoValidData("need at least 2 distinct customers to segment")

    meta = summary(df)
    meta["currency"] = currency
    meta["source"] = source_label or "upload"

    feat = rfm_segments(build_features(df))
    km_labels, sil, _ = kmeans_segments(feat)
    ari = agreement(feat["segment"], km_labels)
    prof = segment_profiles(feat)

    # KPIs (the four mockup cards)
    repeat_rate = round(float((feat["frequency"] > 1).mean()) * 100, 1)
    kpis = {
        "total_customers": meta["customers"],
        "repeat_rate_pct": repeat_rate,         # share buying more than once
        "avg_order_value": meta["avg_order_value"],
        "revenue": meta["revenue"],
    }

    # seasonality
    curve = monthly_curve(df)
    sidx = seasonality_index(df)
    hook = peak_hook(df)

    # AI campaign cards (local LLM)
    cards = all_cards(prof, hook, use_llm=use_llm, currency=currency, lang=lang)
    kpis["ai_campaigns"] = len(cards)

    segments = []
    for name, row in prof.iterrows():
        segments.append({
            "name": name, "color": COLORS.get(name, "#888"),
            "customers": int(row["customers"]), "share_pct": float(row["share_pct"]),
            "revenue": float(row["revenue"]), "rev_share_pct": float(row["rev_share_pct"]),
            "avg_recency": float(row["avg_recency"]), "avg_frequency": float(row["avg_frequency"]),
            "avg_monetary": float(row["avg_monetary"]), "avg_order_value": float(row["avg_order_value"]),
        })

    result = {
        "meta": meta,
        "kpis": kpis,
        "segments": segments,
        "seasonality": {
            "index": sidx.to_dict("records"),
            "curve": [{"month": r["month"], "revenue": round(float(r["revenue"]), 2),
                       "orders": int(r["orders"])} for _, r in curve.iterrows()],
            "hook": hook,
        },
        "campaigns": cards,
        "validation": {
            "kmeans_silhouette": round(sil, 3) if sil is not None else None,
            "rfm_kmeans_ari": round(ari, 3),
            "n_customers": meta["customers"],
            "segment_sizes": {s["name"]: s["customers"] for s in segments},
        },
        "runtime_secs": round(time.time() - t0, 1),
    }

    # out=None -> don't persist (used for uploaded customer data, which must not
    # be written to the shared demo file on disk)
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"computed in {result['runtime_secs']}s "
          f"({'LLM' if use_llm else 'no-LLM'}, {meta['customers']} customers)"
          + (f", wrote {out}" if out else ", not persisted"))
    return result


if __name__ == "__main__":
    import sys
    if "--config" in sys.argv:
        run_config()            # uses config/segsmart.json (or $SEG_CONFIG)
    else:
        run(use_llm="--no-llm" not in sys.argv)
