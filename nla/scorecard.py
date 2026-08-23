import argparse
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
    bench_rets = hist[hist["date"].astype(str) == latest_date].set_index("symbol")["close"]
    bench_first = hist[hist["date"].astype(str) <= str(ledger["entry_date"].min())]
    rows = []
    for week, tranche in ledger.groupby("week"):
        entry_dates = tranche["exec_date"].fillna(tranche["entry_date"]).astype(str)
        start_day = entry_dates.min()
        base = hist[hist["date"].astype(str) <= start_day].sort_values("date")
        base_day = str(base["date"].max()) if not base.empty else start_day
        prices_then = hist[hist["date"].astype(str) == base_day].set_index("symbol")["close"]
        prices_now = hist[hist["date"].astype(str) == latest_date].set_index("symbol")["close"]
        common = prices_then.index.intersection(prices_now.index)
        bench_ret = float((prices_now[common] / prices_then[common] - 1).mean()) * 100 if len(common) else float("nan")
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
                "signal_ret_pct": round(sig_ret / n_sig, 2) if n_sig else float("nan"),
                "exec_ret_pct": round(exec_ret / n_exec, 2) if n_exec else float("nan"),
                "bench_ret_pct": round(bench_ret, 2),
                "stopped": int((tranche["status"] == "stopped").sum()) if "status" in tranche.columns else 0,
                "as_of": latest_date,
                "base_day": base_day,
            }
        )
    return pd.DataFrame(rows)


def render(df: pd.DataFrame, month: str) -> str:
    lines = [
        f"# Paper Scorecard {month}",
        "",
        f"Generated {date.today().isoformat()} - per-tranche mean returns vs equal-weight universe benchmark.",
        f"Signal basis = decision-day close to latest; Exec basis = next-session open (realistic fill) to latest.",
        "",
        "| Week | Names | Signal Ret | Exec Ret | Benchmark | Stopped | As of |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for _, r in df.iterrows():
        lines.append(
            f"| {r['week']} | {r['names']} | {r['signal_ret_pct']}% | {r['exec_ret_pct']}% "
            f"| {r['bench_ret_pct']}% | {r['stopped']} | {r['as_of']} |"
        )
    if not df.empty and df["exec_ret_pct"].notna().any():
        edge = round(float((df["exec_ret_pct"] - df["bench_ret_pct"]).mean()), 2)
        lines += ["", f"Mean exec-vs-benchmark edge so far: **{edge}%** per tranche."]
    lines += ["", "_Not investment advice. Small samples are noise until several months accrue._"]
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
