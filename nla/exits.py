from pathlib import Path

import pandas as pd

from nla.config import PRICE_DIR
from nla.ledger import LEDGER_CSV, load_ledger

TRAIL_FLOOR_PCT = 0.10
TRAIL_CAP_PCT = 0.20
TREND_BREAK_DAYS = 2
TIME_STOP_SESSIONS = 40


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _high_water(hist: pd.DataFrame, symbol: str, since: str) -> float | None:
    series = hist[(hist["symbol"] == symbol) & (hist["date"].astype(str) >= since)]
    if series.empty:
        return None
    return float(series["close"].max())


def run_exit_checks(target_date: str, price_dir=None) -> dict[str, int]:
    price_dir = Path(price_dir) if price_dir else Path(PRICE_DIR)
    ledger = load_ledger()
    if ledger.empty:
        return {"exited": 0}
    path = price_dir / f"{target_date}.parquet"
    if not path.exists():
        return {"exited": 0}
    try:
        day_df = pd.read_parquet(path)
    except Exception:
        return {"exited": 0}
    from nla.history import load_close_history

    hist = load_close_history()
    open_mask = (ledger["status"] == "open") & (ledger["exec_price"].notna()) & (ledger["exec_date"].astype(str) <= target_date)
    counts = {"initial_stop": 0, "trailing_stop": 0, "trend_break": 0, "time_stop": 0}
    for idx, row in ledger[open_mask].iterrows():
        symbol = str(row["symbol"])
        match = day_df[day_df["symbol"] == symbol]
        if match.empty:
            continue
        mrow = match.iloc[0]
        exec_price = float(row["exec_price"])
        stop_pct = row.get("stop_pct")
        low = mrow.get("low")
        close = mrow.get("close")

        def exit_row(reason: str, price: float) -> None:
            ledger.loc[idx, "status"] = "stopped" if "stop" in reason else "exited"
            ledger.loc[idx, "exit_date"] = target_date
            ledger.loc[idx, "exit_price"] = round(price, 2)
            ledger.loc[idx, "exit_reason"] = reason

        if stop_pct is not None and not pd.isna(stop_pct) and low is not None and not pd.isna(low):
            level = round(exec_price * (1 - float(stop_pct)), 2)
            if float(low) <= level:
                exit_row(f"initial stop {float(stop_pct) * 100:.1f}%", level)
                counts["initial_stop"] += 1
                continue
        sym_hist = hist[(hist["symbol"] == symbol) & (hist["date"].astype(str) >= str(row["exec_date"]))].sort_values("date")
        hw = sym_hist["close"].max() if not sym_hist.empty else None
        vol = None
        if len(sym_hist) >= 15:
            rets = sym_hist["close"].pct_change().dropna().tail(14)
            vol = float(rets.abs().mean()) if len(rets) else None
        if hw is not None and vol is not None and close is not None and not pd.isna(close):
            trail_pct = min(max(2 * vol, TRAIL_FLOOR_PCT), TRAIL_CAP_PCT)
            trail_level = round(float(hw) * (1 - trail_pct), 2)
            if float(close) < trail_level and float(hw) > exec_price:
                exit_row(f"trailing {trail_pct * 100:.0f}% off high {hw:.2f}", float(close))
                counts["trailing_stop"] += 1
                continue
        sessions_held = len(sym_hist[sym_hist["date"].astype(str) > str(row["exec_date"])])
        if close is not None and not pd.isna(close) and len(sym_hist) >= 52:
            ema50 = float(_ema(sym_hist["close"], 50).iloc[-1])
            recent_closes = sym_hist["close"].tail(TREND_BREAK_DAYS)
            if sessions_held >= TREND_BREAK_DAYS and bool((recent_closes < ema50).all()):
                exit_row(f"trend break: {TREND_BREAK_DAYS} closes under EMA50", float(close))
                counts["trend_break"] += 1
                continue
        if sessions_held >= TIME_STOP_SESSIONS and close is not None and not pd.isna(close):
            if float(close) < exec_price:
                exit_row(f"time stop after {sessions_held} sessions", float(close))
                counts["time_stop"] += 1
                continue
    if any(counts.values()):
        ledger.to_csv(LEDGER_CSV, index=False)
    return {"exited": sum(counts.values()), **counts}


def check_stop_exits(target_date: str, price_dir=None) -> dict[str, int]:
    return run_exit_checks(target_date, price_dir)
