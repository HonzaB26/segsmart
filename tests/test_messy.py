"""Messy-input battery: realistic horrible files an SME actually exports.

Every fixture encodes the SAME underlying transactions, so the assertion is
strong: whatever the encoding/delimiter/schema disease, the parsed numbers
must converge to the same customers, date span and mean line value.
"""
import random

import pandas as pd
import pytest

from seg.sniff import read_table, _clean_header
from seg.mapping import infer_mapping
from seg.loader import load_csv, _guess_dayfirst, _parse_dates
from seg.util import NoValidData


def _rows(n=300):
    rng = random.Random(1)
    out = []
    for i in range(n):
        day = pd.Timestamp("2025-03-01") + pd.Timedelta(days=rng.randint(0, 420))
        out.append({"email": f"zakaznik{rng.randint(1, 25)}@seznam.cz",
                    "order": f"OBJ-{1000 + i}", "date": day,
                    "qty": rng.randint(1, 5),
                    "price": round(rng.uniform(49, 1299), 2),
                    "item": rng.choice(["Šampon", "Mýdlo", "Krém"])})
    return out

ROWS = _rows()


def _write(tmp_path, name, encoding, header, fmt):
    p = tmp_path / name
    with open(p, "w", encoding=encoding) as f:
        f.write(header + "\n")
        for r in ROWS:
            f.write(fmt(r) + "\n")
    return str(p)


def _ingest(path):
    """The exact server flow: bytes -> sniff -> infer (heuristic) -> load."""
    raw, info = read_table(open(path, "rb").read(), filename=path)
    inf = infer_mapping(list(raw.columns), raw.head(5).astype(str).values.tolist(),
                        use_llm=False, delimiter=info.get("delimiter"))
    return load_csv(path, inf["mapping"], decimal=inf["decimal"]), inf


def _check(df):
    """All fixtures hold the same data -> same numbers must come out."""
    assert len(df) >= 290
    assert df.customer_id.nunique() == 25
    assert df.order_date.dt.year.between(2025, 2026).all()
    assert 1500 < df.line_value.mean() < 2500          # qty*price of the base data


# --- the twelve diseases -----------------------------------------------------

def test_czech_semicolon_cp1250(tmp_path):
    p = _write(tmp_path, "a.csv", "cp1250",
               "Zákazník;Číslo objednávky;Datum;Počet ks;Cena za kus",
               lambda r: f"{r['email']};{r['order']};{r['date']:%d.%m.%Y};"
                         f"{r['qty']};{str(r['price']).replace('.', ',')} Kč")
    df, inf = _ingest(p)
    _check(df)
    assert inf["decimal"] == "," and inf["currency"] == "Kč" and inf["language"] == "cs"


def test_bom_semicolon_datetime(tmp_path):
    p = _write(tmp_path, "a.csv", "utf-8-sig",
               "Zákazník;Objednávka;Datum;Množství;Jednotková cena",
               lambda r: f"{r['email']};{r['order']};{r['date']:%d.%m.%Y %H:%M};"
                         f"{r['qty']};{str(r['price']).replace('.', ',')}")
    df, _ = _ingest(p)
    _check(df)


def test_order_grain_total_only(tmp_path):
    p = _write(tmp_path, "a.csv", "utf-8",
               "customer_email,order_number,order_date,total",
               lambda r: f"{r['email']},{r['order']},{r['date']:%Y-%m-%d},"
                         f"{r['qty'] * r['price']:.2f}")
    df, inf = _ingest(p)
    _check(df)
    assert inf["missing_required"] == []                # total counts as money
    # unit_price derived from total / default qty 1
    assert (df.line_value == df.unit_price).all()


def test_no_order_id_transaction_dump(tmp_path):
    p = _write(tmp_path, "a.csv", "utf-8", "klient,datum,castka",
               lambda r: f"{r['email']},{r['date']:%Y-%m-%d},{r['qty'] * r['price']:.2f}")
    df, _ = _ingest(p)
    _check(df)
    assert df.order_id.str.contains("@seznam.cz@20").any()   # synthesized ids


def test_preamble_junk_above_header(tmp_path):
    p = tmp_path / "a.csv"
    body = "\n".join(f"{r['email']},{r['order']},{r['date']:%Y-%m-%d},{r['qty']},{r['price']}"
                     for r in ROWS)
    p.write_text("Export objednávek\nObdobí: 1.3.2025 – 31.5.2026\n\n"
                 "email,order_id,date,quantity,unit_price\n" + body)
    df, inf = _ingest(str(p))
    _check(df)
    assert inf["sniff"]["skipped_rows"] == 2 if "sniff" in inf else True


def test_one_bad_date_row_survives(tmp_path):
    p = _write(tmp_path, "a.csv", "utf-8", "customer_id,order_id,order_date,quantity,unit_price",
               lambda r: f"{r['email']},{r['order']},{r['date']:%Y-%m-%d},{r['qty']},{r['price']}")
    with open(p, "a") as f:
        f.write("x@y.cz,OBJ-9999,not a date,1,10\n")
    df, _ = _ingest(p)
    _check(df)                                          # bad row dropped, rest kept


def test_excel_xlsx(tmp_path):
    p = tmp_path / "a.xlsx"
    pd.DataFrame([{"Zákazník": r["email"], "Obj.": r["order"], "Datum": r["date"],
                   "ks": r["qty"], "Cena": r["price"]} for r in ROWS]
                 ).to_excel(p, index=False)
    df, _ = _ingest(str(p))
    _check(df)


def test_excel_serial_dates(tmp_path):
    p = _write(tmp_path, "a.csv", "utf-8", "customer,order,date,qty,price",
               lambda r: f"{r['email']},{r['order']},"
                         f"{(r['date'] - pd.Timestamp('1899-12-30')).days},"
                         f"{r['qty']},{r['price']}")
    df, _ = _ingest(p)
    _check(df)


def test_dayfirst_dates_czech(tmp_path):
    p = _write(tmp_path, "a.csv", "utf-8", "zakaznik,objednavka,datum,mnozstvi,cena",
               lambda r: f"{r['email']},{r['order']},{r['date']:%d/%m/%Y},{r['qty']},{r['price']}")
    df, _ = _ingest(p)
    _check(df)
    # a m/d misparse would produce dates not in the ground truth at all
    truth = {r["date"] for r in ROWS}
    assert set(df.order_date) == truth


def test_tab_separated(tmp_path):
    p = _write(tmp_path, "a.txt", "utf-8", "customer\torder\tdate\tquantity\tprice",
               lambda r: f"{r['email']}\t{r['order']}\t{r['date']:%Y-%m-%d}\t{r['qty']}\t{r['price']}")
    df, _ = _ingest(p)
    _check(df)


def test_currency_symbols_and_spaces(tmp_path):
    p = _write(tmp_path, "a.csv", "utf-8", "EMAIL,Order ID,DATE,Qty,PRICE",
               lambda r: f"{r['email']},{r['order']},{r['date']:%Y-%m-%d},{r['qty']},"
                         + f"€ {r['price']:,.2f}".replace(",", " "))
    df, _ = _ingest(p)
    _check(df)


def test_duplicate_and_empty_headers(tmp_path):
    p = _write(tmp_path, "a.csv", "utf-8", "email,order,date,qty,price,,price",
               lambda r: f"{r['email']},{r['order']},{r['date']:%Y-%m-%d},"
                         f"{r['qty']},{r['price']},x,{r['price']}")
    df, _ = _ingest(p)
    _check(df)


# --- unit tests for the new pieces -------------------------------------------

def test_clean_header_dedup():
    assert _clean_header(["a", "", "a", "b"]) == ["a", "col2", "a_2", "b"]


def test_guess_dayfirst():
    assert _guess_dayfirst(pd.Series(["13/02/2025", "01/03/2025"])) is True
    assert _guess_dayfirst(pd.Series(["02/13/2025", "03/01/2025"])) is False
    assert _guess_dayfirst(pd.Series(["5.3.2025"])) is True      # dotted = EU
    assert _guess_dayfirst(pd.Series(["2025-03-05"])) is False


def test_parse_dates_unix_epoch():
    s = pd.Series(["1740000000", "1750000000"])
    out = _parse_dates(s)
    assert out.dt.year.between(2025, 2026).all()


def test_read_table_empty_raises():
    with pytest.raises(NoValidData):
        read_table(b"")
    with pytest.raises(NoValidData):
        read_table(b"just one line, no data")


# --- the C-prefix cancellation rule must be UCI-shaped, not a substring grab ---
def test_cz_prefixed_order_ids_survive(tmp_path):
    p = _write(tmp_path, "a.csv", "utf-8", "customer_id,order_id,order_date,quantity,unit_price",
               lambda r: f"{r['email']},CZ2025-{r['order'][4:]},{r['date']:%Y-%m-%d},"
                         f"{r['qty']},{r['price']}")
    df, _ = _ingest(p)
    _check(df)                          # 'CZ...' is an order number, not a cancellation


def test_synthesized_ids_for_c_customers_survive(tmp_path):
    # customer ids starting with 'c' + synthesized order ids must not be
    # mistaken for C-invoices (this silently deleted 100% of rows once)
    p = _write(tmp_path, "a.csv", "utf-8", "klient,datum,castka",
               lambda r: f"c_{r['email']},{r['date']:%Y-%m-%d},{r['qty'] * r['price']:.2f}")
    df, _ = _ingest(p)
    _check(df)


def test_uci_c_invoice_still_dropped():
    from seg.loader import load_dataframe
    df = pd.DataFrame({
        "customer_id": ["a", "a"], "order_id": ["536365", "C536366"],
        "order_date": ["2025-01-01", "2025-01-02"],
        "quantity": [1, -1], "unit_price": [10.0, 10.0]})
    out = load_dataframe(df, {})
    assert list(out.order_id) == ["536365"]


def test_missing_money_column_raises(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("customer_id,order_date\na@x.cz,2025-01-01\nb@x.cz,2025-01-02\n")
    with pytest.raises(NoValidData):
        load_csv(str(p), {})
