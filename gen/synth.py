"""Synthetic Czech e-shop generator — the partner shop's EXACT export schema.

Why: the real export we got is only ~2 months, so repeat-purchase behaviour is
invisible and segmentation has nothing to bite on. This generates a longer
(~20-month) history with archetype-driven customers so RFM/KMeans actually
separate — and it's safe to put on a slide (no real customers).

Output columns match bq_export.csv exactly, incl. Czech decimal comma, Czech
order statuses, line-type rows (SKU / BILLING / SHIPPING / COUPON / GIFT),
customer_key as a realistic Czech email (per the shop owner: "spíš email než hash").
Drop it straight into seg.loader.load_eshop.
"""
from __future__ import annotations
import csv, hashlib, random
from datetime import datetime, timedelta

from gen.catalog import build_catalog

HEADER = ["dt_created", "dt_updated", "dt_filename", "order_id", "order_datetime",
          "order_status", "currency", "exchange_rate", "customer_key", "zip_prefix",
          "country", "revenue_per_order", "product_quantity", "product_id",
          "item_status", "unit_price", "revenue_per_line", "cost_per_line"]

JMENA_M = ["Jan", "Petr", "Tomáš", "Martin", "Jakub", "Lukáš", "Pavel", "Ondřej",
           "Filip", "Marek", "Michal", "David", "Vojtěch", "Adam", "Josef"]
JMENA_Z = ["Jana", "Eva", "Hana", "Lucie", "Kateřina", "Tereza", "Markéta", "Veronika",
           "Petra", "Lenka", "Barbora", "Anna", "Kristýna", "Klára", "Michaela"]
PRIJMENI = ["Novák", "Svoboda", "Novotný", "Dvořák", "Černý", "Procházka", "Kučera",
            "Veselý", "Horák", "Němec", "Marek", "Pospíšil", "Pokorný", "Hájek",
            "Král", "Beneš", "Fiala", "Sedláček", "Doležal", "Zeman"]
EMAIL_DOM = ["seznam.cz", "gmail.com", "email.cz", "centrum.cz", "post.cz", "volny.cz"]
# realistic-ish Czech PSČ prefixes with rough weights
ZIPS = (["110", "120", "130", "140", "150", "160", "170", "180", "190"] * 3 +  # Praha
        ["252", "250", "251", "190", "281"] * 2 +                              # Praha-okolí
        ["602", "612", "618", "664"] * 2 + ["779", "779"] +                    # Brno/Olomouc
        ["370", "460", "500", "530", "326", "350", "680", "739", "748", "100"])

STATUS_OK = "Vyřízená objednávka"
# (status, weight) for non-defaulted orders
STATUS_MIX = [("Vyřízená objednávka", 0.90), ("Objednávka na cestě", 0.03),
              ("Zásilka na odběrném místě", 0.02), ("Stornována", 0.025),
              ("Vrácená objednávka", 0.005), ("Platba selhala", 0.01),
              ("Uvolněno do skladu", 0.01)]

# archetype -> (orders_min, orders_max, active span as (start_frac,end_frac of window),
#               basket_min, basket_max, qty_max, coupon_prob, gift_prob)
ARCHETYPES = {
    "champion": (10, 26, (0.0, 1.0), 2, 8, 4, 0.45, 0.20),
    "loyal":    (5, 11, (0.0, 0.97), 1, 5, 3, 0.30, 0.10),
    "b2b_bulk": (6, 16, (0.0, 1.0), 4, 12, 12, 0.20, 0.05),
    "new":      (1, 3, (0.85, 1.0), 1, 4, 2, 0.20, 0.05),
    "at_risk":  (4, 9, (0.0, 0.55), 1, 5, 3, 0.25, 0.08),
    "dormant":  (1, 3, (0.0, 0.40), 1, 3, 2, 0.10, 0.03),
}
# population mix (sums ~1)
MIX = [("champion", 0.14), ("loyal", 0.24), ("b2b_bulk", 0.05),
       ("new", 0.18), ("at_risk", 0.18), ("dormant", 0.21)]
# Czech retail seasonality multiplier by month (Vánoce peak, summer dip)
SEASON = {1: 0.85, 2: 0.80, 3: 0.95, 4: 1.0, 5: 1.05, 6: 0.95,
          7: 0.80, 8: 0.82, 9: 1.05, 10: 1.15, 11: 1.45, 12: 1.55}


def _czk(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def _feminize(surname: str) -> str:
    """Czech feminine surname form: Novák→Nováková, Černý→Černá, Němec→Němcová."""
    if surname.endswith("ý"):
        return surname[:-1] + "á"
    if surname.endswith("a"):
        return surname[:-1] + "ová"
    if surname.endswith("ec"):
        return surname[:-2] + "cová"
    if surname.endswith("ek"):
        return surname[:-2] + "ková"
    return surname + "ová"


_FOLD = str.maketrans("áčďéěíňóřšťúůýž", "acdeeinorstuuyz")


def _email(rng, used):
    for _ in range(50):
        female = rng.random() < 0.5
        jm = rng.choice(JMENA_Z if female else JMENA_M)
        surname = rng.choice(PRIJMENI)
        if female:
            surname = _feminize(surname)
        pr = surname.lower().translate(_FOLD)
        jm_a = jm.lower().translate(_FOLD)
        sep = rng.choice([".", "", "_"])
        tail = rng.choice(["", str(rng.randint(1, 99)), str(rng.randint(70, 99))])
        e = f"{jm_a}{sep}{pr}{tail}@{rng.choice(EMAIL_DOM)}"
        if e not in used:
            used.add(e)
            return e
    e = f"user{len(used)}@seznam.cz"; used.add(e); return e


def _seasonal_date(rng, lo: datetime, hi: datetime) -> datetime:
    """Sample a datetime in [lo,hi] weighted by monthly seasonality."""
    for _ in range(8):
        t = lo + timedelta(seconds=rng.random() * (hi - lo).total_seconds())
        if rng.random() < SEASON.get(t.month, 1.0) / 1.55:
            break
    return t.replace(hour=rng.randint(7, 22), minute=rng.randint(0, 59),
                     second=rng.randint(0, 59))


def generate(n_customers=4000, end_date="2026-06-11", window_months=20,
             seed=42, out="data/synthetic_eshop.csv"):
    rng = random.Random(seed)
    catalog = build_catalog(seed=seed)
    end = datetime.strptime(end_date, "%Y-%m-%d")
    start = end - timedelta(days=int(window_months * 30.4))
    span = (end - start).total_seconds()

    arch_names = [a for a, _ in MIX]
    arch_w = [w for _, w in MIX]
    ingest = end + timedelta(hours=6)
    dt_created = ingest.strftime("%Y-%m-%d %H:%M:%S")
    dt_updated = (ingest + timedelta(seconds=47)).strftime("%Y-%m-%d %H:%M:%S")
    fname = f"web-1639-{end.strftime('%Y%m%d')}-{hashlib.md5(str(seed).encode()).hexdigest()}.csv"

    used_emails: set[str] = set()
    rows = []
    order_id = 1_000_000

    for _ in range(n_customers):
        arch = rng.choices(arch_names, weights=arch_w)[0]
        omin, omax, (sf, ef), bmin, bmax, qmax, cp, gp = ARCHETYPES[arch]
        email = _email(rng, used_emails)
        zip_prefix = rng.choice(ZIPS)
        a_lo = start + timedelta(seconds=sf * span)
        a_hi = start + timedelta(seconds=ef * span)
        n_orders = rng.randint(omin, omax)

        for _o in range(n_orders):
            order_id += 1
            odt = _seasonal_date(rng, a_lo, a_hi)
            # most orders fine; a minority get a non-default status
            status = STATUS_OK if rng.random() < 0.86 else \
                rng.choices([s for s, _ in STATUS_MIX], weights=[w for _, w in STATUS_MIX])[0]
            basket = rng.sample(catalog, k=min(rng.randint(bmin, bmax), len(catalog)))
            lines = []           # (product_id, qty, unit_price, rev, cost_str)
            subtotal = 0.0
            for item in basket:
                qty = rng.randint(1, qmax)
                up = item["unit_price_czk"]
                rev = round(up * qty, 2)
                cost = round(item["unit_cost_czk"] * qty, 2)
                subtotal += rev
                lines.append((item["sku"], qty, up, rev, _czk(cost)))
            # coupon (negative) on some orders
            if rng.random() < cp and subtotal > 0:
                disc = -round(subtotal * rng.uniform(0.05, 0.18), 2)
                subtotal += disc
                lines.append((f"COUPON{rng.randint(10000, 99999)}", 1, disc, disc, ""))
            # occasional gift (0 value) line
            if rng.random() < gp:
                lines.append((f"GIFT{rng.randint(100, 999)}", 1, 0.0, 0.0, ""))
            # structural billing + shipping (0-value)
            lines.append((f"BILLING{rng.randint(10, 99)}", 1, 0.0, 0.0, ""))
            lines.append((f"SHIPPING{rng.randint(10, 99)}", 1, 0.0, 0.0, ""))

            rev_order = _czk(round(subtotal, 2))
            odt_s = odt.strftime("%Y-%m-%d %H:%M:%S")
            for pid, qty, up, rev, cost in lines:
                rows.append([dt_created, dt_updated, fname, order_id, odt_s, status,
                             "CZK", "1", email, zip_prefix, "Česká republika",
                             rev_order, qty, pid, status, _czk(up), _czk(rev), cost])

    rng.shuffle(rows)   # de-cluster by customer, like a real export sort
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    print(f"wrote {out}: {len(rows):,} lines, {order_id - 1_000_000:,} orders, "
          f"{n_customers:,} customers, {start.date()}–{end.date()}, {len(catalog)} SKUs")
    return out


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    generate(n_customers=n)
