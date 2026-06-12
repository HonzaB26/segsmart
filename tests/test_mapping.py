from seg.mapping import infer_mapping, _heuristic


HDR = ["E-Mail Kunde", "Bestell-Nr", "Bestelldatum", "Menge", "Einzelpreis", "Artikel"]
ROWS = [["a@x.de", "B-1", "2025-03-04", "2", "19,90", "Shampoo"]]


def test_heuristic_maps_required_fields():
    d = _heuristic(HDR, ROWS)
    m = d["mapping"]
    assert m["customer_id"] == "E-Mail Kunde"
    assert m["order_id"] == "Bestell-Nr"
    assert m["order_date"] == "Bestelldatum"
    assert m["quantity"] == "Menge"
    assert m["unit_price"] == "Einzelpreis"


def test_detects_comma_decimal():
    d = _heuristic(HDR, ROWS)
    assert d["decimal"] == ","


def test_czech_language_detection():
    d = _heuristic(["Zákazník", "Objednávka"], [["aě", "1,50"]])
    assert d["language"] == "cs"


def test_infer_no_llm_covers_required():
    d = infer_mapping(HDR, ROWS, use_llm=False)
    assert d["missing_required"] == []
    # every mapped column actually exists in the header
    assert all(v in HDR for v in d["mapping"].values())


def test_currency_sanitised_to_known_symbol():
    # heuristic finds no currency symbol in this data -> empty, never junk
    d = infer_mapping(HDR, ROWS, use_llm=False)
    assert d["currency"] in {"", "Kč", "€", "$", "£", "zł"}


def test_currency_detected_from_symbol():
    d = _heuristic(["price EUR"], [["10,00 €"]])
    assert d["currency"] == "€"
