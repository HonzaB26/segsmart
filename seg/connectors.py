"""Database / API connectors → canonical schema.

Each connector just FETCHES a raw frame and funnels it through
seg.loader.load_dataframe(raw, mapping). Segmentation/seasonality/campaigns/
dashboard never change — adding a shop = a mapping, not new code.

Heavy clients (sqlalchemy, google-cloud-bigquery, requests) are imported lazily
so the core app and Docker image stay slim; install only what a deployment needs.

A `mapping` is {canonical_name: source_column}. Canonical names:
  customer_id, order_id, order_date, quantity, unit_price, product, country
(line_value is derived as quantity*unit_price if not provided).
"""
from __future__ import annotations
import re

import pandas as pd

from seg.loader import load_dataframe

_WRITE = re.compile(r"\b(insert|update|delete|drop|alter|truncate|create|replace|"
                    r"grant|merge|attach)\b", re.IGNORECASE)


def _assert_readonly(query: str) -> None:
    """Reject anything that isn't a single read query. Defence in depth — always
    also use read-only DB credentials."""
    q = query.strip().rstrip(";")
    if ";" in q:
        raise ValueError("only a single statement is allowed")
    if not re.match(r"^\s*(select|with)\b", q, re.IGNORECASE):
        raise ValueError("only SELECT/WITH queries are allowed")
    if _WRITE.search(q):
        raise ValueError("write/DDL keywords are not allowed in a connector query")


# --- Generic SQL (MySQL/MariaDB → Shoptet, WooCommerce, PrestaShop, Magento;
#     PostgreSQL; SQLite) via SQLAlchemy --------------------------------------
def sql_connector(connection_url: str, query: str, mapping: dict | None = None) -> pd.DataFrame:
    """Read transactions from any SQL DB.

    connection_url examples:
      mysql+pymysql://user:pass@host:3306/eshop      (Shoptet/WooCommerce/PrestaShop)
      postgresql+psycopg2://user:pass@host:5432/shop
      sqlite:///path/to/shop.db
    query: SELECT returning order-line rows (any column names; map them).
    """
    _assert_readonly(query)
    from sqlalchemy import create_engine                    # lazy
    engine = create_engine(connection_url)
    with engine.connect() as conn:
        raw = pd.read_sql(query, conn)
    return load_dataframe(raw, mapping)


# --- Google BigQuery (Milan's data lives here) -----------------------------
def bigquery_connector(query: str, mapping: dict | None = None,
                       project: str | None = None, credentials_path: str | None = None) -> pd.DataFrame:
    """Run a BigQuery SQL query and map the result.

    Auth: set GOOGLE_APPLICATION_CREDENTIALS, or pass credentials_path to a
    service-account JSON. Reads stay read-only.
    """
    _assert_readonly(query)
    from google.cloud import bigquery                       # lazy
    if credentials_path:
        client = bigquery.Client.from_service_account_json(credentials_path, project=project)
    else:
        client = bigquery.Client(project=project)
    raw = client.query(query).result().to_dataframe()
    return load_dataframe(raw, mapping)


# Milan's actual table → canonical (matches load_milan, but live from BQ).
MILAN_BQ_MAPPING = {
    "customer_id": "customer_key", "order_id": "order_id", "order_date": "order_datetime",
    "quantity": "product_quantity", "unit_price": "unit_price", "product": "product_id",
    "country": "country",
}


# --- Shoptet (dominant Czech e-shop platform) REST API ----------------------
def shoptet_connector(api_token: str, base_url: str,
                      mapping: dict | None = None, page_limit: int = 100) -> pd.DataFrame:
    """Pull orders from the Shoptet Admin API and flatten to order-line rows.

    base_url e.g. https://<eshop>.myshoptet.com/action/ApiOrders/  (illustrative).
    Returns canonical order-line frame. Endpoint/field names vary by API version —
    adjust the flattening below to the tenant's actual schema.
    """
    import requests                                          # lazy
    rows, page = [], 1
    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}
    while True:
        r = requests.get(base_url, headers=headers,
                         params={"page": page, "itemsPerPage": page_limit}, timeout=30)
        r.raise_for_status()
        orders = r.json().get("data", {}).get("orders", [])
        if not orders:
            break
        for o in orders:
            for it in o.get("items", []):
                rows.append({
                    "customer_email": o.get("email"),
                    "order_code": o.get("code"),
                    "creation_time": o.get("creationTime"),
                    "amount": it.get("amount"),
                    "price": (it.get("itemPrice") or {}).get("withVat"),
                    "name": it.get("name"),
                    "country": (o.get("billingAddress") or {}).get("country"),
                })
        page += 1
    default = {"customer_id": "customer_email", "order_id": "order_code",
               "order_date": "creation_time", "quantity": "amount",
               "unit_price": "price", "product": "name", "country": "country"}
    return load_dataframe(pd.DataFrame(rows), {**default, **(mapping or {})})


CONNECTORS = {"sql": sql_connector, "bigquery": bigquery_connector, "shoptet": shoptet_connector}
