import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd

from nla.config import REFERENCE_DIR

SECTOR_MAP_CSV = REFERENCE_DIR / "sector_map.csv"
SECTOR_PENDING_CSV = REFERENCE_DIR / "sector_pending.csv"

MAP_COLUMNS = ["symbol", "sector", "industry", "source"]
POLITE_DELAY_SEC = 0.5

SECTOR_ALIASES = {
    "Health Care": "Healthcare",
    "Information Technology": "Technology",
    "Materials": "Basic Materials",
    "Consumer Discretionary": "Consumer Cyclical",
    "Consumer Staples": "Consumer Defensive",
    "Financials": "Financial Services",
}


def _canonical(sector: str) -> str:
    return SECTOR_ALIASES.get(str(sector).strip(), str(sector).strip())

FUND_NAME_RE = re.compile(r"\b(HDFCAMC|ICICIPRAMC|UTIAMC|KOTAKMAMC|DSPAMC|MIRAEAMC|ZERODHAAMC|AXISAMC|ADITYABIRLAMC|NIPPONINDIAETF|SAMCOMF)\b|\b[A-Z]+AMC\b\s*-")


def _resolve_yahoo(symbol: str) -> tuple[str, str] | None:
    import yfinance as yf

    ticker = yf.Ticker(f"{symbol}.NS")
    try:
        info = ticker.get_info()
    except Exception:
        return None
    sector = str(info.get("sector") or "").strip()
    industry = str(info.get("industry") or "").strip()
    if sector:
        return sector, industry
    qtype = str(info.get("quoteType") or "").strip()
    name = str(info.get("shortName") or info.get("longName") or "")
    if qtype in {"ETF", "MUTUALFUND", "INDEX"} or "etf" in name.lower() or FUND_NAME_RE.search(name.upper()):
        return "Etf", qtype or name
    return None


def _resolve_tickertape_sector(symbol: str) -> str | None:
    try:
        resp = requests_get_search(symbol)
        if resp is None:
            return None
        stocks = [s for s in resp.get("stocks", []) if s.get("type") == "stock"]
        exact = [s for s in stocks if str(s.get("ticker", "")).upper() == symbol.upper()]
        hit = (exact or [None])[-1] if exact else (stocks[0] if stocks else None)
        if hit and hit.get("match") == "EXACT":
            return str(hit.get("sector") or "") or None
        if hit:
            return f"{hit.get('sector')}?".replace("??", "?")
    except Exception:
        return None
    return None


def requests_get_search(symbol: str):
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Referer": "https://www.tickertape.in/",
    }
    try:
        resp = requests.get(f"https://api.tickertape.in/search?text={symbol}", headers=headers, timeout=20)
        if resp.status_code != 200:
            return None
        payload = resp.json()
        if not payload.get("success"):
            return None
        return payload.get("data")
    except Exception:
        return None


def _resolve(symbol: str) -> tuple[str, str, str] | None:
    resolved = _resolve_yahoo(symbol)
    if resolved and resolved[0] != "Unclassified":
        return resolved[0], resolved[1], "yahoo"
    tt = _resolve_tickertape_sector(symbol)
    if tt:
        return _canonical(tt), "", "tickertape"
    if resolved:
        return resolved[0], resolved[1], "yahoo"
    return None


def _load_rows() -> pd.DataFrame:
    if SECTOR_MAP_CSV.exists():
        try:
            return pd.read_csv(SECTOR_MAP_CSV)[MAP_COLUMNS]
        except Exception:
            pass
    return pd.DataFrame(columns=MAP_COLUMNS)


def _save_rows(rows: pd.DataFrame) -> None:
    SECTOR_MAP_CSV.parent.mkdir(parents=True, exist_ok=True)
    rows.drop_duplicates(subset=["symbol"], keep="last").sort_values("symbol").to_csv(SECTOR_MAP_CSV, index=False)


def build_static_map(symbols: list[str], delay: float = POLITE_DELAY_SEC) -> tuple[int, int]:
    rows = _load_rows()
    known = set(rows["symbol"].astype(str))
    added = skipped = 0
    for i, raw in enumerate(symbols):
        symbol = str(raw).strip().upper()
        if not symbol or symbol in known:
            skipped += 1
            continue
        resolved = _resolve(symbol)
        if resolved is None:
            pending = pd.DataFrame([{"symbol": symbol, "reason": "unresolved"}])
            SECTOR_PENDING_CSV.parent.mkdir(parents=True, exist_ok=True)
            if SECTOR_PENDING_CSV.exists():
                existing = pd.read_csv(SECTOR_PENDING_CSV)
                if symbol in set(existing["symbol"].astype(str)):
                    continue
                pending = pd.concat([existing, pending], ignore_index=True)
            pending.to_csv(SECTOR_PENDING_CSV, index=False)
            known.add(symbol)
            skipped += 1
            time.sleep(delay)
            continue
        sector, industry, source = resolved
        rows = pd.concat(
            [rows, pd.DataFrame([{"symbol": symbol, "sector": sector, "industry": industry, "source": source}])],
            ignore_index=True,
        )
        known.add(symbol)
        added += 1
        if added % 25 == 0:
            _save_rows(rows)
            print(f"progress: {added} resolved (last={symbol})", flush=True)
        if i < len(symbols) - 1:
            time.sleep(delay)
    _save_rows(rows)
    return added, skipped


def cycle_stage(macro: pd.DataFrame, flows: pd.DataFrame) -> str:
    raise NotImplementedError("Phase 4")


def cycle_stage_label(ret_21d, ret_63d, ret_126d) -> str:
    vals = [ret_21d, ret_63d, ret_126d]
    if any(v is None or pd.isna(v) for v in vals):
        return "-"
    r21, r63, r126 = float(ret_21d), float(ret_63d), float(ret_126d)
    if r21 > 0 and r63 > 0 and r126 > 0:
        return "Leading"
    if r63 > 0 and r126 > 0:
        return "Pullback"
    if r63 < 0 and r126 > 0:
        return "Weakening"
    if r63 > 0 and r126 < 0:
        return "Improving"
    return "Lagging"


def load_sector_map() -> pd.DataFrame:
    if not SECTOR_MAP_CSV.exists():
        raise FileNotFoundError(f"static sector map missing: {SECTOR_MAP_CSV} - build it once via python -m nla.sector")
    return pd.read_csv(SECTOR_MAP_CSV)


def sector_for(symbol: str, smap: pd.DataFrame | None = None) -> str:
    smap = smap if smap is not None else load_sector_map()
    hit = smap[smap["symbol"].astype(str) == str(symbol)]
    return str(hit["sector"].iloc[0]) if not hit.empty else "Unclassified"


def register_pending_symbols(symbols: list[str]) -> int:
    smap = load_sector_map()
    known = set(smap["symbol"].astype(str))
    fresh = [str(s).upper() for s in symbols if str(s).upper() not in known]
    if not fresh:
        return 0
    pending = pd.DataFrame([{"symbol": s, "reason": "new-in-universe"} for s in fresh])
    SECTOR_PENDING_CSV.parent.mkdir(parents=True, exist_ok=True)
    if SECTOR_PENDING_CSV.exists():
        existing = pd.read_csv(SECTOR_PENDING_CSV)
        fresh = [s for s in fresh if s not in set(existing["symbol"].astype(str))]
        if not fresh:
            return 0
        pending = pd.concat([existing, pd.DataFrame([{"symbol": s, "reason": "new-in-universe"} for s in fresh])], ignore_index=True)
    pending.to_csv(SECTOR_PENDING_CSV, index=False)
    return len(fresh)


def relative_strength(prices: pd.DataFrame, sector_map: pd.DataFrame) -> pd.DataFrame:
    from nla.factors import MOMENTUM_LOOKBACKS

    mapped = sector_map[~sector_map["sector"].astype(str).isin({"Unclassified", "Etf"})]
    sym_sector = dict(zip(mapped["symbol"].astype(str), mapped["sector"].astype(str)))
    close = prices.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index().ffill(limit=10)
    rets = close.pct_change()
    cols = [c for c in close.columns if c in sym_sector]
    sector_daily = rets[cols].T.groupby(rets[cols].columns.map(sym_sector)).mean().T
    universe_daily = rets[cols].mean(axis=1)
    sector_cum = (1 + sector_daily.fillna(0)).cumprod()
    universe_cum = (1 + universe_daily.fillna(0)).cumprod()
    counts = pd.Series(cols).map(sym_sector).value_counts()
    eligible_lookbacks = [lb for lb in MOMENTUM_LOOKBACKS if len(sector_cum) > lb]
    rows = []
    for sector in sector_cum.columns:
        row: dict[str, object] = {"sector": sector, "n_names": int(counts.get(sector, 0))}
        for lb in eligible_lookbacks:
            sec_ret = float(sector_cum[sector].iloc[-1] / sector_cum[sector].iloc[-1 - lb] - 1)
            uni_ret = float(universe_cum.iloc[-1] / universe_cum.iloc[-1 - lb] - 1)
            row[f"ret_{lb}d"] = round(sec_ret, 4)
            row[f"excess_{lb}d"] = round(sec_ret - uni_ret, 4)
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    pct_scores = []
    for lb in eligible_lookbacks:
        col = f"excess_{lb}d"
        pct_scores.append(out[col].rank(pct=True))
    out["rs_score"] = (pd.concat(pct_scores, axis=1).mean(axis=1) * 100).round(1)
    out["rs_rank"] = out["rs_score"].rank(ascending=False, method="min").astype(int)
    ordered = ["sector", "n_names"] + sum([[f"ret_{lb}d", f"excess_{lb}d"] for lb in eligible_lookbacks], []) + ["rs_score", "rs_rank"]
    return out.sort_values("rs_rank").reset_index(drop=True)[ordered]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sector")
    parser.add_argument("--build", action="store_true", help="resolve given/all active symbols into the static map")
    parser.add_argument("--symbols", help="comma-separated symbols; defaults to full active universe")
    args = parser.parse_args(argv)
    if not args.build:
        smap = load_sector_map()
        print(f"static sector map: {len(smap)} symbols, {smap['sector'].nunique()} sectors")
        print(smap.groupby('sector').size().sort_values(ascending=False).to_string())
        return 0
    from nla.universe import load_active_symbols

    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else load_active_symbols()
    added, skipped = build_static_map(symbols)
    smap = load_sector_map()
    print(f"built: +{added} resolved, {skipped} already-known-or-pending; total={len(smap)} symbols, {smap['sector'].nunique()} sectors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
