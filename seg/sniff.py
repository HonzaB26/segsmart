"""Read ANYTHING that looks remotely like sales data into a raw DataFrame.

Real SME exports are messy: cp1250 Czech Excel CSVs with semicolons and decimal
commas, UTF-8 BOMs, report titles above the header, tab-separated copy-pastes,
.xlsx files. This module's job is to get the bytes into a string-celled frame;
column meaning is seg.mapping's job, parsing/cleaning is seg.loader's.

read_table(data, filename) -> (DataFrame, info)
  info = {"encoding", "delimiter", "skipped_rows", "kind"}
"""
from __future__ import annotations
import csv, io

import pandas as pd

from seg.util import NoValidData

ENCODINGS = ("utf-8-sig", "utf-8", "cp1250", "latin-1")   # latin-1 never fails
DELIMITERS = (",", ";", "\t", "|")

# magic numbers for Excel containers
_XLSX_MAGIC = b"PK\x03\x04"          # zip (xlsx)
_XLS_MAGIC = b"\xd0\xcf\x11\xe0"     # OLE2 (legacy xls)


def _decode(data: bytes) -> tuple[str, str]:
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1"


def _sniff_delimiter(text: str) -> str:
    head = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(head, delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        counts = {d: head.count(d) for d in DELIMITERS}
        return max(counts, key=counts.get) if any(counts.values()) else ","


def _find_header(rows: list[list[str]]) -> int:
    """Index of the header row: first row with >=2 non-empty cells whose
    successor has the same width (skips report titles / blank preamble)."""
    for i in range(min(len(rows) - 1, 30)):
        cells = [c for c in rows[i] if str(c).strip()]
        if len(cells) >= 2 and len(rows[i + 1]) == len(rows[i]):
            return i
    return 0


def _clean_header(header: list) -> list[str]:
    out, seen = [], {}
    for i, h in enumerate(header):
        name = str(h).strip().lstrip("﻿") or f"col{i + 1}"
        if name in seen:                      # duplicate -> price, price_2, ...
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        out.append(name)
    return out


def read_table(data: bytes, filename: str = "") -> tuple[pd.DataFrame, dict]:
    """Bytes of a CSV/TSV/Excel file -> (string-celled DataFrame, sniff info)."""
    if not data:
        raise NoValidData("empty file")

    if (filename.lower().endswith((".xlsx", ".xls"))
            or data[:4] in (_XLSX_MAGIC, _XLS_MAGIC)):
        df = pd.read_excel(io.BytesIO(data))
        df.columns = _clean_header(list(df.columns))
        return df, {"encoding": "binary", "delimiter": None,
                    "skipped_rows": 0, "kind": "excel"}

    text, encoding = _decode(data)
    delim = _sniff_delimiter(text)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim)]
    rows = [r for r in rows if any(str(c).strip() for c in r)]   # drop blank lines
    if len(rows) < 2:
        raise NoValidData("file has no data rows")
    h = _find_header(rows)
    header = _clean_header(rows[h])
    width = len(header)
    body = [(r + [""] * width)[:width] for r in rows[h + 1:]]    # pad/trim ragged rows
    df = pd.DataFrame(body, columns=header, dtype=str)
    return df, {"encoding": encoding, "delimiter": delim,
                "skipped_rows": h, "kind": "csv"}
