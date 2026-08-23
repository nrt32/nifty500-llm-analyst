import sys
from datetime import date

import pandas as pd

from nla.config import REPORTS_DIR
from nla.factors import momentum_ranks
from nla.history import load_close_history, refresh_from_daily
from nla.report import write_report
from nla.sector import load_sector_map, refresh_sector_map, relative_strength
from nla.site import build_site
from nla.universe import load_active_symbols

TOP_N = 30


def week_slug(ref: date | None = None) -> str:
    d = ref or date.today()
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _pct(x: float) -> str:
    if pd.isna(x):
        return "-"
    return f"{x * 100:+.1f}%"


def render_screen(slug: str, mom: pd.DataFrame, rs: pd.DataFrame, hist: pd.DataFrame, smap: pd.DataFrame) -> str:
    first, last = hist["date"].min(), hist["date"].max()
    covered = len(smap)
    sym_sector = dict(zip(smap["symbol"], smap["sector"]))
    lines = [
        f"# Weekly Quant Screen {slug}",
        "",
        f"Generated {date.today().isoformat()} - deterministic factors only, no LLM input yet.",
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
        "| Sector | Names | Ret 21d | Excess 21d | Ret 63d | Excess 63d | Ret 126d | Excess 126d | RS Score | Rank |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for _, r in rs.iterrows():
        lines.append(
            f"| {r['sector']} | {r['n_names']} | {_pct(r.get('ret_21d'))} | {_pct(r.get('excess_21d'))} "
            f"| {_pct(r.get('ret_63d'))} | {_pct(r.get('excess_63d'))} "
            f"| {_pct(r.get('ret_126d'))} | {_pct(r.get('excess_126d'))} | {r['rs_score']} | {r['rs_rank']} |"
        )
    top = mom.head(TOP_N)
    lines += [
        "",
        f"## Top {len(top)} momentum",
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
    lines += [
        "",
        "---",
        "_Not investment advice. Personal research tool for the repository owner. "
        "Factor ranks are descriptive, not recommendations; score engine and risk rules land in Phase 3._",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    slug = week_slug()
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
    write_report(REPORTS_DIR / "weekly" / f"{slug}.md", render_screen(slug, mom, rs, hist, smap))
    pages = build_site()
    print(f"weekly screen written: reports/weekly/{slug}.md (site pages: {pages})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
