# Contributing to SegSmart

Thanks for jumping in 🎉 This is an alpha MBA project — friendly territory, so
don't worry about getting things perfect.

## Setup

```bash
pip install -r requirements.txt
python3 -m gen.synth                 # synthetic demo data (no real customers)
python3 server.py                    # http://localhost:8099
```

Run the modules standalone to see them work:

```bash
python3 -m seg.loader        # canonical schema + summary
python3 -m seg.segment       # RFM vs KMeans + agreement
python3 -m seg.campaigns --no-llm    # campaign cards (rule-based, instant)
```

## The one rule that keeps the codebase clean

**Everything consumes the canonical order-line frame** (see `seg/loader.py`):

```
customer_id · order_id · order_date · quantity · unit_price · line_value · product · country
```

New data source? Add an adapter in `seg/connectors.py` that fetches a raw frame
and returns `load_dataframe(raw, mapping)`. Don't touch the analytics — that's
the whole point of the seam.

## Good first issues

- A new **connector** (your shop's platform) + its column mapping.
- **Campaign export** to an ESP (Ecomail / SmartEmailing / Mailchimp).
- **Czech copy** polish in `seg/campaigns.py` (the LLM occasionally slips).
- A **churn/propensity** signal per segment.
- Dashboard niceties (segment drill-down, CSV export of a segment).

## Conventions

- Plain Python, standard library where reasonable; heavy connector deps imported
  lazily so the core image stays slim.
- No real customer data in the repo — ever. Use `gen/` to synthesize.
- Keep the dashboard dependency-free (single-file SVG, works offline).
- The UI is bilingual (EN/CS). Any user-facing string you add must be wired to
  a key in **both** the `en` and `cs` dicts (`data-i` for static text,
  `t()`/`tf()` for dynamic) — `tests/test_i18n.py` enforces it. See AGENTS.md
  rule #11.

## PRs

Branch, commit, open a PR against `main`. Describe what and why. CI runs the
full pytest suite (`python3 -m pytest` — offline, a few seconds); keep it green.
For the lay of the land see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); if
you're pointing a coding agent at the repo, it reads [AGENTS.md](AGENTS.md).
