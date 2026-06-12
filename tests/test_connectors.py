import pandas as pd
import sqlalchemy

from seg.connectors import sql_connector
from seg.loader import CANON


def test_sql_connector_sqlite_roundtrip(tmp_path):
    db = tmp_path / "shop.db"
    url = f"sqlite:///{db}"
    src = pd.DataFrame({
        "cust_ref": ["a", "a", "b", "c"],
        "invoice": ["1", "1", "2", "3"],
        "created_at": ["2025-01-01", "2025-01-01", "2025-02-01", "2025-03-01"],
        "qty": [2, 1, 3, 1],
        "price": [10.0, 5.0, 7.0, 100.0],
        "item_name": ["X", "Y", "Z", "W"],
    })
    src.to_sql("order_lines", url, index=False, if_exists="replace")

    mapping = {"customer_id": "cust_ref", "order_id": "invoice",
               "order_date": "created_at", "quantity": "qty",
               "unit_price": "price", "product": "item_name"}
    df = sql_connector(url, "SELECT * FROM order_lines", mapping)

    assert list(df.columns) == CANON
    assert df["customer_id"].nunique() == 3
    assert abs(df.iloc[0]["line_value"] - 20.0) < 1e-6
