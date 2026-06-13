# SegSmart architecture

A walking tour for humans. The agent-facing rules are in
[AGENTS.md](../AGENTS.md); the pitch and quickstart in [README.md](../README.md).

## The one idea

Competing segmentation tools are SaaS: the SME ships its customer database to
someone else's cloud. SegSmart inverts that — **the software comes to the
data**. Everything below (parsing, features, clustering, even the LLM that
writes campaign copy) runs on the customer's own machine. Local isn't a
constraint we tolerate; it's the moat: a GDPR answer no SaaS competitor can
copy without abandoning their business model.

That single decision explains most of the design: stdlib HTTP server, no CDN
assets, single-file HTML, Ollama sidecar instead of an API key, a config file
on disk instead of an account.

## The big picture

```
                       INGESTION (the moat's gatehouse)
  ┌───────────────────────────────────────────────────────────────┐
  │  upload (CSV/TSV/XLSX, any encoding/delimiter/language)       │
  │  SQL (MySQL/Postgres/SQLite → Shoptet, WooCommerce, Presta…)  │
  │  BigQuery · Shoptet API · built-in samples                    │
  └───────────────┬───────────────────────────────────────────────┘
                  ▼
   seg/sniff.py        bytes → raw frame      (encoding cascade, delimiter
                                               sniff, preamble skip, Excel)
   seg/mapping.py      raw header+samples → proposed column mapping
                                              (local LLM + multilingual
                                               heuristic fallback)
   seg/loader.py       raw frame + mapping → ★ CANONICAL ORDER-LINE FRAME ★
                                              (synthesis of missing columns,
                                               EU/US dates, decimal commas,
                                               cancellation filtering)
                  │
                  ▼            ANALYTICS (source-agnostic from here on)
   seg/features.py     per-customer RFM + behavior (AOV, basket, tenure,
                       product diversity, interpurchase gap)
   seg/segment.py      ① RFM quantile rules → 5 named segments
                       ② KMeans on log-scaled features (cross-check)
                       validation: silhouette + adjusted Rand index ①↔②
   seg/seasonality.py  revenue index by calendar month (exposure-normalized)
   seg/external.py     owner-uploaded daily factors (FX/weather/promo) scored
                       against a persisted no-PII daily revenue series:
                       % lift for 0/1 flags, correlation for numeric factors
   seg/catalog.py      product-catalog enrichment: maps product ids to a
                       cat_1/cat_2 hierarchy from data/product_catalog.csv;
                       unknown ids generated via local LLM (Ollama) + appended,
                       heuristic fallback when no model. Feeds products.py.
   seg/products.py     product-mix cross-tab: segment × category → revenue +
                       customer count per cell; categories come from the
                       catalog first, else local LLM / heuristic, or a
                       'category' column the source already provides
                  │
                  ▼            NARRATIVE
   seg/campaigns.py    local LLM (Ollama) drafts one campaign card per
                       segment, EN/CS; impact estimates & priority are
                       DETERMINISTIC (the model writes copy, never numbers)
                  │
                  ▼
   pipeline.py         orchestrates the above → out/result.json
   server.py           stdlib HTTP: serves the JSON + two pages
   index.html          /        the dashboard (results only)
   setup.html          /setup   data onboarding + config editor
```

## The canonical frame — the architectural seam

Every source funnels into one shape, and everything downstream consumes only
that shape:

| column | type | notes |
|---|---|---|
| `customer_id` | str | email, hash, phone — whatever identifies the buyer |
| `order_id` | str | synthesized (customer+day) when the export has none |
| `order_date` | datetime | EU/US autodetected; Excel serials; unix epochs |
| `quantity` | float | defaults to 1 when absent |
| `unit_price` | float | derived from `line_value`/`quantity` when absent |
| `line_value` | float | the export's total wins over qty×price when present |
| `product` | str | optional; `is_product=False` lines (coupons) excluded from diversity counts |
| `country` | str | optional |

Truly required from any source: **who, when, and some money column**. The rest
is synthesized. Adding a data source means writing a fetch + a
`{canonical: source_column}` mapping — the analytics never change. This is the
single most load-bearing decision in the codebase.

The robustness contract is executable: `tests/test_messy.py` encodes the same
transactions twelve hostile ways (cp1250 + semicolons, BOM, order-grain
totals-only, no order ids, preamble junk, broken dates, Excel, serial dates,
ambiguous d/m, tabs, currency noise, duplicate headers) and asserts they all
converge to identical numbers.

## Why two segmentation algorithms

RFM quantile rules give **interpretable, named** segments (Champions, Loyal,
At-risk, New, Dormant) an SME owner can act on. KMeans on the feature matrix
finds **whatever geometry is actually there**. Neither is ground truth; their
*agreement* (adjusted Rand index) plus the KMeans silhouette is the honesty
metric shown on the dashboard. On a real 2-month export ARI collapses to ~0.06
— the pipeline visibly tells you the data window is too short, instead of
inventing segments.

## The two-model LLM strategy

| | model | role |
|---|---|---|
| fast | `gemma4:e4b` (~4 B) | interactive mapping inference, quick cards |
| quality | `qwen3.6:35b` MoE | polished Czech campaign copy (CPU-slow, batch) |

A rule-based fallback writes the cards when no model is reachable, so the
product never hard-depends on the LLM. Three hard lines: model output is
untrusted (escaped in the UI, JSON extracted defensively), **numbers are
never the model's** — response-rate assumptions and priority ranking are
deterministic and printed on the card — and **commercial terms are never the
model's**: prompts forbid invented voucher codes AND concrete discount
values; `strip_voucher_codes` scrubs codes that slip through, and a card
that still promises a specific percentage/amount is replaced by clean
rule-based copy (`has_invented_discount`). Discounts enter only through
the owner's form.

## Campaign workflow: draft → discount → launch

The card the model drafts is copy, not commerce. The owner then optionally
**adds a real discount** (percent / amount / free shipping + the code from
their own shop system) — `apply_discount` regenerates the copy around it (LLM
rewrite with the code protected, deterministic template as fallback). **Launch**
turns the approved card into a mailing artifact: subject, assembled body
(signed with `output.signature` from the config — editable in the /setup text
box, language-appropriate default otherwise) and
the segment's recipient list — each recipient with **e-mail and name** (from
mapped contact columns, or the customer id itself when it is an e-mail), so a
mailer can consume the file directly; the UI also offers the same list as an
import-ready CSV. Written to `out/mailings/` (never git-tracked)
and optionally POSTed to `config.mailer.webhook_url` — the seam where n8n,
Zapier, or a ten-line SMTP script plugs in. SegSmart itself never sends
anything; launch *is* the human gate — and it is double-locked: the first
click fetches a preview of the EXACT e-mail (subject, body, recipient counts,
whether a live webhook will fire) for review, and only an explicit confirm —
enforced by the API (`confirm: true`), not just the UI — saves and delivers.
No single stray click can mail a whole segment.

## Two languages, two knobs

The **UI language** (dashboard/setup chrome, incl. Czech month and segment
names and `cs-CZ` number formatting) is a client-side toggle in the header
(persisted in localStorage, `?lang=cs` override). The **content language** —
campaign copy and quality warnings — is set per run in /setup
(`output.language`) and carried in `result.meta.language`. They are
deliberately independent: a Czech owner may want an English UI over Czech
campaigns, or vice versa.

## The quality layer — honesty as a feature

Two mechanisms keep the tool from quietly lying:

- **Ingest report.** The loader counts every row it drops and why (unparseable
  date/price, missing customer, cancellations, billing lines). The dashboard
  shows "kept X of Y rows · dropped: …" — silently-dropped data is never silent.
- **Warnings.** The pipeline emits `quality.warnings` when the data can't
  support the conclusions: window shorter than ~6 months (frequency segments
  degenerate — observed on a real 2-month export), median order value below 1
  (the classic wrong-decimal-separator symptom), fewer than 50 customers,
  a high share of unparseable rows (mapping is off), or rule/cluster agreement
  near zero. The product tells the user *"don't trust this yet"* instead of
  rendering confident nonsense — something SaaS competitors structurally avoid.

## From insight to action

`result.json` carries per-customer rows (`customers`: id, segment, recency,
orders, spend), so the dashboard offers **segment drill-down** — click
Champions, see exactly who they are — and **CSV export** for handoff to a
mailing tool. The export is generated client-side from the already-local JSON;
nothing new touches the network.

Persisted runs also snapshot per-customer segments to `out/history/` (never
git-tracked). The next run diffs against the latest snapshot and reports
**segment migration** — "3 Champions → At-risk since the last run" is the
churn alarm an SME can actually act on. Segmentation becomes a movie, not a
photo.

## PII policy (enforced, not promised)

The public repo must contain no personal data — including team members' names.
`tests/test_no_pii.py` makes the policy executable: a denylist of personal
names stored as SHA-256 hashes (so the policy can't reintroduce what it bans),
e-mail patterns allowed only in known-synthetic fixtures, birth-number-shaped
strings banned, private artifacts (real exports, user config, uploads, history
snapshots) asserted never-tracked, and the baked demo's customer rows must be
anonymized `demo-NNNN` ids.

## Configuration & persistence

`config/segsmart.json` — plain JSON, owned by the user, hand-editable,
**re-read on every run** (no restart). `${ENV_VAR}` references expand at use
time so secrets live in the environment, not the file. The `/setup` page is
just a friendly editor for this file plus test-and-preview for connectors.

Persistence rule: a run from the saved config writes `out/result.json` and a
segment snapshot under `out/history/` (your configured source *is* your
dashboard, and snapshots enable migration tracking); an ad-hoc wizard upload
renders once and leaves nothing on disk.

## Server & security model

`server.py` is ~200 lines of stdlib `http.server`:

| route | method | role |
|---|---|---|
| `/`, `/setup` | GET | the two app pages |
| `/ad`, `/ad-cs` | GET | standalone marketing one-pager (`ad.html` / `ad.cs.html`, self-contained, EN + CS) |
| `/api/result` | GET | cached result JSON |
| `/api/config` | GET/POST | read / write the config file |
| `/api/infer_mapping` | POST | uploaded bytes → proposed mapping |
| `/api/preview_source` | POST | connector test: first rows + mapping |
| `/api/run` | POST | ad-hoc upload run (not persisted) |
| `/api/run_config` | POST | run from config (persisted) |
| `/api/external_impact` | POST | score an uploaded daily factors CSV against the persisted daily revenue (no customer data read) |
| `/api/refine_card` | POST | rewrite a card around an owner-set discount |
| `/api/launch` | POST | approved card → mailing artifact (+ webhook) |

Defence layers (sized for a data-handling tool on an SME machine):
binds `127.0.0.1` unless told otherwise (the compose file maps the container
port to host-localhost too) · optional HTTP Basic Auth (`SEG_AUTH`) over every
route — the results are customer revenue data — with a loud startup warning
when binding publicly without it · API-supplied file sources confined to
`data/` (no arbitrary-file read via preview; hand-edited configs on disk may
point anywhere) · upload size cap · uploads handled as bytes and deleted after
ad-hoc runs · all LLM/CSV-derived strings escaped in the UI · CSV exports
guard against spreadsheet formula injection · all JSON written atomically
(temp file + `os.replace`, so the threaded server never serves a partial
file) · connector queries restricted to a single SELECT/WITH · Docker runs as
a non-root user. For exposure beyond a trusted LAN, terminate TLS in a reverse
proxy in front.

## Deployment

```
docker compose up --build
┌────────────────────────── customer's machine ─────────────────────────┐
│  ┌────────────────┐  OLLAMA_URL   ┌──────────────────┐                │
│  │ segsmart       │ ────────────▶ │ ollama (sidecar) │                │
│  │ openSUSE Leap  │               │ gemma4 / qwen3.6 │                │
│  │ python3.11     │               └──────────────────┘                │
│  │ non-root, :8099│   volumes: ./config  ./data/uploads               │
│  └────────────────┘                                                   │
│        ▲ browser on the LAN (SEG_AUTH recommended)                    │
└────────────────────────────────────────────────────────────────────────┘
```

No GPU required (CPU inference works; quality model is just slower). The image
bakes a synthetic demo so the dashboard renders before any real data is
connected.

## Synthetic data (`gen/`)

A teammate's real export is gitignored; what ships is a generator matching its
exact BigQuery schema: a **templated** (deliberately not LLM-generated —
local models produced broken Czech) drogerie catalog and archetype-driven
customers (champion/loyal/B2B/new/at-risk/dormant) over a 20-month window with
a Christmas peak. The real-vs-synthetic contrast is itself a finding: 2 months
of real data can't show segmentation; 20 synthetic months can.

## Testing philosophy

~115 pytest cases, all offline, a few seconds total. Three layers: unit tests
per module, the messy-ingestion convergence battery, and regression tests
pinning every bug found by code review or live use (tie-aware scoring, the
C-invoice rule that once silently deleted whole exports, auth, read-only
guards) — plus the executable PII policy. CI runs the suite on every push;
a second workflow publishes the container image to GHCR on version tags.
