import json
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from nla.config import DATA_DIR, REPORTS_DIR, UNIVERSE_CSV
from nla.ingest import day_path, update_day
from nla.report import write_report
from nla.universe import refresh_universe

IST = timezone(timedelta(hours=5, minutes=30))
PUBLISH_CUTOFF_HOUR = 18
REPAIR_WINDOW_DAYS = 7

SOURCE_LABELS = {
    "exists": "already ingested (idempotent skip)",
    "bhavcopy": "NSE bhavcopy",
    "yahoo": "Yahoo Finance fallback (close only)",
    "missing": "unavailable - likely market holiday",
    "missing-universe": "unavailable - universe file missing",
}


def last_trading_day(now: datetime | None = None) -> date:
    moment = now or datetime.now(IST)
    d = moment.date()
    if d.weekday() < 5 and moment.hour < PUBLISH_CUTOFF_HOUR:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def repair_recent(window: int = REPAIR_WINDOW_DAYS) -> list[str]:
    repaired = []
    today = date.today()
    for offset in range(1, window + 1):
        d = today - timedelta(days=offset)
        if d.weekday() >= 5 or day_path(d).exists():
            continue
        status = update_day(d)
        if status not in {"missing", "missing-universe"}:
            repaired.append(f"{d.isoformat()}={status}")
    return repaired


def render_daily_report(target: date, refreshed: bool, price_source: str, repaired: list[str]) -> str:
    lines = [
        f"# Daily Scan {target.isoformat()}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Date | {target.isoformat()} |",
        f"| Universe list refreshed | {'yes' if refreshed else 'no - cached copy used'} |",
        f"| Price source | {SOURCE_LABELS.get(price_source, price_source)} |",
        f"| Backfilled | {', '.join(repaired) if repaired else 'nothing pending'} |",
    ]
    path = day_path(target)
    if path.exists():
        try:
            df = pd.read_parquet(path)
            lines.append(f"| Rows ingested | {len(df)} |")
            lines.append(f"| Symbols | {df['symbol'].nunique()} |")
        except Exception:
            pass
    lines += ["", "_Not investment advice. Personal research tool for the repository owner._"]
    return "\n".join(lines) + "\n"


def main() -> int:
    target = last_trading_day()
    refreshed = refresh_universe()
    if not UNIVERSE_CSV.exists():
        print("universe file unavailable", file=sys.stderr)
        return 2
    price_source = update_day(target)
    repaired = repair_recent()
    payload = {
        "date": target.isoformat(),
        "universe_refreshed": refreshed,
        "price_source": price_source,
        "repaired": repaired,
    }
    (DATA_DIR / "status.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_report(
        REPORTS_DIR / "daily" / f"{target.isoformat()}.md",
        render_daily_report(target, refreshed, price_source, repaired),
    )
    print(json.dumps(payload))
    if price_source == "missing-universe":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
