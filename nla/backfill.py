import argparse
import sys
from datetime import date, timedelta

from nla.ingest import update_day


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backfill")
    parser.add_argument("--start", required=True, type=parse_date)
    parser.add_argument("--end", required=True, type=parse_date)
    args = parser.parse_args(argv)
    missing = 0
    for offset in range((args.end - args.start).days + 1):
        d = args.start + timedelta(days=offset)
        if d.weekday() >= 5:
            continue
        status = update_day(d)
        if status in {"missing", "missing-universe"}:
            missing += 1
        print(f"{d.isoformat()} {status}")
    print(f"missing days: {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
