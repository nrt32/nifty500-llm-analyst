import pandas as pd

from nla.config import PRICE_DIR


def detect_events(lookback_files: int = 22, high_tol: float = 0.02, vol_mult: float = 3.0) -> pd.DataFrame:
    paths = sorted(PRICE_DIR.glob("*.parquet"))[-lookback_files:]
    if len(paths) < 5:
        return pd.DataFrame(columns=["symbol", "event", "detail"])
    vol_frames: list[pd.DataFrame] = []
    for path in paths[:-1]:
        try:
            df = pd.read_parquet(path, columns=["symbol", "volume"])
        except Exception:
            continue
        frames_ok = df[df["volume"] > 0]
        if not frames_ok.empty:
            vol_frames.append(frames_ok)
    if not vol_frames:
        return pd.DataFrame(columns=["symbol", "event", "detail"])
    vol_long = pd.concat(vol_frames, ignore_index=True)
    med_vol = vol_long.groupby("symbol")["volume"].median()
    try:
        latest_df = pd.read_parquet(paths[-1])
    except Exception:
        return pd.DataFrame(columns=["symbol", "event", "detail"])
    rows: list[dict[str, str]] = []
    for _, r in latest_df.iterrows():
        symbol = str(r["symbol"])
        close = r.get("close")
        volume = r.get("volume")
        detail_52w = ""
        hit_high = False
        try:
            hist_sym = hist_close(symbol)
            if hist_sym is not None and not hist_sym.empty and close and close > 0:
                peak = float(hist_sym.max())
                if peak > 0 and close >= (1 - high_tol) * peak:
                    hit_high = True
                    detail_52w = f"close {close:.2f} vs 52w-high {peak:.2f}"
        except Exception:
            pass
        spike = False
        detail_vol = ""
        med = med_vol.get(symbol)
        if med is not None and med > 0 and volume and volume >= vol_mult * med:
            spike = True
            detail_vol = f"vol {int(volume)} vs median {int(med)}"
        if hit_high:
            rows.append({"symbol": symbol, "event": "52w-high", "detail": detail_52w})
        if spike:
            rows.append({"symbol": symbol, "event": "volume-spike", "detail": detail_vol})
    return pd.DataFrame(rows, columns=["symbol", "event", "detail"])


_HIST_CACHE: dict[str, pd.Series] | None = None
_HIST_LOADED = False


def hist_close(symbol: str) -> pd.Series | None:
    global _HIST_CACHE, _HIST_LOADED
    if not _HIST_LOADED:
        try:
            from nla.history import load_close_history

            hist = load_close_history()
            _HIST_CACHE = {s: g.sort_values("date").set_index("date")["close"] for s, g in hist.groupby("symbol")}
        except Exception:
            _HIST_CACHE = {}
        _HIST_LOADED = True
    return _HIST_CACHE.get(symbol) if _HIST_CACHE else None
