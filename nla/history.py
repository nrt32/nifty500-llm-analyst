import argparse
import contextlib
import io
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

from nla.config import DATA_DIR, PRICE_DIR
from nla.universe import load_universe

HISTORY_DIR = DATA_DIR / "history"
CLOSE_PARQUET = HISTORY_DIR / "close.parquet"

COLUMNS = ["symbol", "date", "close"]


def _fetch_yahoo_closes(symbols: list[str], period: str = "1y") -> pd.DataFrame:
    logging.getLogger("yfinance").disabled = True
    tickers = [f"{s}.NS" for s in symbols]
    frames: list[pd.DataFrame] = []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i : i + 100]
        noise = io.StringIO()
        try:
            with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
                data = yf.download(
                    tickers=chunk,
                    period=period,
                    interval="1d",
                    progress=False,
                    threads=False,
                    auto_adjust=False,
                )
        except Exception:
            continue
        if data is None or data.empty or "Close" not in data.columns:
            continue
        close = data["Close"].copy()
        if isinstance(close, pd.Series):
            close = close.to_frame(name=chunk[0])
        idx_name = close.index.name or "date"
        long = close.reset_index().melt(id_vars=idx_name, var_name="ticker", value_name="close")
        long = long.dropna(subset=["close"]).rename(columns={idx_name: "date"})
        if not long.empty:
            frames.append(long)
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    combined["symbol"] = combined["ticker"].astype(str).str.removesuffix(".NS")
    combined["date"] = pd.to_datetime(combined["date"]).dt.date
    return combined[COLUMNS]


def bootstrap(symbols: list[str] | None = None, period: str = "1y") -> pd.DataFrame:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    fresh = _fetch_yahoo_closes(symbols or load_universe(), period)
    history = fresh
    if CLOSE_PARQUET.exists() and not fresh.empty:
        existing = pd.read_parquet(CLOSE_PARQUET)
        history = (
            pd.concat([existing, fresh], ignore_index=True)
            .drop_duplicates(subset=["symbol", "date"], keep="last")
            .sort_values(["symbol", "date"])
            .reset_index(drop=True)
        )
    if not history.empty:
        history.to_parquet(CLOSE_PARQUET, index=False)
    return history


def refresh_from_daily(history: pd.DataFrame | None = None) -> pd.DataFrame:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    base = history if history is not None else (pd.read_parquet(CLOSE_PARQUET) if CLOSE_PARQUET.exists() else pd.DataFrame(columns=COLUMNS))
    max_date = base["date"].max() if not base.empty else None
    daily_frames: list[pd.DataFrame] = []
    for path in sorted(PRICE_DIR.glob("*.parquet")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if max_date is not None and day <= max_date:
            continue
        try:
            df = pd.read_parquet(path, columns=["symbol", "close"])
        except Exception:
            continue
        df = df.copy()
        df["date"] = day
        daily_frames.append(df[["symbol", "date", "close"]])
    if not daily_frames:
        return base
    combined = (
        pd.concat([base] + daily_frames, ignore_index=True)
        .drop_duplicates(subset=["symbol", "date"], keep="last")
        .sort_values(["symbol", "date"])
        .reset_index(drop=True)
    )
    combined.to_parquet(CLOSE_PARQUET, index=False)
    return combined


def load_close_history() -> pd.DataFrame:
    if not CLOSE_PARQUET.exists():
        raise FileNotFoundError(f"close history missing: {CLOSE_PARQUET} - run python -m nla.history first")
    return pd.read_parquet(CLOSE_PARQUET)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="history")
    parser.add_argument("--period", default="1y")
    args = parser.parse_args(argv)
    history = bootstrap(period=args.period)
    history = refresh_from_daily(history)
    span = (history["date"].min(), history["date"].max()) if not history.empty else ("-", "-")
    print(f"close history rows={len(history)} symbols={history['symbol'].nunique()} span={span[0]}..{span[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
