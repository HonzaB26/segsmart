"""Seasonality detection — the 'seasonality-aware' pillar.

Monthly revenue curve + which months over/under-index vs the average month.
Feeds the dashboard 'Revenue over time' view and gives the campaign layer a
concrete hook ('December runs +X% — plan a winter push now').
"""
from __future__ import annotations
import pandas as pd


def monthly_curve(df: pd.DataFrame) -> pd.DataFrame:
    """Revenue & order count per calendar month of the data window."""
    m = df.set_index("order_date").resample("MS").agg(
        revenue=("line_value", "sum"),
        orders=("order_id", "nunique"),
    ).reset_index()
    m["month"] = m["order_date"].dt.strftime("%Y-%m")
    return m


def seasonality_index(df: pd.DataFrame) -> pd.DataFrame:
    """Average revenue by month-of-year, indexed to 100 = mean month.

    >100 = that month over-performs. The peak month is the campaign hook.
    """
    g = df.copy()
    g["moy"] = g["order_date"].dt.month
    g["day"] = g["order_date"].dt.normalize()
    rev = g.groupby("moy").line_value.sum()
    # normalise by DAYS OBSERVED in each month-of-year — so a truncated month
    # (e.g. data ending mid-December) is compared as revenue/day, not deflated.
    days = g.groupby("moy").day.nunique()
    avg_rev = rev / days
    idx = (avg_rev / avg_rev.mean() * 100).round(0)
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    out = pd.DataFrame({"month_num": idx.index,
                        "month": [names[i - 1] for i in idx.index],
                        "index": idx.values.astype(int)})
    return out.sort_values("month_num").reset_index(drop=True)


def peak_hook(df: pd.DataFrame) -> dict:
    """The single biggest seasonal opportunity, as a campaign-ready fact."""
    si = seasonality_index(df)
    top = si.loc[si["index"].idxmax()]
    low = si.loc[si["index"].idxmin()]
    return {
        "peak_month": top["month"], "peak_index": int(top["index"]),
        "peak_uplift_pct": int(top["index"] - 100),
        "low_month": low["month"], "low_index": int(low["index"]),
    }


if __name__ == "__main__":
    from seg.loader import load_uci
    d = load_uci()
    print(seasonality_index(d).to_string(index=False))
    print("\nhook:", peak_hook(d))
