import io
from datetime import date

import pandas as pd
import requests

from nla.config import PRICE_DIR, REFERENCE_DIR, UNIVERSE_CSV, UNIVERSE_MODE, UNIVERSE_SIZE

UNIVERSE_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"

LIQUID_UNIVERSE_CSV = REFERENCE_DIR / "universe_liquid.csv"
UNIVERSE_HISTORY_DIR = REFERENCE_DIR / "universe_history"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


def refresh_universe() -> bool:
    try:
        resp = requests.get(UNIVERSE_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        if df.empty or "Symbol" not in df.columns:
            return False
        UNIVERSE_CSV.parent.mkdir(parents=True, exist_ok=True)
        UNIVERSE_CSV.write_text(resp.text, encoding="utf-8")
        return True
    except Exception:
        return False


def load_universe() -> list[str]:
    if not UNIVERSE_CSV.exists():
        raise FileNotFoundError(f"universe file missing: {UNIVERSE_CSV}")
    df = pd.read_csv(UNIVERSE_CSV)
    symbols = df["Symbol"].dropna().astype(str).str.strip().tolist()
    symbols = [s for s in symbols if s]
    if not symbols:
        raise ValueError("universe file has no symbols")
    return symbols


def build_liquid_universe(
    top_n: int = UNIVERSE_SIZE,
    min_price: float = 20.0,
    min_turnover_cr: float = 0.5,
    window_files: int = 20,
) -> pd.DataFrame:
    paths = sorted(PRICE_DIR.glob("*.parquet"))[-window_files:]
    if not paths:
        raise FileNotFoundError("no daily price parquets found to rank liquidity")
    frames: list[pd.DataFrame] = []
    for path in paths:
        try:
            df = pd.read_parquet(path, columns=["symbol", "close", "volume"])
        except Exception:
            continue
        df = df[(df["close"] > 0) & (df["volume"] > 0)].copy()
        if df.empty:
            continue
        df["turnover_cr"] = df["close"] * df["volume"] / 1e7
        frames.append(df[["symbol", "close", "turnover_cr"]])
    if not frames:
        raise ValueError("no usable rows in recent price parquets")
    long = pd.concat(frames, ignore_index=True)
    latest_close = long.groupby("symbol")["close"].last()
    grouped = long.groupby("symbol")["turnover_cr"].agg(median="median", days="count")
    stats = grouped.join(latest_close.rename("close"))
    stats = stats[
        (stats["days"] >= max(3, len(paths) // 2))
        & (stats["close"] >= min_price)
        & (stats["median"] >= min_turnover_cr)
    ]
    ranked = (
        stats.sort_values("median", ascending=False)
        .head(top_n)
        .reset_index()[["symbol", "close", "median", "days"]]
        .rename(columns={"median": "med_turnover_cr"})
    )
    content = ranked.to_csv(index=False)
    changed = not LIQUID_UNIVERSE_CSV.exists() or LIQUID_UNIVERSE_CSV.read_text(encoding="utf-8") != content
    LIQUID_UNIVERSE_CSV.parent.mkdir(parents=True, exist_ok=True)
    LIQUID_UNIVERSE_CSV.write_text(content, encoding="utf-8")
    if changed:
        UNIVERSE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        (UNIVERSE_HISTORY_DIR / f"{date.today().isoformat()}.csv").write_text(content, encoding="utf-8")
    return ranked


def load_liquid_universe() -> pd.DataFrame:
    if not LIQUID_UNIVERSE_CSV.exists():
        raise FileNotFoundError(f"liquid universe missing: {LIQUID_UNIVERSE_CSV} - run build_liquid_universe first")
    return pd.read_csv(LIQUID_UNIVERSE_CSV)


def load_active_symbols() -> list[str]:
    if UNIVERSE_MODE == "liquid":
        symbols = load_liquid_universe()["symbol"].dropna().astype(str).str.strip().tolist()
        symbols = [s for s in symbols if s]
        if not symbols:
            raise ValueError("liquid universe has no symbols")
        return symbols
    return load_universe()
