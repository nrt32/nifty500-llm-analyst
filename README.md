# nifty500-llm-analyst

Research pipeline for Indian equities: an LLM analyst layer writes thesis memos and conviction scores over a quant screen; a deterministic score engine has the final say on every buy/sell recommendation. Universe is the top-N most liquid listed stocks (default 1000, configurable). Long-only cash, positional holds of months–quarters.

**Not investment advice. Personal research tool.**

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
| 1 | Factor engine + sector RS + weekly quant screen (no LLM) | done |
| 2 | LLM analyst memos (`nla/llm_client.py` + `nla/memos.py`, opencode Zen / Gemini) | done |
| 3 | Score engine, vol-targeted sizing, stops, conflict flags, paper ledger | done (v1) |
| 4 | Sector-cycle stage labels + event watchlist (52w-high/volume); announcements via NseIndiaApi later | partial |
| 5 | Tune weights from paper results (monthly scorecard harness ready) | awaiting data |

## LLM analyst layer

`nla/llm_client.py` is provider-agnostic via env (`NLA_LLM_PROVIDER`=opencode\|gemini, model names, base URL, keys). The weekly run builds structured packets (momentum stats, sector RS rank, cached screener ratios, Google News RSS headlines) for the top 10 names and requests strict-JSON memos (stance, conviction 0-100, thesis, risks), cached under `data/memos/<week>/`. Without a key the step skips gracefully and the engine runs quant-only. Where quant and LLM conviction diverge by >= 30 points the candidate is flagged **HUMAN_REVIEW** and routed to `reports/review/<week>.md` instead of receiving a weight - conflicts are never auto-resolved.

## Score engine & risk rules

`nla/engine.py`: final = blend(0.65 x quant + 0.35 x LLM conviction when present) x sector-cycle multiplier (0.92-1.05 from RS percentile) x penalties (ROCE<8 -> x0.93, P/E>60 -> x0.95, <130 sessions -> x0.92). Sizing is volatility-targeted (inverse mean absolute daily move over 21d), normalized to 100%, capped at 10%/position, 30%/mapped sector, 20 positions max. Stops = 2x mean daily move over 14d clamped to 8-20%; armed when a tranche settles and checked against daily lows by `check_stop_exits()` in the daily run. Monthly `python -m nla.scorecard` (workflow: monthly-scorecard) scores ledger tranches vs the equal-weight universe benchmark on both signal and execution bases.

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

Membership snapshots are archived under `data/reference/universe_history/` whenever composition changes, keeping churn auditable for the Phase 3 paper ledger. Churn is damped with hysteresis: a new name must rank inside the top N to enter, but an existing member only drops out once it falls outside the top N×1.1 — so boundary names don't flap in and out daily. Price history for dropped names keeps accumulating (bhavcopy is all-market), and dated snapshots allow reconstructing membership as of any past date. Sector tags come from a **static sector map** (`data/reference/sector_map.csv`) covering **all 1,019 names** with Yahoo/Tickertape GICS sectors + industries (11 equity sectors plus an Etf bucket that is excluded from sector baskets and memos treat accordingly). The map is built once via `python -m nla.sector --build` (resumable), treated as long-term static, and new/unmapped universe entrants are auto-logged to `sector_pending.csv` for manual curation. Known limitation: yahoo backfill depth thins out in the smallcap tail; tail momentum starts accruing from ingestion day via bhavcopy.

## Data waterfall

1. NSE full-delivery bhavcopy CSV: `https://archives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv` — CDN-backed, usually reachable from cloud runners.
2. Fallback: Yahoo Finance `.NS` daily closes (chunked downloads) → close-only parquet for that day.
3. Every day is ingested once: reruns and retries are idempotent against existing `data/prices/YYYY-MM-DD.parquet`.

NSE blocks most cloud/datacenter IPs. If bhavcopy ever hard-fails from runner IPs, capture the workflow logs — known upgrade path is `BennyThadikaran/NseIndiaApi` server mode.

### Sources evaluated & rejected (tested Aug 2026)

- **BSE**: legacy bhavcopy zip serves homepage HTML; UDiFF CSV 404; `api.bseindia.com` endpoints hostile even with browser headers. Do not revisit without a specific unmet need.
- **Moneycontrol**: RSS feeds Akamai-blocked (Access Denied/403). Rejected; fundamentals stay on screener.in.
- **News context for LLM memos**: Google News RSS (`news.google.com/rss/search?q=...`) — validated working, no key, adopted for Phase 2.
- **Corporate announcements / bulk deals (Phase 4)**: planned path is `NseIndiaApi` server mode against NSE, not BSE scraping.

## Fundamentals source

Two robots-permitted, keyless sources feed `data/fundamentals/<SYMBOL>.json`:

1. **screener.in** (`nla/fundamentals.py`): ratio strip (ROCE, ROE, P/E, book value, dividend yield), 30-day TTL cache, 2s polite delay. Known gap: debt-to-equity no longer shown on its pages.
2. **tickertape.in** (`nla/tickertape.py`, Zerodha-incubated): search API resolves NSE symbol → internal slug; each stock page embeds server-side JSON with **GICS sector/industry**, scorecard tags (performance/valuation/growth/profitability/red-flags/entry-point), P/E & TTM P/E vs industry, P/B, beta, 52-week range, market-cap rank, and full annual balance-sheet/income/cash-flow statements — **debt-to-equity is computed from `balTdeb/balTeq`**, closing the screener gap. Same TTL/delay discipline; ~60 fields per symbol.

Both are consumed by LLM memo packets. Full-universe refresh at N=1000 costs roughly an hour of polite crawling per source per month — run manually or via a future scheduled job, never per-week.

## Paper ledger

Every weekly run logs the screen's top 20 as hypothetical long entries into `data/ledger/paper_ledger.csv` (append-only, one tranche per ISO week, idempotent on reruns). Two prices are tracked: `signal_price` (decision-day close, for signal purity) and `exec_price` — auto-filled by the daily run at the **next session's open**, which is what a person acting on the report could actually get. The gap between the two is the measured execution cost of acting on weekly signals. Rows stay `open` until the Phase 3 score engine defines stops and exits; monthly scoring against the Nifty 500 TRI benchmark lands with Phase 5. This starts the 3–6 month shadow-validation clock from the first logged week.

## Security model

- Public repo ⇒ zero secrets in code or git history, ever.
- Keys live ONLY as GitHub encrypted Actions secrets (`GEMINI_API_KEY`, optional `OPENCODE_API_KEY`), injected as env vars; GitHub auto-redacts secret values in run logs. Get a Gemini key at aistudio.google.com (free tier).
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

