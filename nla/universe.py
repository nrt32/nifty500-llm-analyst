import io

import pandas as pd
import requests

from nla.config import UNIVERSE_CSV

UNIVERSE_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"

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
