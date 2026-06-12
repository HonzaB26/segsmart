"""Local configuration: where the data comes from and how to read it.

One JSON file, owned by the user, editable by hand or via the /setup page:

    config/segsmart.json
    {
      "source": {
        "type": "file",                       // file | sql | bigquery | shoptet | sample
        "path": "data/uploads/orders.csv",    // file
        "connection_url": "mysql+pymysql://user:${DB_PASSWORD}@host/eshop",  // sql
        "query": "SELECT * FROM order_lines", // sql / bigquery
        "project": "my-project",              // bigquery
        "credentials_path": "sa.json",        // bigquery
        "base_url": "https://...",            // shoptet
        "api_token": "${SHOPTET_TOKEN}",      // shoptet
        "mapping": {"customer_id": "email", "...": "..."},
        "decimal": ","
      },
      "output": {"currency": "Kč", "language": "cs"},
      "ai": {"use_llm": true}
    }

`${ENV_VAR}` references are expanded when the source is used (never written
back), so secrets can stay in the environment instead of the file. The file is
re-read on every run — hand edits apply without a restart.
"""
from __future__ import annotations
import json, os, re
from pathlib import Path

import pandas as pd

from seg.util import NoValidData, atomic_write_json

CONFIG_PATH = os.environ.get("SEG_CONFIG", "config/segsmart.json")

SOURCE_TYPES = ("file", "sql", "bigquery", "shoptet", "sample")


def load_config(path: str | None = None) -> dict:
    p = path or CONFIG_PATH
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict, path: str | None = None) -> str:
    return atomic_write_json(path or CONFIG_PATH, cfg)


def _env(v):
    """Expand ${VAR} in string values at use time (secrets stay in the env)."""
    if isinstance(v, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), v)
    return v


def fetch_raw(source: dict, trusted_paths: bool = False) -> pd.DataFrame:
    """Fetch the source's RAW frame (pre-mapping) — also used by the /setup
    preview so the user can confirm the column mapping before saving.

    trusted_paths: file-type sources may point anywhere ONLY when the config
    came from the local disk (CLI, hand-edited file). Configs supplied over
    the HTTP API are confined to data/ — otherwise any API caller could read
    arbitrary local files (/proc/self/environ, ~/.ssh/...) via the preview."""
    t = source.get("type")
    if t == "file":
        from seg.sniff import read_table
        path = _env(source.get("path", ""))
        if not trusted_paths:
            p = Path(path).resolve()
            root = Path("data").resolve()
            if not p.is_relative_to(root):
                raise NoValidData(
                    "file sources supplied via the API are restricted to the "
                    "data/ directory — upload the file in the wizard, or point "
                    f"the config file on disk ({CONFIG_PATH}) at it by hand")
        if not path or not os.path.exists(path):
            raise NoValidData(f"file not found: {path!r} — check source.path in the config")
        with open(path, "rb") as f:
            raw, _info = read_table(f.read(), filename=path)
        return raw
    if t == "sql":
        from seg.connectors import _assert_readonly
        query = _env(source.get("query", ""))
        _assert_readonly(query)
        from sqlalchemy import create_engine                    # lazy
        engine = create_engine(_env(source.get("connection_url", "")))
        with engine.connect() as conn:
            return pd.read_sql(query, conn)
    if t == "bigquery":
        from seg.connectors import _assert_readonly
        query = _env(source.get("query", ""))
        _assert_readonly(query)
        from google.cloud import bigquery                       # lazy
        creds = _env(source.get("credentials_path") or "")
        project = _env(source.get("project") or "") or None
        client = (bigquery.Client.from_service_account_json(creds, project=project)
                  if creds else bigquery.Client(project=project))
        return client.query(query).result().to_dataframe()
    if t == "sample":
        from seg.loader import load_uci
        return load_uci(_env(source.get("path") or "data/online_retail.parquet"))
    if t == "shoptet":
        from seg.connectors import shoptet_connector
        return shoptet_connector(_env(source.get("api_token", "")),
                                 _env(source.get("base_url", "")),
                                 mapping=source.get("mapping"))
    raise NoValidData(f"unknown source type {t!r} — expected one of {SOURCE_TYPES}")


def fetch_dataframe(source: dict, trusted_paths: bool = False) -> pd.DataFrame:
    """Source config -> canonical order-line frame."""
    raw = fetch_raw(source, trusted_paths=trusted_paths)
    if source.get("type") in ("sample", "shoptet"):     # already canonical
        return raw
    from seg.loader import load_dataframe
    return load_dataframe(raw, source.get("mapping"),
                          decimal=source.get("decimal", "."))
