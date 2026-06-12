"""External factors layer — owner-supplied signals that may move sales.

The ONLY way external data enters the tool is via manual CSV upload
(`/api/external_impact`). No automatic network fetch ever happens here —
the analytics path must stay offline (AGENTS.md rule 2).

What this module does:
  - `daily_sales`     : collapse the canonical frame to a daily revenue series
                        (date + revenue + orders, no PII).  Stored in result.json
                        so an uploaded CSV can be scored without re-reading
                        customer data.
  - `load_external_csv`: parse the owner's daily CSV into a date-indexed frame.
                        Any columns are accepted: numeric factors (ad spend,
                        temperature, exchange rate…) or 0/1 flag columns
                        (promo day, holiday, …).
  - `factor_impact`   : measure how each uploaded factor moves daily revenue.
                        Binary flags → % revenue lift on 'on' days vs 'off' days.
                        Numeric factors → Pearson correlation with daily revenue.
                        All numbers are computed deterministically — never by an LLM.
  - `impact_from_daily`: the public entry point used by the server endpoint —
                        joins an uploaded CSV onto the persisted daily-revenue
                        series and returns the impact summary.
"""
from __future__ import annotations
import io

import numpy as np
import pandas as pd


# columns that are identifiers / labels, never treated as impact factors
_SKIP = {"date", "revenue", "orders"}


def daily_sales(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse the canonical order-line frame to date-level revenue + orders.

    The result contains no customer identifiers — it is safe to persist
    alongside result.json and read back without touching customer data.
    """
    g = df.copy()
    g["date"] = pd.to_datetime(g["order_date"]).dt.normalize()
    return (g.groupby("date")
             .agg(revenue=("line_value", "sum"),
                  orders=("order_id", "nunique"))
             .reset_index())


def load_external_csv(path_or_text: str, date_col: str | None = None,
                      is_text: bool = False) -> pd.DataFrame:
    """Parse an owner-uploaded daily CSV into a date-indexed factor frame.

    The manual-upload seam: any CSV with a date column + one or more factor
    columns (numeric or 0/1 flags). The date column is auto-detected when
    not specified. Non-numeric columns are kept as-is (e.g. an event_name).
    """
    src = io.StringIO(path_or_text) if is_text else path_or_text
    raw = pd.read_csv(src)
    if raw.empty:
        return pd.DataFrame()
    if date_col is None:
        date_col = next(
            (c for c in raw.columns
             if any(k in c.lower() for k in ("date", "datum", "day", "den"))),
            raw.columns[0],
        )
    out = pd.DataFrame({"date": pd.to_datetime(raw[date_col], errors="coerce")})
    out = out.dropna(subset=["date"])
    out["date"] = out["date"].dt.normalize()
    for c in raw.columns:
        if c == date_col:
            continue
        num = pd.to_numeric(raw[c], errors="coerce")
        out[c] = num if num.notna().mean() >= 0.5 else raw[c]
    return out.groupby("date", as_index=False).first()


def combine(sales: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    """Left-join daily sales with a factor frame on date."""
    return sales.merge(factors, on="date", how="left")


def factor_impact(daily: pd.DataFrame, min_days: int = 30) -> list[dict]:
    """Quantify each factor column's relationship to daily revenue.

    Binary flags (0/1):  % revenue lift on 'on' days vs 'off' days.
    Numeric factors:     Pearson correlation with daily revenue.

    Returns a list sorted by absolute effect size (strongest driver first).
    """
    if len(daily) < min_days:
        return []
    rev = daily["revenue"].astype(float)
    base = float(rev.mean())
    out = []
    for col in daily.columns:
        if col in _SKIP:
            continue
        vals = pd.to_numeric(daily[col], errors="coerce")
        if vals.notna().sum() < min_days:
            continue
        uniq = set(vals.dropna().unique())
        if uniq <= {0, 1} and len(uniq) == 2:          # binary flag → lift
            on = rev[vals == 1]
            off = rev[vals == 0]
            if len(on) < 3 or len(off) < 3 or base == 0:
                continue
            lift = (float(on.mean()) - float(off.mean())) / base * 100
            out.append({
                "factor": col, "type": "flag",
                "effect_pct": round(lift, 1),
                "on_days": int(len(on)),
                "avg_on": round(float(on.mean()), 2),
                "avg_off": round(float(off.mean()), 2),
                "direction": "raises" if lift >= 0 else "lowers",
            })
        else:                                           # numeric → correlation
            mask = vals.notna()
            if mask.sum() < min_days or float(vals[mask].std()) == 0:
                continue
            r = float(np.corrcoef(vals[mask], rev[mask])[0, 1])
            if np.isnan(r):
                continue
            out.append({
                "factor": col, "type": "numeric",
                "correlation": round(r, 3),
                "direction": "raises" if r >= 0 else "lowers",
                "strength": ("strong" if abs(r) >= 0.5 else
                             "moderate" if abs(r) >= 0.3 else "weak"),
            })
    out.sort(key=lambda d: -abs(d.get("effect_pct", (d.get("correlation") or 0) * 100)))
    return out


def impact_from_daily(daily_records: list[dict], uploaded_csv: str,
                      uploaded_is_text: bool = True) -> dict:
    """Score an uploaded external CSV against a persisted daily-revenue series.

    Privacy-preserving: only the daily revenue aggregate (no customer data)
    is required. Called by /api/external_impact in server.py.
    """
    sales = pd.DataFrame(daily_records)
    if sales.empty:
        return {"impact": [], "factors": [], "n_days": 0, "matched_days": 0}
    sales["date"] = pd.to_datetime(sales["date"]).dt.normalize()
    factors = load_external_csv(uploaded_csv, is_text=uploaded_is_text)
    if factors.empty:
        return {
            "impact": [], "factors": [], "n_days": int(len(sales)),
            "matched_days": 0,
            "error": "no usable rows (need a date column + at least one factor column)",
        }
    daily = combine(sales, factors)
    factor_cols = [c for c in factors.columns if c != "date"]
    matched = int(daily[factor_cols].notna().any(axis=1).sum()) if factor_cols else 0
    return {
        "impact": factor_impact(daily),
        "factors": factor_cols,
        "n_days": int(len(sales)),
        "matched_days": matched,
    }
