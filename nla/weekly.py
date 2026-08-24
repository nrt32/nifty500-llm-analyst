import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from nla.config import REPORTS_DIR
from nla.engine import evaluate
from nla.events import detect_events
from nla.factors import momentum_ranks
from nla.history import load_close_history, refresh_from_daily
from nla.ledger import ledger_stats, log_weekly_entries
from nla.memos import get_memo
from nla.report import write_report
from nla.sector import cycle_stage_label, load_sector_map, refresh_sector_map, relative_strength
from nla.site import build_site
from nla.universe import load_active_symbols

IST = timezone(timedelta(hours=5, minutes=30))
TOP_N = 30
MEMO_TOP_N = 10


def week_slug(ref: datetime | None = None) -> str:
    moment = ref or datetime.now(IST)
    iso_year, iso_week, _ = moment.date().isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _pct(x) -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{float(x) * 100:+.1f}%"


def _load_fundamentals() -> dict[str, dict]:
    from nla.config import DATA_DIR

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


def build_memos(mom: pd.DataFrame, smap: pd.DataFrame, rs: pd.DataFrame, slug: str) -> dict[str, dict]:
    from nla import llm_client

    memos: dict[str, dict] = {}
    if not llm_client.available():
        return memos
    sym_sector = dict(zip(smap["symbol"].astype(str), smap["sector"].astype(str)))
    sym_rs_rank = dict(zip(rs["sector"], rs["rs_rank"])) if not rs.empty else {}
    fundamentals = _load_fundamentals()
    ok = err = 0
    for _, row in mom.head(MEMO_TOP_N).iterrows():
        symbol = str(row["symbol"])
        sector = sym_sector.get(symbol)
        memo = get_memo(
            symbol,
            row,
            sector,
            sym_rs_rank.get(sector),
            slug,
            fundamentals=fundamentals.get(symbol),
        )
        if memo and "error" not in memo:
            memos[symbol] = memo
            ok += 1
            print(f"memo {symbol}: {memo.get('stance')} / {memo.get('conviction')}")
        elif memo and "error" in memo:
            err += 1
            print(f"memo {symbol} FAILED: {memo.get('error')}")
    print(f"memos built: ok={ok} errors={err}")
    return memos


def render_recs(recs: pd.DataFrame, memos_count: int) -> list[str]:
    lines = [
        "",
        "## Recommendations (score engine output)",
        "",
        f"LLM memos used: {memos_count} of top {MEMO_TOP_N}. "
        "Suggested weight is volatility-targeted (inverse 21d move), capped at "
        "10% per position, 30% per mapped sector, 20 positions. Stop = next-session entry minus the shown %.",
        "",
        "| Rank | Symbol | Sector | Final | Quant | LLM stance/conv | Weight % | Stop % | Flag |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, (_, r) in enumerate(recs.iterrows(), start=1):
        llm_txt = "-" if r["llm_conviction"] is None or pd.isna(r["llm_conviction"]) else f"{r['llm_stance']}/{int(r['llm_conviction'])}"
        lines.append(
            f"| {i} | {r['symbol']} | {r['sector_tag']} | {r['final_score']} | {r['quant_score']} "
            f"| {llm_txt} | {r['suggested_weight_pct']} | {_pct(r['stop_pct']) if r['stop_pct'] else '-'} | {r['flag'] or ''} |"
        )
    return lines


def write_review_queue(slug: str, recs: pd.DataFrame, memos: dict[str, dict]) -> int:
    flagged = recs[recs["flag"] == "HUMAN_REVIEW"]
    if flagged.empty:
        return 0
    lines = [f"# Human Review Queue {slug}", "", "Quant and LLM disagree beyond tolerance - resolve manually before acting.", ""]
    for _, r in flagged.iterrows():
        memo = memos.get(str(r["symbol"]), {})
        lines += [
            f"## {r['symbol']} - quant {r['quant_score']} vs LLM {r['llm_conviction']} ({memo.get('stance', '-')})",
            "",
            str(memo.get("thesis", "")),
            "",
        ]
        for risk in memo.get("risks", []):
            lines.append(f"- {risk}")
        lines.append("")
    write_report(REPORTS_DIR / "review" / f"{slug}.md", "\n".join(lines) + "\n")
    return len(flagged)


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
    memos_count: int,
    review_n: int,
) -> str:
    first, last = hist["date"].min(), hist["date"].max()
    covered = len(smap)
    sym_sector = dict(zip(smap["symbol"], smap["sector"]))
    lines = [
        f"# Weekly Quant Screen {slug}",
        "",
        f"Generated {date.today().isoformat()} - quant factors + score engine; LLM memos where a key is configured.",
        "",
        "## Universe health",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Close history rows | {len(hist)} |",
        f"| Symbols with history | {hist['symbol'].nunique()} in universe |",
        f"| Data window | {first} to {last} |",
        f"| Sector map coverage | {covered} / {hist['symbol'].nunique()} (NSE sector indices) |",
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
    lines += render_recs(recs, memos_count)
    if review_n:
        lines += ["", f"**{review_n} conflict(s) routed to reports/review/{slug}.md - resolve before acting.**"]
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
        "Tranches are recorded at signal-day close for research purity; the daily run then fills each row's "
        "`exec_price` at the NEXT session's open and arms its stop. Treat tables as watchlists for coming sessions, not fills.",
        "",
        "## How to read this report",
        "",
        "- **Sector RS**: equal-weight sector baskets vs whole-universe average return over 21/63/126d; "
        "RS Score = mean percentile rank of excess returns. Covers only NSE-sector-index members (~188 names); "
        "these 14 lists are not India's full industry taxonomy and overlap. Stage: Leading/Pullback/Weakening/Improving/Lagging.",
        "- **Momentum Score**: mean cross-sectional percentile of a stock's 21d/63d/126d total returns (0-100). "
        "Windows skipped below minimum history gates.",
        "- **Recommendations**: final = blend(quant, LLM conviction when available) x sector-cycle multiplier x "
        "fundamental/volatility penalties. HUMAN_REVIEW rows have quant-vs-LLM disagreement >= 30 points.",
        "- **Prices are unadjusted closes** from NSE bhavcopy / Yahoo; dividend adjustments are not applied.",
        "- Nothing here is investment advice.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    slug = week_slug()
    try:
        _run(slug)
        return 0
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
        refresh_sector_map()
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
    memos = build_memos(mom, smap, rs, slug)
    recs = evaluate(mom.copy(), pd.DataFrame(), smap, hist=hist, rs=rs, memos=memos, fundamentals=_load_fundamentals()).head(15)
    review_n = write_review_queue(slug, recs, memos)
    try:
        events = detect_events()
    except Exception:
        events = pd.DataFrame(columns=["symbol", "event", "detail"])
    try:
        added, _created = log_weekly_entries(mom, smap, slug, str(hist["date"].max()))
    except Exception:
        added = -1
    write_report(
        REPORTS_DIR / "weekly" / f"{slug}.md",
        render_screen(slug, mom, rs, hist, smap, ledger_stats(), added, recs, events, len(memos), review_n),
    )
    pages = build_site()
    print(f"weekly screen written: reports/weekly/{slug}.md (ledger +{added}, memos {len(memos)}, review {review_n}, site pages: {pages})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
