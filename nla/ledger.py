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
    "entry_price",
    "momentum_rank",
    "momentum_score",
    "status",
]

DEFAULT_TOP_N = 20


def load_ledger() -> pd.DataFrame:
    if not Path(LEDGER_CSV).exists():
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    try:
        df = pd.read_csv(LEDGER_CSV)
        return df[LEDGER_COLUMNS]
    except Exception:
        return pd.DataFrame(columns=LEDGER_COLUMNS)


def log_weekly_entries(
    mom: pd.DataFrame,
    smap: pd.DataFrame,
    week: str,
    entry_date: str,
    top_n: int = DEFAULT_TOP_N,
) -> tuple[int, bool]:
    ledger = load_ledger()
    if not ledger.empty and week in set(ledger["week"].astype(str)):
        return 0, False
    sym_sector = dict(zip(smap["symbol"].astype(str), smap["sector"].astype(str)))
    top = mom.head(top_n)
    if top.empty:
        return 0, False
    rows = pd.DataFrame(
        {
            "week": week,
            "entry_date": entry_date,
            "symbol": top["symbol"].astype(str).values,
            "sector": [sym_sector.get(str(s), "-") for s in top["symbol"]],
            "entry_price": top["price"].round(2).values,
            "momentum_rank": top["momentum_rank"].astype(int).values,
            "momentum_score": top["momentum_score"].values,
            "status": "open",
        }
    )
    combined = pd.concat([ledger, rows], ignore_index=True)[LEDGER_COLUMNS]
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_csv(LEDGER_CSV, index=False)
    return len(rows), True


def ledger_stats() -> dict[str, int]:
    ledger = load_ledger()
    if ledger.empty:
        return {"total": 0, "open": 0, "weeks": 0}
    return {
        "total": len(ledger),
        "open": int((ledger["status"] == "open").sum()),
        "weeks": int(ledger["week"].nunique()),
    }
