# AGENTS.md — how to work in this repo

Operational guide for coding agents (and fast-moving humans). The big picture
lives in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); the contribution flow in
[CONTRIBUTING.md](CONTRIBUTING.md). This file is the rules and the gotchas.

## What this is

SegSmart: local-first customer segmentation for SMEs. RFM + KMeans + seasonality
+ campaign cards drafted by a **local** LLM (Ollama). The entire value
proposition is that customer data never leaves the user's machine — every
change must preserve that.

## Hard rules

1. **No real customer data in the repo. Ever.** `data/bq_export.csv` (a real
   e-shop export) exists locally but is gitignored — never force-add it, never
   paste rows from it into code, tests, or docs. Synthesize with `gen/` instead.
   Likewise gitignored: `config/segsmart.json` (user config, may reference
   private DBs) and `data/uploads/`.
2. **Nothing calls the network in the analytics path** except the Ollama LLM
   (which is local) and explicit connectors. No telemetry, no CDN assets in the
   HTML (single-file, works offline), no new SaaS dependencies.
3. **Everything consumes the canonical order-line frame** produced by
   `seg/loader.py`:
   `customer_id · order_id · order_date · quantity · unit_price · line_value · product · country`.
   New data source = a fetch + `load_dataframe(raw, mapping)`. Never teach the
   analytics about a specific source.
4. **Tests must stay offline and fast** (currently ~5 s for ~100 tests). No
   Ollama, no UCI download, no Docker in tests — fixtures synthesize their own
   data (see `tests/conftest.py`, `tests/test_messy.py`).
5. **The dashboard escapes everything** — any LLM- or CSV-derived string
   rendered in `index.html`/`setup.html` goes through `esc()`. LLM output and
   uploaded files are untrusted input.
6. **Connector queries are read-only** — `_assert_readonly` in
   `seg/connectors.py` must guard every SQL/BQ path (single SELECT/WITH, no
   write keywords). Don't weaken it; it's defence-in-depth next to read-only
   DB credentials.
7. **Persistence contract**: runs from the saved config persist
   `out/result.json` plus a per-customer segment snapshot in `out/history/`
   (feeds the dashboard and migration tracking); ad-hoc wizard uploads
   (`/api/run`) must NOT persist anything. `out/history/` and `out/mailings/`
   (launch artifacts with recipient lists) are never tracked.
8. **The server stays stdlib-only** (`http.server`). No Flask/FastAPI — the
   buy-once product has no dependency churn.
9. **Docs ship in the same commit as the change.** If a change alters behavior,
   API, schema, config, routes, or workflow, update every doc that describes it
   (README.md, docs/ARCHITECTURE.md, this file, CONTRIBUTING.md) before
   committing. A doc that contradicts the code is worse than no doc — readers
   can't tell which one is lying. Quick audit: `git grep` the names of whatever
   you changed (routes, env vars, functions, file paths) across `*.md`.
10. **No personal data in the repo — including team members' names.**
    `tests/test_no_pii.py` enforces this (hashed name denylist, e-mail and
    birth-number patterns, never-tracked private paths). If it fires, scrub the
    content; to denylist a new name, add its SHA-256 — never the name itself.
11. **The UI is bilingual — shipping English-only breaks the Czech demo, so
    it breaks the build.** Every user-facing string in `index.html` /
    `setup.html` is wired to a translation key that exists in BOTH the `en`
    and `cs` `UI` dicts: static chrome via `data-i`, user-visible attributes
    (`title` / `aria-label` / `alt`) via `data-i-<attr>` (applyLang sets them),
    dynamic strings via `t()`/`tf()`, numbers via `fmtNum`/`fmtDec`,
    months/segment names via the display-only helpers. Hardcode visible
    English, or add a key to one dict and forget the other, and
    `tests/test_i18n.py` fails (key parity + data-i resolution + unwired-chrome
    scan over tags/hints/attributes). The 🌐 toggle must re-render
    whatever you add — remember rendered state (see `CUR`, `EXT` in
    `index.html`) so a language switch repaints it live. Czech prose: n-dash
    (–), never m-dash; no kalky.

## Commands

```bash
python3 -m pytest                 # the whole suite, offline, seconds
python3 server.py                 # dashboard http://localhost:8099 (+ /setup)
python3 pipeline.py --config      # headless run from config/segsmart.json
python3 -m gen.synth              # regenerate synthetic data (partner-shop schema)
docker compose up --build         # the shippable thing (app + Ollama sidecar)
```

CI (`.github/workflows/ci.yml`) runs pytest on every push/PR. Keep it green.

## Gotchas that already bit us (don't re-learn them)

- **Reasoning LLMs return empty under Ollama's `format:"json"`** (qwen3.6 MoE).
  Call `/api/chat` *without* format and extract the fenced JSON block —
  `seg/util.py::extract_json` handles fences/prose/think-tags. Plain models
  (gemma) are fine with `format:"json"`.
- **The UCI "C-invoice" cancellation rule once silently deleted 100 % of rows**
  whose order ids merely started with C (`CZ2025-001`, synthesized ids from
  c-prefixed emails). It is now `[Cc]\d+` fullmatch, never applied to
  synthesized ids. If you touch `_finalize`, run `tests/test_messy.py`.
- **Czech/German exports**: decimal comma, `;` delimiter, cp1250 encoding,
  `d.m.Y` dates. `seg/sniff.py` + `_to_num` + `_parse_dates` exist precisely
  for this; don't bypass them with a bare `pd.read_csv`.
- **`tests/test_messy.py` is a contract, not a sample**: 12 hostile encodings
  of the *same* transactions must converge to the *same* numbers. New ingestion
  features should extend it.
- **Ollama model choice**: `gemma4:e4b` = fast interactive default;
  `gemma4:26b` too slow on CPU (timeouts); `qwen3.6:35b` = quality Czech copy,
  ~4 min/card on CPU. Selected via `SEG_LLM_MODEL`.
- **LLM impact numbers are banned**: campaign cards get deterministic estimates
  (`seg/campaigns.py::RESPONSE` rates) and deterministic priority by revenue
  opportunity. The LLM writes copy, never numbers.
- **LLM discount codes AND discount values are banned**: models fabricate
  Czech-lettered codes and promise percentages nobody approved. Prompts forbid
  both; `strip_voucher_codes` scrubs codes; `has_invented_discount` demotes a
  card promising concrete values to rule-based fallback copy. Real discounts
  enter only via `apply_discount` (owner input, validated). The
  `_INVENTED_DISCOUNT` regex covers `%`, currency-first (`€50`), word currency
  after the amount (`100 Kč`) and symbol-after-the-amount (`50€`) — symbols are
  non-word chars so a trailing `\b` silently misses them; that gap shipped
  twice, keep the symbol branch.
- **Two language knobs, never merge them**: UI chrome language = client-side
  toggle (each page's `UI` dict, localStorage `seg_ui_lang`); content language
  (cards, warnings) = `output.language` per run, in `result.meta.language`.
  Wiring new UI strings into both dicts is hard rule #11 (enforced by
  `tests/test_i18n.py`) — this bullet is just the *why two dicts exist*.
- **File sources from the HTTP API are confined to `data/`**
  (`seg/config.py::fetch_raw trusted_paths`) — never relax this; it's what
  stops `/api/preview_source` from reading arbitrary local files.
- **JSON files the server serves are written via `seg.util.atomic_write_json`**
  (temp + `os.replace`) — plain `json.dump` can serve a partial file under
  the threaded server.
- **`${ENV_VAR}` in config values** is expanded at *use* time
  (`seg/config.py::_env`), never written back — keeps secrets out of the file.

## Where things live

| Path | Role |
|---|---|
| `seg/sniff.py` → `seg/loader.py` | bytes → raw frame → canonical frame |
| `seg/mapping.py` | header+samples → proposed column mapping (LLM + heuristic) |
| `seg/features.py` → `seg/segment.py` | RFM + behavioral features → segments (rules + KMeans cross-check) |
| `seg/seasonality.py`, `seg/campaigns.py` | monthly index; LLM campaign cards |
| `seg/external.py` | owner-uploaded daily factors (FX/weather/promo) → revenue impact, scored against the persisted no-PII daily series (`/api/external_impact`) |
| `seg/products.py` | product-mix cross-tab: segment × category → revenue + customers. Categories come from `seg/catalog.py` first, else local LLM / heuristic, or a `category` column the source provides. Stored as `product_mix` in `result.json`. |
| `seg/catalog.py` | product-id → `cat_1`/`cat_2` hierarchy from `data/product_catalog.csv`; unknown ids generated via local LLM (Ollama) and appended, heuristic fallback offline. |
| `seg/config.py`, `seg/connectors.py` | local config file; SQL/BQ/Shoptet fetch |
| `pipeline.py` | orchestration → `out/result.json` |
| `server.py`, `index.html`, `setup.html` | stdlib HTTP; dashboard; data setup |
| `ad.html`, `ad.cs.html` | standalone marketing one-pager (`/ad`, `/ad-cs`); self-contained EN + CS sibling files cross-linked in the header — outside the app i18n contract (edit both together; Czech follows the n-dash / no-kalk rules) |
| `gen/` | synthetic Czech e-shop data (templated, no LLM) |

## Style

Match what's here: small modules, docstrings that state the *why*, comments only
for non-obvious constraints. Czech strings: ASCII-safe code, UTF-8 data;
n-dash (–) not m-dash in Czech prose.
