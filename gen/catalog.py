"""Czech product catalog for the synthetic generator.

Default = a templated, GUARANTEED-correct-Czech catalog (category × qualifier →
clean names). No LLM gibberish risk. An optional LLM path (local model good at
Czech) can enrich/expand it, but the templated base is what the demo ships with.

A drogerie / household / cosmetics e-shop (high repeat-purchase → good for
segmentation). Prices in CZK, cost ~50–62 % of price (realistic drogerie margin).
"""
from __future__ import annotations
import random

# category -> (base nouns, qualifiers, (price_min, price_max))
TEMPLATES = {
    "Prací prostředky": (
        ["Prací gel", "Prací prášek", "Kapsle na praní", "Aviváž", "Odstraňovač skvrn"],
        ["univerzální", "na bílé prádlo", "na barevné prádlo", "na černé prádlo",
         "s vůní levandule", "pro citlivou pokožku", "color", "sensitive"],
        (79, 399)),
    "Čisticí prostředky": (
        ["Čistič na okna", "Čistič na podlahy", "Odmašťovač do kuchyně", "Čistič WC",
         "Univerzální čistič", "Čistič na koupelny", "Tablety do myčky", "Leštěnka na nábytek"],
        ["citron", "eukalyptus", "antibakteriální", "ocet", "s vůní moře", "koncentrát"],
        (39, 249)),
    "Toaletní papír a hygiena": (
        ["Toaletní papír", "Papírové kapesníky", "Kuchyňské utěrky", "Vlhčené ubrousky"],
        ["3vrstvý 8 rolí", "3vrstvý 16 rolí", "4vrstvý 10 rolí", "box 100 ks",
         "balení 10 ks", "s balzámem"],
        (29, 199)),
    "Vlasová kosmetika": (
        ["Šampon", "Kondicionér", "Vlasová maska", "Suchý šampon", "Lak na vlasy", "Olej na vlasy"],
        ["pro normální vlasy", "pro mastné vlasy", "proti lupům", "pro objem",
         "pro barvené vlasy", "regenerační", "s arganovým olejem"],
        (59, 329)),
    "Péče o pleť": (
        ["Pleťový krém", "Micelární voda", "Čisticí gel", "Pleťové sérum", "Tělové mléko",
         "Krém na ruce", "Sprchový gel", "Tělový peeling"],
        ["hydratační", "vyživující", "pro citlivou pleť", "denní", "noční",
         "s kyselinou hyaluronovou", "s vitaminem C", "zpevňující"],
        (49, 449)),
    "Dekorativní kosmetika": (
        ["Řasenka", "Rtěnka", "Make-up", "Korektor", "Tvářenka", "Oční stíny", "Lak na nehty"],
        ["voděodolná", "dlouhotrvající", "matný", "hydratační", "objemová", "paletka"],
        (89, 399)),
    "Péče o dutinu ústní": (
        ["Zubní pasta", "Ústní voda", "Zubní kartáček", "Mezizubní kartáčky", "Dentální nit"],
        ["whitening", "pro citlivé zuby", "complete care", "bylinná", "soft", "medium"],
        (29, 189)),
    "Holení a depilace": (
        ["Holicí strojek", "Gel na holení", "Pěna na holení", "Náhradní hlavice", "Depilační krém"],
        ["pro muže", "pro ženy", "sensitive", "3 břity", "5 břitů", "balení 4 ks"],
        (59, 349)),
    "Dětská drogerie": (
        ["Dětské plenky", "Vlhčené ubrousky", "Dětský šampon", "Dětský zásyp", "Krém na opruzeniny"],
        ["vel. 3", "vel. 4", "vel. 5", "newborn", "sensitive", "Mega Pack"],
        (89, 549)),
    "Krmivo pro mazlíčky": (
        ["Granule pro psy", "Granule pro kočky", "Kapsička pro kočky", "Konzerva pro psy",
         "Pamlsky pro psy", "Stelivo pro kočky"],
        ["s kuřecím masem", "s lososem", "adult", "junior", "sterilised", "4 kg", "10 kg"],
        (29, 699)),
}


def build_catalog(seed: int = 7, per_category: int = 8) -> list[dict]:
    """Generate a clean Czech SKU catalog. Deterministic given seed."""
    rng = random.Random(seed)
    cat = []
    sku = 100000
    for kategorie, (bases, quals, (pmin, pmax)) in TEMPLATES.items():
        combos = [(b, q) for b in bases for q in quals]
        rng.shuffle(combos)
        for b, q in combos[:per_category]:
            sku += 1
            price = round(rng.uniform(pmin, pmax) / 10) * 10 - 0.10   # ...9,90 style
            margin = rng.uniform(0.50, 0.62)
            cat.append({
                "sku": str(sku),
                "nazev": f"{b} {q}",
                "kategorie": kategorie,
                "unit_price_czk": round(price, 2),
                "unit_cost_czk": round(price * margin, 2),
            })
    return cat


if __name__ == "__main__":
    c = build_catalog()
    print(f"{len(c)} SKUs across {len(TEMPLATES)} categories\n")
    for x in c[:12]:
        print(f"  {x['sku']}  {x['nazev']:48} {x['kategorie']:24} "
              f"{x['unit_price_czk']:.2f} Kč (cost {x['unit_cost_czk']:.2f})")
