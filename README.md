# nifty500-llm-analyst

Personal, recommendation-only research pipeline for Indian equities (Nifty 500 universe). An LLM analyst layer writes thesis memos and conviction scores; a deterministic score engine has the final say on every buy/sell recommendation. Long-only cash, positional holds of months–quarters.

**Not investment advice. Personal research tool for the owner's own portfolio (<5% allocation).**

## Architecture

```
DATA (daily cron)          QUANT SCREEN (nightly)        LLM LAYER (weekly)
NSE bhavcopy ─┐            momentum ranks (21/63/126d)   structured packet per
yahoo .NS  ───┼→ parquet → quality (ROCE/D-E/accruals)→ stock → stance,
screener.in   │            valuation vs own history     conviction 0-100,
RSS/news      │            earnings surprise            thesis memo, risks
announcements ┘            sector relative strength     sector-cycle stage
                                 ↓                            ↓
                        deterministic SCORE ENGINE (final say)
                        blend(quant, llm_conviction×agreement) × cycle_mult − penalties
                        HARD RULES: SL=2×ATR(14), ≤10%/position, ≤30%/sector,
                        ≤20 positions, portfolio circuit breaker
                        rule-conflicts → HUMAN REVIEW queue
                                 ↓
                    weekly report → markdown → static site → GitHub Pages
                    paper ledger logs every reco for validation
```

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Ingestion skeleton + CI workflows | done |
| 1 | Factor engine + sector RS + weekly quant screen (no LLM) | current |
| 2 | LLM analyst memos (`nla/llm_client.py`, opencode Zen / Gemini) | planned |
| 3 | Score engine, vol-targeted sizing, stops, conflict flags, paper ledger | planned |
| 4 | Sector-cycle classifier + dynamic event watchlist | planned |
| 5 | Tune weights from paper results; execution stays out of scope | planned |

## Scheduling (GitHub Actions)

| Workflow | Cron (UTC) | IST | Does |
|---|---|---|---|
| `daily-scan` | `45 13 * * 1-5` | 19:15 Mon–Fri | refresh universe, ingest last trading day, commit parquet + status; writes a human-readable status page to `reports/daily/` (published to Pages on the next Sunday build) |
| `weekly-review` | `30 3 * * 0` | 09:00 Sun | weekly report → `reports/weekly/`, build + deploy Pages site |
| `backfill` | manual | manual | repair a date range (`start`/`end` dispatch inputs) |

Scheduled runs may be delayed minutes–hours by GitHub; daily commits also keep the scheduler alive past GitHub's 60-day inactivity cutoff.

## Universe selection

Two modes, controlled by env (`NLA_UNIVERSE_MODE`, `NLA_UNIVERSE_SIZE`; defaults `liquid`, `1000`):

- `nifty500` — official NSE Nifty 500 constituent list
- `liquid` — top-N stocks by **median daily turnover** over the last ~20 sessions (from committed bhavcopy parquets), with floors: price ≥ ₹20, median turnover ≥ ₹0.5cr, listed on ≥ half the window days. At N=1000 the marginal name still trades ~₹6cr/day.

Membership snapshots are archived under `data/reference/universe_history/` whenever composition changes, keeping churn auditable for the Phase 3 paper ledger. Churn is damped with hysteresis: a new name must rank inside the top N to enter, but an existing member only drops out once it falls outside the top N×1.1 — so boundary names don't flap in and out daily. Price history for dropped names keeps accumulating (bhavcopy is all-market), and dated snapshots allow reconstructing membership as of any past date. Known limitation: sector-index coverage (~190 names) and yahoo backfill depth thin out in the smallcap tail; tail momentum starts accruing from ingestion day via bhavcopy.

## Data waterfall

1. NSE full-delivery bhavcopy CSV: `https://archives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv` — CDN-backed, usually reachable from cloud runners.
2. Fallback: Yahoo Finance `.NS` daily closes (chunked downloads) → close-only parquet for that day.
3. Every day is ingested once: reruns and retries are idempotent against existing `data/prices/YYYY-MM-DD.parquet`.

NSE blocks most cloud/datacenter IPs. If bhavcopy ever hard-fails from runner IPs, capture the workflow logs — known upgrade path is `BennyThadikaran/NseIndiaApi` server mode.

## Security model

- Public repo ⇒ zero secrets in code or git history, ever.
- Keys live ONLY as GitHub encrypted Actions secrets (`GEMINI_API_KEY`, optional `OPENCODE_API_KEY`), injected as env vars; GitHub auto-redacts secret values in run logs.
- `.env` is gitignored (local dev only); `.env.example` holds placeholders only.
- Never paste keys into issues, commits, or workflow files. If a key leaks: revoke immediately at the provider.

## Local development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python -m nla.daily                                              # ingest last trading day
python -m nla.backfill --start 2026-08-01 --end 2026-08-21      # repair a range
python -m nla.weekly                                            # weekly report + build site/
python -m nla.site                                              # rebuild site/ from reports/
```

## First push

Create an empty public repo `nifty500-llm-analyst` on github.com (no README/license), then:

```bash
git remote add origin git@github.com:<username>/nifty500-llm-analyst.git
git push -u origin main
```

After pushing: add secrets (Settings → Secrets and variables → Actions), enable Pages with Source = **GitHub Actions**, then manually trigger `weekly-review` once to validate the CI → report → Pages path.
