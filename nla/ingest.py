import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from nla.config import PRICE_DIR
from nla.universe import HEADERS, load_universe

BHAVCOPY_URL = "https://archives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"

PRICE_COLUMNS = [
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "prev_close",
    "volume",
    "delivery_pct",
]

RENAME_MAP = {
    "OPEN_PRICE": "open",
    "HIGH_PRICE": "high",
    "LOW_PRICE": "low",
    "CLOSE_PRICE": "close",
    "PREV_CLOSE": "prev_close",
    "TTL_TRD_QNTY": "volume",
    "DELIV_PER": "delivery_pct",
}

NUMERIC_COLUMNS = ["open", "high", "low", "close", "prev_close", "volume", "delivery_pct"]


def day_path(d: date) -> Path:
    return PRICE_DIR / f"{d.isoformat()}.parquet"


def fetch_bhavcopy(d: date) -> pd.DataFrame | None:
    url = BHAVCOPY_URL.format(ddmmyyyy=d.strftime("%d%m%Y"))
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        raw = pd.read_csv(io.StringIO(resp.text))
    except Exception:
        return None
    raw.columns = [str(c).strip() for c in raw.columns]
    needed = {"SYMBOL", "SERIES"} | set(RENAME_MAP)
    if not needed.issubset(raw.columns):
        return None
    raw = raw.rename(columns=RENAME_MAP)
    raw["SYMBOL"] = raw["SYMBOL"].astype(str).str.strip()
    raw["SERIES"] = raw["SERIES"].astype(str).str.strip()
    raw = raw[raw["SERIES"] == "EQ"]
    if raw.empty:
        return None
    out = raw[["SYMBOL"] + NUMERIC_COLUMNS].rename(columns={"SYMBOL": "symbol"}).copy()
    for col in NUMERIC_COLUMNS:
        out[col] = pd.to_numeric(out[col].astype(str).str.strip(), errors="coerce")
    out = out.dropna(subset=["close"])
    if out.empty:
        return None
    out.insert(1, "date", d)
    return out[PRICE_COLUMNS].reset_index(drop=True)


def fetch_yahoo_close(symbols: list[str], start: date, end: date) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["symbol", "date", "close"])
    tickers = [f"{s}.NS" for s in symbols]
    frames: list[pd.DataFrame] = []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i : i + 100]
        try:
            data = yf.download(
                tickers=chunk,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                interval="1d",
                progress=False,
                threads=False,
                auto_adjust=False,
            )
        except Exception:
            continue
        if data is None or data.empty or "Close" not in data.columns:
            continue
        close = data["Close"]
        if isinstance(close, pd.Series):
            close = close.to_frame(name=chunk[0])
        idx_name = close.index.name or "date"
        long = close.reset_index().melt(id_vars=idx_name, var_name="ticker", value_name="close")
        long = long.dropna(subset=["close"]).rename(columns={idx_name: "date"})
        if long.empty:
            continue
        frames.append(long)
    if not frames:
        return empty
    combined = pd.concat(frames, ignore_index=True)
    combined["symbol"] = combined["ticker"].astype(str).str.removesuffix(".NS")
    combined["date"] = pd.to_datetime(combined["date"]).dt.date
    return combined[["symbol", "date", "close"]]


def update_day(d: date) -> str:
    path = day_path(d)
    if path.exists():
        return "exists"
    bhavcopy = fetch_bhavcopy(d)
    if bhavcopy is not None and not bhavcopy.empty:
        bhavcopy.to_parquet(path, index=False)
        return "bhavcopy"
    try:
        symbols = load_universe()
    except Exception:
        return "missing-universe"
    yahoo = fetch_yahoo_close(symbols, d, d)
    if yahoo.empty:
        return "missing"
    yahoo.to_parquet(path, index=False)
    return "yahoo"
