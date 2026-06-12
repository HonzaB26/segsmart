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
- **LLM discount codes are banned too**: models fabricate Czech-lettered codes
  no shop accepts. Prompts forbid them AND `strip_voucher_codes` scrubs the
  output. Real codes enter only via `apply_discount` (owner input, validated).
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
| `seg/config.py`, `seg/connectors.py` | local config file; SQL/BQ/Shoptet fetch |
| `pipeline.py` | orchestration → `out/result.json` |
| `server.py`, `index.html`, `setup.html` | stdlib HTTP; dashboard; data setup |
| `gen/` | synthetic Czech e-shop data (templated, no LLM) |

## Style

Match what's here: small modules, docstrings that state the *why*, comments only
for non-obvious constraints. Czech strings: ASCII-safe code, UTF-8 data;
n-dash (–) not m-dash in Czech prose.
