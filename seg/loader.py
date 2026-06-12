"""Pluggable transaction loader.

Every adapter returns the SAME canonical line-item frame, so everything
downstream (features, segmentation, campaigns, dashboard) is dataset-agnostic.
When the schoolmate hands over a real e-shop export, write one adapter here
(or a column mapping for `load_csv`) and nothing else changes.

Canonical columns
-----------------
customer_id : str    order_id : str       order_date : datetime64
quantity    : float  unit_price : float   line_value : float (= qty*price)
product     : str    country : str
"""
from __future__ import annotations
import pandas as pd

CANON = ["customer_id", "order_id", "order_date",
         "quantity", "unit_price", "line_value", "product", "country"]


def _finalize(df: pd.DataFrame, drop_cancellations=True, drop_nonpositive=True) -> pd.DataFrame:
    """Shared cleaning every adapter funnels through."""
    df = df.copy()
    df["order_date"] = pd.to_datetime(df["order_date"])
    df["customer_id"] = df["customer_id"].astype(str)
    df["order_id"] = df["order_id"].astype(str)
    df = df.dropna(subset=["customer_id", "order_date"])
    df = df[df["customer_id"].str.lower().ne("nan")]            # excel NaN -> "nan"
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["unit_price"] = pd.to_numeric(df["unit_price"], errors="coerce")
    df = df.dropna(subset=["quantity", "unit_price"])
    if drop_cancellations:
        # a cancellation/return: negative quantity, or order id flagged 'C'
        df = df[~df["order_id"].str.upper().str.startswith("C")]
        df = df[df["quantity"] > 0]
    if drop_nonpositive:
        df = df[df["unit_price"] > 0]
    df["line_value"] = df["quantity"] * df["unit_price"]
    if "product" not in df:
        df["product"] = ""
    if "country" not in df:
        df["country"] = ""
    df["product"] = df["product"].astype(str)
    df["country"] = df["country"].astype(str)
    return df[CANON].reset_index(drop=True)


def load_uci(path: str = "data/online_retail.parquet") -> pd.DataFrame:
    """UCI Online Retail (the standard benchmark dataset)."""
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_excel(path)
    df = df.rename(columns={
        "CustomerID": "customer_id", "InvoiceNo": "order_id",
        "InvoiceDate": "order_date", "Quantity": "quantity",
        "UnitPrice": "unit_price", "Description": "product", "Country": "country",
    })
    return _finalize(df)


# Generic adapter for the schoolmate's real export. Just pass a mapping
# {canonical_name: their_column_name}; line_value is derived if absent.
DEFAULT_MAP = {
    "customer_id": "customer_id", "order_id": "order_id", "order_date": "order_date",
    "quantity": "quantity", "unit_price": "unit_price",
    "product": "product", "country": "country",
}


def _to_num(s: pd.Series, decimal: str = ".") -> pd.Series:
    """Parse a possibly-stringy numeric column. decimal=',' handles European
    formats ('1 234,56' / '1.234,56' → 1234.56)."""
    if pd.api.types.is_numeric_dtype(s):
        return s
    # drop everything that isn't a digit, separator or sign (currency, spaces, letters)
    x = s.astype(str).str.replace(r"[^\d,.\-]", "", regex=True)
    if decimal == ",":
        x = x.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        x = x.str.replace(",", "", regex=False)        # strip US thousands separators
    return pd.to_numeric(x, errors="coerce")


def load_dataframe(raw: pd.DataFrame, mapping: dict | None = None,
                   decimal: str = ".") -> pd.DataFrame:
    """Map an arbitrary source frame into the canonical schema. The single seam
    every connector (CSV, SQL, BigQuery, Shoptet, …) funnels through.
    decimal=',' for European-formatted numbers."""
    m = {**DEFAULT_MAP, **(mapping or {})}
    df = pd.DataFrame()
    for canon, col in m.items():
        if col in raw.columns:
            df[canon] = raw[col]
    for c in ("quantity", "unit_price", "line_value"):
        if c in df.columns:
            df[c] = _to_num(df[c], decimal)
    return _finalize(df)


def load_csv(path: str, mapping: dict | None = None, decimal: str = ".",
             **read_kwargs) -> pd.DataFrame:
    read_kwargs.setdefault("dtype", str)          # let _to_num control parsing
    read_kwargs.setdefault("keep_default_na", False)
    return load_dataframe(pd.read_csv(path, **read_kwargs), mapping, decimal)


def _czk(s: pd.Series) -> pd.Series:
    """Parse Czech money strings ('1 234,56' / '-129,46') to float."""
    return pd.to_numeric(
        s.astype(str).str.replace(" ", "").str.replace(" ", "").str.replace(",", "."),
        errors="coerce")


# Milan's BigQuery export. product_id encodes line TYPE; status is Czech.
NON_PRODUCT_PREFIXES = ("BILLING", "SHIPPING")          # zero-value structural lines
DISCOUNT_PREFIXES = ("COUPON",)                          # negative adjustment lines
NONREVENUE_STATUS = {"Stornována", "Vrácená objednávka", "Platba selhala"}  # not realized


def load_milan(path: str = "data/bq_export.csv") -> pd.DataFrame:
    """Milan's real e-shop export (one row per order line).

    customer_key is the customer id (usually the email; hashed in this export).
    Keeps real SKU + GIFT + COUPON lines so order totals net out discounts;
    drops BILLING/SHIPPING structural lines and cancelled/returned/failed orders.
    line_value comes from revenue_per_line (authoritative — includes discounts),
    NOT qty*unit_price.
    """
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = pd.DataFrame({
        "customer_id": raw["customer_key"].astype(str),
        "order_id": raw["order_id"].astype(str),
        "order_date": pd.to_datetime(raw["order_datetime"], errors="coerce"),
        "quantity": pd.to_numeric(raw["product_quantity"], errors="coerce"),
        "unit_price": _czk(raw["unit_price"]),
        "line_value": _czk(raw["revenue_per_line"]),
        "line_cost": _czk(raw.get("cost_per_line", pd.Series(index=raw.index))),
        "product": raw["product_id"].astype(str),
        "country": raw["country"].astype(str),
        "status": raw["order_status"].astype(str),
        "zip_prefix": raw.get("zip_prefix", pd.Series("", index=raw.index)).astype(str),
    })
    df = df.dropna(subset=["customer_id", "order_date", "line_value"])
    df = df[df["customer_id"].str.lower().ne("nan") & df["customer_id"].ne("")]
    # drop structural billing/shipping rows and non-realized orders
    df = df[~df["product"].str.upper().str.startswith(NON_PRODUCT_PREFIXES)]
    df = df[~df["status"].isin(NONREVENUE_STATUS)]
    # flag real product lines (for diversity counts; excludes coupons/gifts)
    df["is_product"] = ~df["product"].str.upper().str.startswith(
        NON_PRODUCT_PREFIXES + DISCOUNT_PREFIXES)
    df["quantity"] = df["quantity"].fillna(0)
    df["unit_price"] = df["unit_price"].fillna(0)
    return df.reset_index(drop=True)


def summary(df: pd.DataFrame) -> dict:
    """One-glance EDA numbers for the dashboard / validation slide."""
    return {
        "rows": int(len(df)),
        "customers": int(df.customer_id.nunique()),
        "orders": int(df.order_id.nunique()),
        "countries": int(df.country.nunique()),
        "date_from": str(df.order_date.min().date()),
        "date_to": str(df.order_date.max().date()),
        "revenue": round(float(df.line_value.sum()), 2),
        "avg_order_value": round(float(df.groupby("order_id").line_value.sum().mean()), 2),
    }


if __name__ == "__main__":
    d = load_uci()
    print(d.head().to_string())
    import json
    print(json.dumps(summary(d), indent=2))
