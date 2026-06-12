"""SegSmart pipeline — data -> features -> segments -> seasonality -> AI campaigns.

Runs entirely locally. Emits one out/result.json the dashboard renders.
Swap the loader call to point at a real export; nothing else changes.
"""
from __future__ import annotations
import json, os, time
import pandas as pd

from seg.loader import load_uci, load_csv, load_eshop, summary
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
    elif source == "eshop":
        df = load_eshop(path or "data/bq_export.csv")
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


# reasons in the ingest report that signal a parsing/mapping problem, as
# opposed to expected business filtering (cancellations, billing lines)
SUSPICIOUS_DROPS = {"unparseable date", "unparseable price", "missing customer id",
                    "missing customer / date / value"}


def _quality(df, meta, ingest, sil, ari):
    """Honesty warnings: tell the user when the data can't support the
    conclusions, instead of pretending it can."""
    w = []
    span_days = int((df["order_date"].max() - df["order_date"].min()).days)
    if span_days < 180:
        w.append({"code": "short-window", "severity": "high", "message":
                  f"The data covers only {span_days} days. Frequency-based "
                  "segmentation needs roughly 6+ months — on a short window "
                  "almost everyone looks like a one-time buyer."})
    med_order = float(df.groupby("order_id")["line_value"].sum().median())
    if med_order < 1:
        w.append({"code": "tiny-money", "severity": "high", "message":
                  f"Median order value is {med_order:.2f} — money columns were "
                  "probably parsed with the wrong decimal separator. Re-check "
                  "the mapping (decimal , vs .)."})
    if meta["customers"] < 50:
        w.append({"code": "few-customers", "severity": "medium", "message":
                  f"Only {meta['customers']} customers — quantile-based segments "
                  "are coarse below ~50; read them as rough groups."})
    if ingest and ingest.get("rows_in"):
        bad = sum(v for k, v in ingest.get("dropped", {}).items()
                  if k in SUSPICIOUS_DROPS)
        if bad / ingest["rows_in"] > 0.2:
            w.append({"code": "high-drop", "severity": "medium", "message":
                      f"{round(100 * bad / ingest['rows_in'])} % of rows were "
                      "dropped as unparseable — the column mapping or the "
                      "decimal/date format is probably off for part of the file."})
    if ari is not None and ari < 0.15 and meta["customers"] >= 50:
        w.append({"code": "weak-structure", "severity": "medium", "message":
                  f"Rule-based segments and KMeans clusters barely agree "
                  f"(ARI {ari:.2f}) — segment boundaries are uncertain. "
                  "Often a symptom of a short or sparse history."})
    return w


def _migration(out, feat, meta):
    """Compare this run's per-customer segments with the previous persisted
    run; write a new snapshot. Returns the migration summary (or None on the
    first run). Only called for persisted runs — ad-hoc uploads leave no trace."""
    hist_dir = os.path.join(os.path.dirname(out) or ".", "history")
    os.makedirs(hist_dir, exist_ok=True)
    current = dict(zip(feat["customer_id"].astype(str), feat["segment"]))
    snaps = sorted(f for f in os.listdir(hist_dir)
                   if f.startswith("segments-") and f.endswith(".json"))
    migration = None
    if snaps:
        with open(os.path.join(hist_dir, snaps[-1])) as f:
            prev = json.load(f)
        moves = {}
        for cid, seg in current.items():
            old = prev["segments"].get(cid)
            if old and old != seg:
                moves[(old, seg)] = moves.get((old, seg), 0) + 1
        migration = {
            "prev_run": prev["run_at"], "prev_data_to": prev.get("date_to"),
            "moves": sorted(({"from": a, "to": b, "customers": n}
                             for (a, b), n in moves.items()),
                            key=lambda m: -m["customers"]),
            "entered": sum(1 for c in current if c not in prev["segments"]),
            "left": sum(1 for c in prev["segments"] if c not in current),
        }
    stamp, n = time.strftime("%Y%m%d-%H%M%S"), 1
    path = os.path.join(hist_dir, f"segments-{stamp}.json")
    while os.path.exists(path):                     # same-second runs must not overwrite
        n += 1
        path = os.path.join(hist_dir, f"segments-{stamp}-{n}.json")
    with open(path, "w") as f:
        json.dump({"run_at": time.strftime("%Y-%m-%d %H:%M"),
                   "date_to": meta["date_to"], "segments": current}, f)
    return migration


def analyze(df, currency="£", use_llm=True, out="out/result.json",
            lang=None, source_label=None):
    t0 = time.time()
    if lang is None:
        lang = "cs" if source_label == "eshop" or currency == "Kč" else "en"

    if df is None or df.empty:
        raise NoValidData("no usable rows after cleaning — check the file or the column mapping")
    if df["customer_id"].nunique() < 2:
        raise NoValidData("need at least 2 distinct customers to segment")

    ingest = df.attrs.get("ingest")
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
        "quality": {
            "ingest": ingest,
            "warnings": _quality(df, meta, ingest, sil, ari),
        },
        # per-customer rows for segment drill-down + CSV export in the UI
        "customers": [
            {"id": str(r.customer_id), "segment": r.segment,
             "recency": int(r.recency), "frequency": int(r.frequency),
             "monetary": round(float(r.monetary), 2)}
            for r in feat.sort_values("monetary", ascending=False).itertuples()
        ],
        "runtime_secs": round(time.time() - t0, 1),
    }

    # out=None -> don't persist (used for uploaded customer data, which must not
    # be written to the shared demo file on disk)
    if out:
        result["migration"] = _migration(out, feat, meta)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
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
