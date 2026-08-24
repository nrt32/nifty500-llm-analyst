from pathlib import Path

import pandas as pd

from nla.config import DATA_DIR

LEDGER_DIR = DATA_DIR / "ledger"
LEDGER_CSV = LEDGER_DIR / "paper_ledger.csv"

LEDGER_COLUMNS = [
    "week",
    "entry_date",
    "symbol",
    "sector",
    "style",
    "signal_price",
    "momentum_rank",
    "momentum_score",
    "status",
    "exec_date",
    "exec_price",
    "exec_basis",
    "stop_pct",
    "exit_date",
    "exit_price",
    "exit_reason",
]

DEFAULT_TOP_N = 20


def load_ledger() -> pd.DataFrame:
    if not Path(LEDGER_CSV).exists():
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    try:
        df = pd.read_csv(LEDGER_CSV)
    except Exception:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    if "signal_price" not in df.columns and "entry_price" in df.columns:
        df = df.rename(columns={"entry_price": "signal_price"})
    return df.reindex(columns=LEDGER_COLUMNS)


def log_tranche(
    week: str,
    entry_date: str,
    entries: list[dict],
) -> tuple[int, bool]:
    ledger = load_ledger()
    if not ledger.empty and week in set(ledger["week"].astype(str)):
        return 0, False
    if not entries:
        return 0, False
    rows = pd.DataFrame(
        [
            {
                "week": week,
                "entry_date": entry_date,
                "symbol": e["symbol"],
                "sector": e.get("sector", "-"),
                "style": e.get("style", "-"),
                "signal_price": round(float(e["signal_price"]), 2),
                "momentum_rank": int(e.get("momentum_rank", 0)),
                "momentum_score": float(e.get("momentum_score", 0.0)),
                "status": "open",
                "exec_date": "",
                "exec_price": float("nan"),
                "exec_basis": "",
                "stop_pct": float("nan") if e.get("stop_pct") is None else float(e["stop_pct"]),
                "exit_date": "",
                "exit_price": float("nan"),
                "exit_reason": "",
            }
            for e in entries
        ]
    )
    combined = pd.concat([ledger, rows], ignore_index=True)[LEDGER_COLUMNS]
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_csv(LEDGER_CSV, index=False)
    return len(rows), True


def ledger_stats() -> dict[str, int]:
    ledger = load_ledger()
    if ledger.empty:
        return {"total": 0, "open": 0, "weeks": 0, "pending_exec": 0, "settled": 0}
    settled = ledger["exec_price"].notna().sum() if "exec_price" in ledger.columns else 0
    return {
        "total": len(ledger),
        "open": int((ledger["status"] == "open").sum()),
        "weeks": int(ledger["week"].nunique()),
        "pending_exec": int(len(ledger) - settled),
        "settled": int(settled),
    }


def settle_pending_entries(target_date: str, price_dir=None) -> int:
    import pandas as pd

    from nla.config import PRICE_DIR as DEFAULT_PRICE_DIR

    price_dir = Path(price_dir) if price_dir else Path(DEFAULT_PRICE_DIR)
    ledger = load_ledger()
    if ledger.empty or "exec_price" not in ledger.columns:
        return 0
    mask = (
        (ledger["status"] == "open")
        & (ledger["exec_price"].isna())
        & (ledger["entry_date"].astype(str) < target_date)
    )
    if not mask.any():
        return 0
    sessions = sorted(
        p.stem for p in price_dir.glob("*.parquet") if len(p.stem) == 10 and p.stem[4] == "-" and p.stem[7] == "-"
    )
    settled_count = 0
    ledger["exec_date"] = ledger["exec_date"].astype("object")
    ledger["exec_basis"] = ledger["exec_basis"].astype("object")
    for idx, row in ledger[mask].iterrows():
        entry = str(row["entry_date"])
        future = [d for d in sessions if d > entry]
        if not future:
            continue
        exec_day = future[0]
        try:
            day_df = pd.read_parquet(price_dir / f"{exec_day}.parquet")
        except Exception:
            continue
        match = day_df[day_df["symbol"] == row["symbol"]]
        if match.empty:
            continue
        col = "open" if "open" in match.columns and match["open"].notna().any() else "close"
        price = float(match.iloc[0][col])
        if not price > 0:
            continue
        ledger.loc[idx, "exec_date"] = exec_day
        ledger.loc[idx, "exec_price"] = round(price, 2)
        ledger.loc[idx, "exec_basis"] = f"next_{col}"
        try:
            from nla.engine import stop_pct_from_vol, volatility_proxy
            from nla.history import load_close_history

            hist = load_close_history()
            vol = volatility_proxy(hist, str(row["symbol"]), window=14, as_of=exec_day)
            ledger.loc[idx, "stop_pct"] = stop_pct_from_vol(vol)
        except Exception:
            pass
        settled_count += 1
    if settled_count:
        ledger.to_csv(LEDGER_CSV, index=False)
    return settled_count
