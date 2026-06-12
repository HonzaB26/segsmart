"""LLM-assisted CSV onboarding: map ANY e-shop export to our canonical schema.

User drops a CSV with arbitrary column names in any language. We show the local
LLM the header + a few sample rows; it proposes {canonical: their_column},
detects currency / language / decimal format. A multilingual heuristic fallback
runs when no model is available, so onboarding never hard-fails.

Canonical targets: customer_id, order_id, order_date, quantity, unit_price,
product (optional), country (optional).
"""
from __future__ import annotations
import json, os, re, urllib.request

from seg.util import extract_json

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MAP_MODEL = os.environ.get("SEG_MAP_MODEL", "gemma4:e4b")   # fast; interactive

CANONICAL = {
    "customer_id": "customer identifier — email, id, hash, phone",
    "order_id": "order / invoice / transaction id",
    "order_date": "order date or datetime",
    "quantity": "quantity / amount of the item",
    "unit_price": "price per unit",
    "product": "product name or id (optional)",
    "country": "country (optional)",
}

# multilingual header aliases (lowercased substrings) for the heuristic fallback
ALIASES = {
    "customer_id": ["customer", "zakazn", "zákazn", "kunde", "cliente", "client",
                    "email", "e-mail", "mail", "user", "uzivatel", "uživatel", "key"],
    "order_id": ["order", "objedn", "invoice", "faktur", "transaction", "transak",
                 "bestell", "pedido", "doklad", "code"],
    "order_date": ["date", "datum", "datetime", "created", "creation", "time",
                   "cas", "čas", "den", "zeit", "fecha"],
    "quantity": ["quantity", "qty", "mnozstv", "množstv", "amount", "pocet", "počet",
                 "menge", "cantidad", "ks", "pieces", "kusy"],
    "unit_price": ["unit_price", "unitprice", "price", "cena", "preis", "precio",
                   "prix", "jednotk", "cena_ks"],
    "product": ["product", "produkt", "item", "sku", "zbozi", "zboží", "nazev",
                "název", "name", "artikel", "articulo"],
    "country": ["country", "zeme", "země", "land", "stat", "stát", "pais", "país"],
}

CURRENCY_HINTS = {"Kč": ["kc", "kč", "czk"], "€": ["eur", "€"], "$": ["usd", "$"],
                  "£": ["gbp", "£"], "zł": ["pln", "zł", "zl"]}


def _heuristic(header: list[str], samples: list[list]) -> dict:
    used, mapping = set(), {}
    low = [(h, h.lower()) for h in header]
    for canon, keys in ALIASES.items():
        for orig, lo in low:
            if orig in used:
                continue
            if any(k in lo for k in keys):
                mapping[canon] = orig
                used.add(orig)
                break
    # detect currency + decimal from headers + sample cells
    blob = " ".join(header).lower() + " " + " ".join(
        str(c) for row in samples for c in row).lower()
    currency = next((sym for sym, ks in CURRENCY_HINTS.items() if any(k in blob for k in ks)), "")
    decimal = "," if re.search(r"\d+,\d{2}\b", blob) else "."
    lang = "cs" if re.search(r"[ěščřžýáíéúůňťď]", blob) else "en"
    return {"mapping": mapping, "currency": currency, "decimal": decimal,
            "language": lang, "source": "heuristic"}


def _llm(header: list[str], samples: list[list], timeout=90) -> dict | None:
    fields = "\n".join(f"- {k}: {v}" for k, v in CANONICAL.items())
    rows = "\n".join(", ".join(str(c) for c in r) for r in samples[:4])
    prompt = (
        "Map this e-shop CSV to our schema. Return ONLY JSON.\n\n"
        f"Our canonical fields:\n{fields}\n\n"
        f"Their CSV columns: {header}\n"
        f"Sample rows:\n{rows}\n\n"
        'Return: {"mapping": {canonical_field: their_column_name_or_null}, '
        '"currency": "symbol like Kč/€/$ or empty", "decimal": "," or ".", '
        '"language": "cs/en/de/...", "notes": "one short sentence"}. '
        "Only include canonical fields you are confident about."
    )
    body = {"model": MAP_MODEL, "prompt": prompt, "stream": False,
            "format": "json", "options": {"temperature": 0}}
    req = urllib.request.Request(f"{OLLAMA}/api/generate",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        raw = json.load(urllib.request.urlopen(req, timeout=timeout)).get("response", "")
        d = extract_json(raw)
        d["source"] = MAP_MODEL
        return d
    except Exception as e:
        print(f"  [mapping LLM unavailable: {e}] -> heuristic")
        return None


def infer_mapping(header: list[str], samples: list[list], use_llm=True) -> dict:
    """Propose a column mapping + currency/decimal/language for a raw CSV.
    LLM-first, heuristic fallback; always validates columns actually exist."""
    result = (_llm(header, samples) if use_llm else None) or _heuristic(header, samples)
    # keep only mappings whose target column truly exists; fill gaps from heuristic
    heur = _heuristic(header, samples)
    m = {k: v for k, v in (result.get("mapping") or {}).items() if v in header}
    for k, v in heur["mapping"].items():
        m.setdefault(k, v)
    result["mapping"] = m
    # sanitise model output: only accept known currency symbols / decimals,
    # else fall back to the heuristic (LLM sometimes echoes the decimal as currency)
    if result.get("currency") not in CURRENCY_HINTS:
        result["currency"] = heur["currency"]
    if result.get("decimal") not in (",", "."):
        result["decimal"] = heur["decimal"]
    result.setdefault("language", heur["language"])
    # confidence = required fields covered
    required = {"customer_id", "order_id", "order_date", "quantity", "unit_price"}
    result["missing_required"] = sorted(required - set(m))
    return result


if __name__ == "__main__":
    # a deliberately messy, foreign-named CSV
    hdr = ["E-Mail Kunde", "Bestell-Nr", "Bestelldatum", "Menge", "Einzelpreis EUR", "Artikel"]
    rows = [["a@x.de", "B-1001", "2025-03-04", "2", "19,90", "Shampoo"],
            ["b@x.de", "B-1002", "2025-03-05", "1", "1.299,00", "Föhn"]]
    print(json.dumps(infer_mapping(hdr, rows, use_llm=True), indent=2, ensure_ascii=False))
