import json
import sys
from datetime import date, timedelta

from nla.config import DATA_DIR, UNIVERSE_CSV
from nla.ingest import update_day
from nla.universe import refresh_universe


def last_trading_day(ref: date | None = None) -> date:
    d = ref or date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def main() -> int:
    target = last_trading_day()
    refreshed = refresh_universe()
    if not UNIVERSE_CSV.exists():
        print("universe file unavailable", file=sys.stderr)
        return 2
    price_source = update_day(target)
    payload = {
        "date": target.isoformat(),
        "universe_refreshed": refreshed,
        "price_source": price_source,
    }
    (DATA_DIR / "status.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload))
    if price_source == "missing-universe":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
