import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from nla.config import DATA_DIR

FUNDAMENTALS_DIR = DATA_DIR / "fundamentals"

PAGE_URL = "https://www.screener.in/company/{symbol}/consolidated/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TTL_DAYS = 30
POLITE_DELAY_SEC = 2.0

PAIR_RE = re.compile(r'class="name"[^>]*>\s*([^<]+?)\s*</span>.*?class="number"[^>]*>(.*?)</span>', re.S)


def _clean_value(raw: str) -> float | str:
    text = re.sub(r"<[^>]+>", "", raw)
    text = text.replace(",", "").replace("%", "").strip()
    parts = [p for p in text.split("/") if p.strip()]
    if len(parts) == 2:
        try:
            low, high = float(parts[0]), float(parts[1])
            return round((low + high) / 2, 4)
        except ValueError:
            return text
    try:
        return float(text)
    except ValueError:
        return text


def _parse_ratios(html: str) -> dict[str, float | str]:
    out: dict[str, float | str] = {}
    for match in PAIR_RE.finditer(html):
        key = match.group(1).strip().lower()
        if key and key not in out:
            out[key] = _clean_value(match.group(2))
    return out


def _cache_path(symbol: str) -> Path:
    return FUNDAMENTALS_DIR / f"{symbol.upper()}.json"


def _cache_fresh(path: Path, ttl_days: int) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(payload["fetched_at"])
        return datetime.now(timezone.utc) - fetched < timedelta(days=ttl_days)
    except Exception:
        return False


def fetch_fundamentals(symbol: str, refresh: bool = False, ttl_days: int = TTL_DAYS) -> dict:
    path = _cache_path(symbol)
    if path.exists() and not refresh and _cache_fresh(path, ttl_days):
        return json.loads(path.read_text(encoding="utf-8"))
    url = PAGE_URL.format(symbol=symbol.upper())
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if resp.status_code == 404:
        url = url.removesuffix("consolidated/")
        time.sleep(POLITE_DELAY_SEC)
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    payload = {
        "symbol": symbol.upper(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_url": url,
        "metrics": _parse_ratios(resp.text),
    }
    FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fundamentals")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    ok = fail = 0
    for i, symbol in enumerate(s.strip() for s in args.symbols.split(",")):
        if not symbol:
            continue
        if i:
            time.sleep(POLITE_DELAY_SEC)
        try:
            payload = fetch_fundamentals(symbol, refresh=args.refresh)
            print(json.dumps({"symbol": payload["symbol"], "metrics": payload["metrics"]}))
            ok += 1
        except Exception as exc:
            print(json.dumps({"symbol": symbol.upper(), "error": str(exc)}))
            fail += 1
    print(f"ok={ok} failed={fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
