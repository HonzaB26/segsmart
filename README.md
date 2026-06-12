# SegSmart

[![CI](https://github.com/anicka-net/segsmart/actions/workflows/ci.yml/badge.svg)](https://github.com/anicka-net/segsmart/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-2dd4bf.svg)](LICENSE)
[![Status: alpha](https://img.shields.io/badge/status-alpha-f59e0b.svg)](#)

**Enterprise-grade customer segmentation for small & medium businesses — that never leaves your own machine.**

SegSmart reads your e-shop's order history, automatically groups customers into
actionable segments (Champions, Loyal, At-risk, New, Dormant), detects seasonality,
and drafts a ready-to-run marketing campaign for each segment — all on hardware you
control. No cloud, no subscription, no customer data leaving the building.

> ⚠️ **Alpha.** Built as an MBA Data-X project. Interfaces will change. PRs welcome.

![SegSmart dashboard](docs/dashboard.png)

*(Data onboarding — file upload with AI column mapping, or a database connector —
lives on its own page: [`/setup`](docs/setup.png).)*

---

## Why local-first is the whole point

The SaaS segmentation tools (Bloomreach, Samba.ai, Ecomail) all require uploading
your **entire customer transaction history to their cloud**. For a GDPR-bound EU
business that's a liability, an ongoing cost, and a lock-in.

SegSmart inverts it. The analytics and the AI both run **on your own server**, so:

- 🔒 **Customer data never leaves your infrastructure** — the one thing no SaaS competitor can offer.
- 💸 **Buy once.** It's a container you run, not a per-seat subscription. Updates ship as new image tags.
- 🇪🇺 **GDPR by topology** — there's no third party to sign a DPA with.

The local model isn't a compromise. It's the moat.

---

## What it does

| Capability | How |
|---|---|
| **Automatic segmentation** | RFM scoring → 5 named, explainable segments |
| **Cross-check** | KMeans clustering on behavioral features, compared by adjusted Rand index |
| **Seasonality** | Monthly revenue index → the peak becomes a campaign hook |
| **AI campaign drafts** | A **local LLM** writes objective / channel / offer / headline per segment |
| **Impact estimates** | Computed deterministically (transparent response-rate assumptions), never hallucinated |
| **Human gate** | Campaigns are drafts the owner approves — nothing sends automatically |
| **Data honesty** | Ingest report (rows kept/dropped & why) + warnings when the data can't support conclusions (short window, suspicious money values, weak cluster agreement) |
| **Action lists** | Click a segment → the actual customers, exportable as CSV for your mailing tool |
| **Trend tracking** | Each run snapshots per-customer segments; the next run shows who moved (Champions → At-risk is your churn alarm) |

---

## Design

*Deep dive with the full schema and rationale: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Working on the code (human or agent)? Start with [AGENTS.md](AGENTS.md).*

### One canonical schema, many sources

Everything downstream consumes a single **order-line frame**:

```
customer_id · order_id · order_date · quantity · unit_price · line_value · product · country
```

A data source is just an adapter that produces this frame. Add a shop → write a
mapping, not new analytics code. That's the seam the whole system pivots on.

```
 sources ───────────────┐
  CSV / Excel           │
  SQL (MySQL/Postgres)  ├─▶  canonical frame  ─▶  features  ─▶  segmentation  ─▶  campaigns  ─▶  dashboard
  BigQuery              │      loader.py          features.py    segment.py       campaigns.py    server.py
  Shoptet API           │                                        seasonality.py                   index.html
 ───────────────────────┘
```

### Modules (`seg/`)

- **`loader.py`** — canonical schema + cleaning (drops cancellations/returns, null
  customers, non-positive prices). Adapters: `load_uci`, `load_eshop` (real e-shop
  export: Czech decimal comma, line-type rows, status filtering), `load_csv`,
  and `load_dataframe` (the seam every connector funnels through). Tolerates
  missing columns: no order id → one order per customer per day, no quantity → 1,
  no unit price → derived from an order-total column. Dates: EU/US order detected,
  Excel serials and unix epochs handled, bad rows dropped instead of crashing.
- **`sniff.py`** — reads whatever the upload actually is: encoding cascade
  (UTF-8 / BOM / cp1250 / latin-1), delimiter sniffing (`,` `;` tab `|`),
  report-title preamble skipping, duplicate/empty header repair, `.xlsx`/`.xls`.
  Anything that looks remotely like sales data should survive
  (`tests/test_messy.py` is the contract: 12 hostile fixtures of the same data
  must all converge to the same numbers).
- **`features.py`** — per-customer **RFM** + behavioral features (avg order value,
  basket size, tenure, product diversity, inter-purchase gap); log-damped for clustering.
- **`segment.py`** — **two algorithms**: quantile RFM scoring (interpretable, named)
  and KMeans (unsupervised cross-check), plus their agreement (ARI) and silhouette.
- **`seasonality.py`** — revenue indexed by month (normalized by days observed, so a
  truncated month isn't deflated); surfaces the peak as a campaign hook.
- **`campaigns.py`** — a **local LLM** (Ollama) drafts one campaign card per segment.
  Bilingual (EN/CS). Quality model for Czech copy = `qwen3.6:35b`; fast default =
  `gemma4:e4b`. Deterministic impact estimates + a rule-based fallback so it never
  breaks offline.
- **`connectors.py`** — BigQuery / SQL / Shoptet adapters → canonical frame.

### Two-model strategy

The AI layer is split on purpose:
- **Fast** (`gemma4:e4b`, ~4B, on-device-class) for interactive use.
- **Quality** (`qwen3.6:35b` MoE) for polished Czech marketing copy when it matters.

Either runs locally via the Ollama sidecar. Numbers (revenue impact) are *never*
left to the model — they're computed in Python.

### Synthetic data generator (`gen/`)

A real 2-month export is too short to *show* segmentation (everyone has ~1 order).
`gen/` synthesizes a longer, archetype-driven history matching a real e-shop schema
exactly — so RFM/KMeans actually separate (silhouette 0.49, ARI 0.49 vs 0.06 on the
short real window). `gen/catalog.py` is a templated, guaranteed-correct-Czech product
catalog; `gen/synth.py` builds the transactions (Czech seasonality, line types,
realistic emails). Safe to publish — no real customers.

---

## Deployment

### Quickstart (local Python)

```bash
pip install -r requirements.txt
# a small non-proprietary sample ships in the repo — run on it immediately:
python3 -c "import pipeline; pipeline.run(source='eshop', path='data/sample_eshop.csv', currency='Kč', use_llm=False)"
python3 server.py                       # dashboard at http://localhost:8099

# …or generate a full-size synthetic dataset (pure stdlib, no real data):
python3 -m gen.synth                    # writes data/synthetic_eshop.csv
```

`use_llm=False` skips the model (instant, rule-based cards). Set it `True` with an
Ollama running locally to get AI-drafted copy.

### Docker (the shippable product)

Two services — the app and a local Ollama, so data stays on the host:

```bash
docker compose up --build               # dashboard at http://localhost:8099
```

Prebuilt images are published to GHCR on every version tag:
`docker pull ghcr.io/anicka-net/segsmart:latest`.

The dashboard works immediately from the baked demo. To generate AI campaign copy
live, pull a model into the Ollama sidecar:

```bash
docker compose exec ollama ollama pull gemma4:e4b     # fast
docker compose exec ollama ollama pull qwen3.6:35b    # quality Czech copy
```

Or set `SEG_AUTOPULL=1` in `docker-compose.yml` to pull on first boot. The base image
is openSUSE Leap. For GPU inference, uncomment the `deploy` block in the compose file.

### Point it at your real data (`/setup`)

The UI is two pages: **`/`** is the dashboard (results only); **`/setup`** is where
data comes from. There you either **upload a file** (CSV/TSV/Excel — any language,
encoding or delimiter; a local model proposes the column mapping, you confirm) or
**configure a connector** (SQL for MySQL/MariaDB/PostgreSQL/SQLite — which covers
Shoptet, WooCommerce, PrestaShop, Magento — plus BigQuery and the Shoptet API),
test it against your database, confirm the mapping, save.

Either way the choice lands in a **local config file**:

```jsonc
// config/segsmart.json — plain JSON, hand-editable, re-read on every run
{
  "source": {
    "type": "sql",
    "connection_url": "mysql+pymysql://reader:${DB_PASSWORD}@localhost/eshop",
    "query": "SELECT email, order_no, created_at, qty, unit_price FROM order_lines",
    "mapping": {"customer_id": "email", "order_id": "order_no",
                "order_date": "created_at", "quantity": "qty", "unit_price": "price"}
  },
  "output": {"currency": "Kč", "language": "cs"},
  "ai": {"use_llm": true}
}
```

`${ENV_VAR}` references are expanded from the environment at run time, so secrets
never sit in the file. See `config/segsmart.example.json`. Headless runs use the
same file: `python3 pipeline.py --config`. Connector queries are checked to be
read-only (single SELECT/WITH) — still, use read-only DB credentials.

The connectors are also a Python API (`seg.connectors`) for notebooks; a `mapping`
is `{canonical_name: source_column}` — the only thing you write per shop. Connector
deps (`sqlalchemy`, `google-cloud-bigquery`, `requests`) are imported lazily;
install only what your deployment uses.

### Configuration (env)

| Variable | Default | Meaning |
|---|---|---|
| `SEG_PORT` | `8099` | dashboard port |
| `SEG_HOST` | `127.0.0.1` | bind address (`0.0.0.0` in Docker) |
| `SEG_AUTH` | *(empty = off)* | `user:password` → HTTP Basic Auth on the whole app. Set it whenever the port is reachable beyond localhost; for more than a trusted LAN, terminate TLS in a reverse proxy |
| `SEG_CONFIG` | `config/segsmart.json` | data-source config file |
| `OLLAMA_URL` | `http://localhost:11434` | local LLM endpoint (`http://ollama:11434` in compose) |
| `SEG_LLM_MODEL` | `gemma4:e4b` | campaign model (`qwen3.6:35b` for quality Czech) |
| `SEG_AUTOPULL` | `0` | pull the model on container start |

---

## MBA Data-X rubric crosswalk

| Rubric item | Where |
|---|---|
| Problem / NABC | local data-sovereignty reframe (this README, top) |
| EDA / data transformation | `loader.py` cleaning + `summary()` |
| Feature engineering | `features.py` — RFM + behavioral |
| Different models & algorithms | RFM scoring **vs** KMeans, compared by ARI |
| Results / demo / prototype | the dashboard (`server.py` + `index.html`) |
| Validation | silhouette, RFM↔KMeans ARI, real-vs-synthetic contrast |
| Areas of improvement | churn model, ESP send, A/B the offers, i18n |
| Next steps | more connectors, scheduled refresh, trend history |

---

## Roadmap

- [x] RFM + KMeans segmentation, seasonality, local-LLM campaign drafts
- [x] Dashboard, synthetic generator, Docker + Ollama on openSUSE
- [x] SQL connector (MySQL/Postgres/SQLite)
- [x] Ingest report + data-honesty warnings
- [x] Segment drill-down with CSV export (mailing-list handoff)
- [x] Segment-migration history between runs
- [x] GHCR image published on version tags
- [ ] Scheduled refresh (cron/systemd timer calling `pipeline.py --config`)
- [ ] BigQuery & Shoptet connectors hardened against live tenants
- [ ] Campaign export to ESP (Ecomail / SmartEmailing / Mailchimp)
- [ ] Churn / propensity model per segment
- [ ] Background job queue so slow quality-model runs don't block HTTP
- [ ] Margin-aware segmentation (profit, not just revenue, where cost data exists)

See [CONTRIBUTING.md](CONTRIBUTING.md) to pick something up.

## License

MIT — see [LICENSE](LICENSE).
