import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from nla import entry
from nla.config import DATA_DIR, REPORTS_DIR
from nla.engine import evaluate
from nla.events import detect_events
from nla.factors import momentum_ranks
from nla.history import load_close_history, refresh_from_daily
from nla.ledger import ledger_stats, log_tranche
from nla.report import write_report
from nla.sector import cycle_stage_label, load_sector_map, register_pending_symbols, relative_strength
from nla.site import build_site
from nla.universe import load_active_symbols

IST = timezone(timedelta(hours=5, minutes=30))
TOP_N = 30


def week_slug(ref: datetime | None = None) -> str:
    moment = ref or datetime.now(IST)
    iso_year, iso_week, _ = moment.date().isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _pct(x) -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{float(x) * 100:+.1f}%"


def _load_fundamentals() -> dict[str, dict]:
    fdir = DATA_DIR / "fundamentals"
    out: dict[str, dict] = {}
    if not fdir.exists():
        return out
    for path in fdir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            out[str(payload.get("symbol", path.stem)).upper()] = payload
        except Exception:
            continue
    return out


import json


def render_committee(results: list[dict], gated: int) -> list[str]:
    included = sum(1 for r in results if r["decision"] == "INCLUDED")
    lines = [
        "",
        "## Entry Committee",
        "",
        "Candidates must first pass a hard technical gate (uptrend above EMA50/200, RSI 45-78, "
        "extension <=15% over EMA21, plus a BREAKOUT or PULLBACK trigger). Survivors face a two-agent debate: "
        "a Bull researcher argues for inclusion, a Bear researcher argues to exclude. A deterministic judge rules; "
        "strong bear vetoes reject the candidate outright.",
        "",
        f"Debated {len(results)} of {gated} gate-passers. Outcomes: **{included} included**, "
        f"{sum(1 for r in results if r['decision'] == 'REJECTED')} rejected.",
        "",
        "| Symbol | Sector | Style | RSI | Ext% | VolRatio | Bull | Bear | Decision |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        d = r["gate_detail"]

        def side(side_name: str) -> str:
            s = r[side_name]
            return f"{s['verdict']}/{s['conviction']}"

        lines.append(
            f"| {r['symbol']} | {r['sector']} | {r['style']} | {d['rsi']} | {d['ext_pct']} | {d['vol_ratio']} "
            f"| {side('bull')} | {side('bear')} | {r['decision']} |"
        )
    lines += ["", "**Reasoning, name by name:**", ""]
    for r in results:
        lines += [
            f"- **{r['symbol']} ({r['style']}, decision: {r['decision']})**",
            f"  - Bull ({r['bull']['conviction']}): {r['bull']['reason']}",
            f"  - Bear ({r['bear']['conviction']}): {r['bear']['reason']}",
            f"  - Judge: {r['why']}",
        ]
    return lines


def render_screen(
    slug: str,
    mom: pd.DataFrame,
    rs: pd.DataFrame,
    hist: pd.DataFrame,
    smap: pd.DataFrame,
    stats: dict[str, int],
    added: int,
    recs: pd.DataFrame,
    events: pd.DataFrame,
    committee: list[dict],
    gated: int,
    scan_stats: dict | None = None,
) -> str:
    scan_stats = scan_stats or {}
    first, last = hist["date"].min(), hist["date"].max()
    covered = len(smap)
    sym_sector = dict(zip(smap["symbol"], smap["sector"]))
    included = [r for r in committee if r["decision"] == "INCLUDED"]
    rejected_n = sum(1 for r in committee if r["decision"] == "REJECTED")
    lines = [
        f"# Weekly Quant Screen {slug}",
        "",
        f"Generated {date.today().isoformat()} - quant factors, hard technical gates, and a bull/bear entry committee.",
        "",
        "## This Week's Action Summary",
        "",
    ]
    if added > 0 and included:
        names = ", ".join(r["symbol"] for r in included)
        lines += [
            f"**{added} new paper entries logged this week: {names}.**",
            "If replicating manually: buy at the NEXT trading session's open (not Friday's close), "
            "arm the stop shown in the ranked table below, size by the Weight % column. Settlement and "
            "stop-tracking happen automatically from tomorrow's daily run.",
        ]
    else:
        lines += [
            "**No new entries were added to the paper portfolio this week.** That is a deliberate outcome, not an error.",
            "The committee debated every qualifying setup and none cleared the bar for a rare, high-conviction entry.",
        ]
        if committee:
            lines.append(
                f"Of {len(committee)} gate-passing candidates debated: {len(included)} included, {rejected_n} rejected."
            )
    if scan_stats.get("fail_counts"):
        fc = ", ".join(f"{k}: {v}" for k, v in sorted(scan_stats["fail_counts"].items(), key=lambda kv: -kv[1]))
        lines += [
            "",
            f"Why so few? Of {scan_stats.get('scanned', '-')} top-momentum names screened, failures were: {fc}. "
            + (
                "The dominant failure is **no-trigger**: uptrends that are not at a breakout or pullback point yet - "
                "watchlist material, check back weekly."
                if scan_stats["fail_counts"].get("no-trigger")
                else ""
            ),
        ]
    if scan_stats.get("near_misses"):
        nm = ", ".join(f"{n['symbol']} ({n['detail']['dist_52w_high']:+.1f}% from 52w-high, vol x{n['detail']['vol_ratio']})" for n in scan_stats["near_misses"][:8])
        lines += [f"**Near misses - one trigger away from eligibility:** {nm}."]
    lines += [
        "",
        "---",
    ]
    lines += [
        "## Universe health",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Close history rows | {len(hist)} |",
        f"| Symbols with history | {hist['symbol'].nunique()} in universe |",
        f"| Data window | {first} to {last} |",
        f"| Sector map coverage | {covered} / {hist['symbol'].nunique()} (GICS, static map) |",
        "",
        "## Sector relative strength",
        "",
        "| Sector | Names | Stage | Ret 21d | Excess 21d | Ret 63d | Excess 63d | Ret 126d | Excess 126d | RS Score | Rank |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for _, r in rs.iterrows():
        stage = cycle_stage_label(r.get("ret_21d"), r.get("ret_63d"), r.get("ret_126d"))
        lines.append(
            f"| {r['sector']} | {r['n_names']} | {stage} | {_pct(r.get('ret_21d'))} | {_pct(r.get('excess_21d'))} "
            f"| {_pct(r.get('ret_63d'))} | {_pct(r.get('excess_63d'))} "
            f"| {_pct(r.get('ret_126d'))} | {_pct(r.get('excess_126d'))} | {r['rs_score']} | {r['rs_rank']} |"
        )
    top = mom.head(TOP_N)
    lines += [
        "",
        f"## Top {len(top)} momentum (watchlist)",
        "",
        "| Rank | Symbol | Sector | Price | Ret 21d | Ret 63d | Ret 126d | Score |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for _, r in top.iterrows():
        lines.append(
            f"| {r['momentum_rank']} | {r['symbol']} | {sym_sector.get(r['symbol'], '-')} "
            f"| {r['price']:.2f} | {_pct(r.get('ret_21d'))} | {_pct(r.get('ret_63d'))} "
            f"| {_pct(r.get('ret_126d'))} | {r['momentum_score']} |"
        )
    bottom = mom.tail(5)
    lines += [
        "",
        "## Bottom 5 (avoid list)",
        "",
        "| Rank | Symbol | Price | Ret 63d | Ret 126d | Score |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for _, r in bottom.iloc[::-1].iterrows():
        lines.append(
            f"| {r['momentum_rank']} | {r['symbol']} | {r['price']:.2f} "
            f"| {_pct(r.get('ret_63d'))} | {_pct(r.get('ret_126d'))} | {r['momentum_score']} |"
        )
    lines += render_committee(committee, gated)
    # Ranked watchlist (quality-adjusted) — distinct from actual paper portfolio
    lines += ["", "## Quality-adjusted watchlist (top 15 by final score)", ""]
    lines += [
        "Watchlist only — not trades. Final score blends momentum (50%), sector-relative quality (30%: ROCE/ROE/D/E/P/E/EBIT margin, ranked within GICS sector), and LLM conviction (20% when present).",
        "",
        "| Rank | Symbol | Sector | Final | Quant | Quality | Weight % | Stop % |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    watchlist = recs.head(15)
    for i, (_, r) in enumerate(watchlist.iterrows(), start=1):
        stop_txt = _pct(r["stop_pct"]) if r["stop_pct"] else "-"
        q = f"{r['quality_score']:.0f}" if "quality_score" in r and not pd.isna(r["quality_score"]) else "-"
        lines.append(f"| {i} | {r['symbol']} | {r['sector_tag']} | {r['final_score']} | {r['quant_score']} | {q} | {r['suggested_weight_pct']} | {stop_txt} |")
    lines += [
        "",
        "Weights shown are *what would be used* if the watchlist names had passed the entry committee; only committee-INCLUDED names ever enter the paper ledger below.",
        "",
        "## Current paper portfolio",
        "",
    ]
    open_positions = stats.get("open", 0)
    if open_positions == 0:
        lines += ["No open paper positions — the ledger is empty. The system is waiting for rare, high-conviction setups (typically 0–2 per week)."]
    else:
        lines += [f"{open_positions} open position(s) — see Paper ledger section and `data/ledger/paper_ledger.csv` for details."]
    lines += [
        "",
        "## Event watchlist",
        "",
        "| Symbol | Event | Detail |",
        "| --- | --- | --- |",
    ]
    if events.empty:
        lines.append("| - | no triggers today | - |")
    else:
        for _, e in events.head(25).iterrows():
            lines.append(f"| {e['symbol']} | {e['event']} | {e['detail']} |")
    lines += [
        "",
        "## Paper ledger (shadow validation)",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Open shadow positions | {stats['open']} |",
        f"| Total logged entries | {stats['total']} |",
        f"| Weeks logged | {stats['weeks']} |",
        f"| Awaiting next-open fill | {stats.get('pending_exec', 0)} |",
        f"| Filled at next-session open | {stats.get('settled', 0)} |",
        f"| Entries added this run | {added} |",
        "",
        "Exits are layered: initial volatility stop (intraday), chandelier-style trailing stop off the "
        "high-water close, 2-day EMA50 trend break, and a 40-session time stop - all checked daily.",
        "",
        "## How to read this report",
        "",
        "- **Sector RS**: equal-weight GICS baskets vs whole-universe average over 21/63/126d from the static sector map.",
        "- **Entry Committee**: technical gate -> bull/bear agent debate -> deterministic judge. Only INCLUDED names enter the paper ledger.",
        "- **Prices are unadjusted closes** from NSE bhavcopy / Yahoo.",
        "- Nothing here is investment advice.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    slug = week_slug()
    try:
        _run(slug)
        return 0
    except SystemExit:
        raise
    except Exception:
        import traceback

        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        try:
            write_report(REPORTS_DIR / "weekly" / f"error-{slug}.md", f"# Weekly Run Error {slug}\n\n```\n{tb}\n```\n")
        except Exception:
            pass
        return 1


def _run(slug: str) -> None:
    try:
        refresh_from_daily()
    except Exception:
        pass
    try:
        register_pending_symbols(load_active_symbols())
    except Exception:
        pass
    hist = load_close_history()
    try:
        active = set(load_active_symbols())
        hist = hist[hist["symbol"].isin(active)]
    except Exception:
        pass
    smap = load_sector_map()
    mom = momentum_ranks(hist)
    rs = relative_strength(hist, smap)
    fundamentals = _load_fundamentals()
    try:
        from nla.quality import compute_quality_scores

        qdf = compute_quality_scores(fundamentals, smap)
        quality_map = dict(zip(qdf["symbol"], qdf["quality_score"]))
    except Exception:
        quality_map = {}
    committee, gated, scan_stats = entry.run_committee(mom, hist, smap, fundamentals, slug)
    recs = evaluate(mom.copy(), pd.DataFrame(), smap, hist=hist, rs=rs, memos={}, fundamentals=fundamentals, quality_map=quality_map)
    last_date = str(hist["date"].max())
    prices_last = hist[hist["date"].astype(str) == last_date].set_index("symbol")["close"]
    rank_lookup = mom.set_index("symbol")[["momentum_rank", "momentum_score"]]
    entries = []
    from nla.engine import volatility_proxy, stop_pct_from_vol

    for symbol in entry.included_entries(committee):
        if symbol not in prices_last.index:
            continue
        style = next((r["style"] for r in committee if r["symbol"] == symbol), "-")
        vol = volatility_proxy(hist, symbol)
        entries.append(
            {
                "symbol": symbol,
                "sector": smap[smap["symbol"] == symbol]["sector"].iloc[0] if symbol in set(smap["symbol"]) else "-",
                "style": style,
                "signal_price": float(prices_last[symbol]),
                "momentum_rank": int(rank_lookup.loc[symbol, "momentum_rank"]) if symbol in rank_lookup.index else 0,
                "momentum_score": float(rank_lookup.loc[symbol, "momentum_score"]) if symbol in rank_lookup.index else 0.0,
                "stop_pct": stop_pct_from_vol(vol),
            }
        )
    try:
        added, _created = log_tranche(slug, last_date, entries)
    except Exception as exc:
        print(f"tranche logging failed: {exc}")
        added = -1
    try:
        events = detect_events()
    except Exception:
        events = pd.DataFrame(columns=["symbol", "event", "detail"])
    write_report(
        REPORTS_DIR / "weekly" / f"{slug}.md",
        render_screen(slug, mom, rs, hist, smap, ledger_stats(), added, recs, events, committee, gated, scan_stats),
    )
    pages = build_site()
    print(f"weekly screen written: reports/weekly/{slug}.md (ledger +{added}, site pages: {pages})")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
