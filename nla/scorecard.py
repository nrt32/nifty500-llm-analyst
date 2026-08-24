import argparse
import math
import sys
from datetime import date

import pandas as pd

from nla.config import REPORTS_DIR
from nla.ledger import load_ledger
from nla.report import write_report


def _price_on(hist: pd.DataFrame, symbol: str, on_or_before: str) -> tuple[str, float] | None:
    series = hist[(hist["symbol"] == symbol) & (hist["date"].astype(str) <= on_or_before)]
    if series.empty:
        return None
    row = series.sort_values("date").iloc[-1]
    return str(row["date"]), float(row["close"])


def build_scorecard(month: str | None = None) -> pd.DataFrame:
    ledger = load_ledger()
    if ledger.empty:
        return pd.DataFrame()
    if month:
        ledger = ledger[ledger["entry_date"].astype(str).str.startswith(month)]
        if ledger.empty:
            return pd.DataFrame()
    from nla.history import load_close_history

    hist = load_close_history()
    try:
        from nla.universe import load_active_symbols

        hist = hist[hist["symbol"].isin(set(load_active_symbols()))]
    except Exception:
        pass
    latest_date = str(hist["date"].max())
    rows = []
    for week, tranche in ledger.groupby("week"):
        entry_dates = tranche["exec_date"].fillna(tranche["entry_date"]).astype(str)
        start_day = entry_dates.min()
        base = hist[hist["date"].astype(str) <= start_day].sort_values("date")
        base_day = str(base["date"].max()) if not base.empty else start_day
        prices_then = hist[hist["date"].astype(str) == base_day].set_index("symbol")["close"]
        prices_now = hist[hist["date"].astype(str) == latest_date].set_index("symbol")["close"]
        common = prices_then.index.intersection(prices_now.index)
        sessions_held = len(hist[(hist["date"].astype(str) > base_day) & (hist["date"].astype(str) <= latest_date)]["date"].unique())
        bench_ret = float((prices_now[common] / prices_then[common] - 1).mean()) * 100 if (len(common) and sessions_held) else None
        sig_ret = exec_ret = 0.0
        n_sig = n_exec = 0
        for _, r in tranche.iterrows():
            symbol = str(r["symbol"])
            sig = _price_on(hist, symbol, latest_date)
            entry_sig = _price_on(hist, symbol, str(r["entry_date"]))
            if sig and entry_sig and entry_sig[0] != sig[0]:
                sig_ret += (sig[1] / entry_sig[1] - 1) * 100
                n_sig += 1
            exec_price = r.get("exec_price")
            exec_day = r.get("exec_date")
            if pd.notna(exec_price) and isinstance(exec_day, str) and sig and exec_day != sig[0]:
                exec_ret += (sig[1] / float(exec_price) - 1) * 100
                n_exec += 1
        rows.append(
            {
                "week": week,
                "names": len(tranche),
                "signal_ret_pct": round(sig_ret / n_sig, 2) if n_sig else None,
                "exec_ret_pct": round(exec_ret / n_exec, 2) if n_exec else None,
                "bench_ret_pct": round(bench_ret, 2) if bench_ret is not None else None,
                "stopped": int((tranche["status"] == "stopped").sum()) if "status" in tranche.columns else 0,
                "sessions_held": sessions_held,
                "as_of": latest_date,
            }
        )
    return pd.DataFrame(rows)


def _fmt(x) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{x:+.2f}%"


def render(df: pd.DataFrame, month: str) -> str:
    any_accruing = bool((df["sessions_held"] == 0).any()) if not df.empty else False
    lines = [
        f"# Paper Scorecard {month}",
        "",
        f"Generated {date.today().isoformat()}.",
        "",
        "## What this is",
        "",
        "This is the **validation scoreboard** for the paper ledger. Every week the system records the top-20 momentum names it *would have bought* (`data/ledger/paper_ledger.csv`). This report grades those hypothetical tranches against an equal-weight benchmark of the whole universe over the same window.",
        "",
        "It answers one question: *does the signal actually beat just holding the market?* A consistent positive edge across months is what earns this system real capital; without it, weights get tuned or factors rethought (Phase 5).",
        "",
        "## How to read each column",
        "",
        "- **Signal Ret** - mean return per name from the decision-day close to now. Measures raw signal quality.",
        "- **Exec Ret** - mean return per name from the NEXT session's open to now. What a human acting on the Sunday report could realistically achieve. The gap vs Signal is execution cost.",
        "- **Benchmark** - equal-weight return of all universe names over the identical window. Edge = Exec Ret minus Benchmark.",
        "- **Stopped** - names already exited by their stop-loss.",
        "",
        "## Tranches",
        "",
        "| Week | Names | Sessions Held | Signal Ret | Exec Ret | Benchmark | Edge (Exec-Bench) | Stopped | As of |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    edges: list[float] = []
    for _, r in df.iterrows():
        edge = (
            round(r["exec_ret_pct"] - r["bench_ret_pct"], 2)
            if r["exec_ret_pct"] is not None and r["bench_ret_pct"] is not None
            else None
        )
        if edge is not None:
            edges.append(edge)
        lines.append(
            f"| {r['week']} | {r['names']} | {r['sessions_held']} | {_fmt(r['signal_ret_pct'])} | {_fmt(r['exec_ret_pct'])} "
            f"| {_fmt(r['bench_ret_pct'])} | {_fmt(edge)} | {r['stopped']} | {r['as_of']} |"
        )
    lines += ["", "### Reading today's numbers", ""]
    if any_accruing:
        lines.append(
            "- Tranches showing `-` were logged at the most recent close, so no trading session has elapsed since entry; returns start accruing from the next session."
        )
    if not edges:
        lines.append("- No measurable edge yet. Meaningful readout begins after ~4+ weeks of accrued sessions; the real verdict needs the full 3-6 month shadow window.")
    elif len(edges) < 4:
        mean_edge = round(sum(edges) / len(edges), 2)
        lines.append(
            f"- Early mean edge: {mean_edge:+.2f}% per tranche - far too few tranches to trust; treat as a curiosity until ~4+ accrue."
        )
    else:
        mean_edge = round(sum(edges) / len(edges), 2)
        verdict = "beating" if mean_edge > 0 else "trailing"
        lines.append(f"- Mean edge across {len(edges)} tranches: {mean_edge:+.2f}% - currently {verdict} the benchmark.")
    lines += [
        "",
        "---",
        "_Not investment advice. Paper returns exclude brokerage/slippage/taxes; small samples are noise._",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scorecard")
    parser.add_argument("--month", default=date.today().strftime("%Y-%m"))
    args = parser.parse_args(argv)
    df = build_scorecard(args.month)
    if df.empty:
        print(f"no ledger entries for {args.month}")
        return 0
    path = write_report(REPORTS_DIR / "scorecard" / f"{args.month}.md", render(df, args.month))
    print(f"scorecard written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
