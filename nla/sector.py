import argparse
import io
import sys

import pandas as pd
import requests

from nla.config import REFERENCE_DIR
from nla.universe import HEADERS

SECTOR_MAP_CSV = REFERENCE_DIR / "sector_map.csv"

SECTOR_BASE_URL = "https://archives.nseindia.com/content/indices/"

SECTOR_SOURCES = {
    "financial_services": "ind_niftyfinancelist.csv",
    "energy": "ind_niftyenergylist.csv",
    "oil_gas": "ind_niftyoilgaslist.csv",
    "infra": "ind_niftyinfralist.csv",
    "healthcare": "ind_niftyhealthcarelist.csv",
    "fmcg": "ind_niftyfmcglist.csv",
    "it": "ind_niftyitlist.csv",
    "auto": "ind_niftyautolist.csv",
    "metal": "ind_niftymetallist.csv",
    "realty": "ind_niftyrealtylist.csv",
    "media": "ind_niftymedialist.csv",
    "bank": "ind_niftybanklist.csv",
    "psu_bank": "ind_niftypsubanklist.csv",
    "pharma": "ind_niftypharmalist.csv",
}


def refresh_sector_map() -> bool:
    frames: list[pd.DataFrame] = []
    ok = False
    for sector, filename in SECTOR_SOURCES.items():
        url = SECTOR_BASE_URL + filename
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            if df.empty or "Symbol" not in df.columns:
                continue
            part = pd.DataFrame(
                {
                    "symbol": df["Symbol"].astype(str).str.strip(),
                    "sector": sector,
                    "industry": df["Industry"].astype(str).str.strip() if "Industry" in df.columns else sector,
                }
            )
            part = part[part["symbol"] != ""]
            frames.append(part)
            ok = True
        except Exception:
            continue
    if not ok:
        return False
    combined = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["symbol"], keep="last")
        .sort_values(["sector", "symbol"])
        .reset_index(drop=True)
    )
    SECTOR_MAP_CSV.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(SECTOR_MAP_CSV, index=False)
    return True


def load_sector_map() -> pd.DataFrame:
    if not SECTOR_MAP_CSV.exists():
        raise FileNotFoundError(f"sector map missing: {SECTOR_MAP_CSV} - run python -m nla.sector first")
    return pd.read_csv(SECTOR_MAP_CSV)


def relative_strength(prices: pd.DataFrame, sector_map: pd.DataFrame) -> pd.DataFrame:
    from nla.factors import MOMENTUM_LOOKBACKS

    close = prices.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index().ffill(limit=10)
    rets = close.pct_change()
    sym_sector = dict(zip(sector_map["symbol"].astype(str), sector_map["sector"].astype(str)))
    cols = [c for c in close.columns if c in sym_sector]
    sector_daily = rets[cols].T.groupby(rets[cols].columns.map(sym_sector)).mean().T
    universe_daily = rets[cols].mean(axis=1)
    sector_cum = (1 + sector_daily.fillna(0)).cumprod()
    universe_cum = (1 + universe_daily.fillna(0)).cumprod()
    counts = pd.Series(cols).map(sym_sector).value_counts()
    rows = []
    eligible_lookbacks = [lb for lb in MOMENTUM_LOOKBACKS if len(sector_cum) > lb]
    for sector in sector_cum.columns:
        row: dict[str, object] = {"sector": sector, "n_names": int(counts.get(sector, 0))}
        pcts = []
        for lb in eligible_lookbacks:
            sec_ret = float(sector_cum[sector].iloc[-1] / sector_cum[sector].iloc[-1 - lb] - 1)
            uni_ret = float(universe_cum.iloc[-1] / universe_cum.iloc[-1 - lb] - 1)
            row[f"ret_{lb}d"] = round(sec_ret, 4)
            row[f"excess_{lb}d"] = round(sec_ret - uni_ret, 4)
            pcts.append((sec_ret, uni_ret))
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


def cycle_stage(macro: pd.DataFrame, flows: pd.DataFrame) -> str:
    raise NotImplementedError("Phase 4")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sector")
    parser.parse_args(argv)
    if not refresh_sector_map():
        print("sector map refresh failed", file=sys.stderr)
        return 2
    smap = load_sector_map()
    print(f"sector map symbols={len(smap)} sectors={smap['sector'].nunique()}")
    print(smap.groupby('sector').size().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
