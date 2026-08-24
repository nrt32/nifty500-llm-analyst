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

SEARCH_URL = "https://api.tickertape.in/search?text={symbol}"
PAGE_URL = "https://www.tickertape.in{slug}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Referer": "https://www.tickertape.in/"}
TTL_DAYS = 30
POLITE_DELAY_SEC = 2.0

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)

SCALAR_KEYS = ("marketCap",)


def _search(symbol: str) -> dict | None:
    resp = requests.get(SEARCH_URL.format(symbol=symbol), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        return None
    stocks = [s for s in payload.get("data", {}).get("stocks", []) if s.get("type") == "stock"]
    if not stocks:
        return None
    exact = [s for s in stocks if s.get("match") == "EXACT" or str(s.get("ticker", "")).upper() == symbol.upper()]
    return (exact or stocks)[0]


def _page_props(slug: str) -> dict:
    resp = requests.get(PAGE_URL.format(slug=slug), headers={"User-Agent": USER_AGENT}, timeout=45)
    resp.raise_for_status()
    match = NEXT_DATA_RE.search(resp.text)
    if not match:
        raise ValueError(f"no __NEXT_DATA__ for {slug}")
    data = json.loads(match.group(1))
    return data.get("props", {}).get("pageProps", {})


def _clean_scorecard(entries) -> dict[str, float | str]:
    out: dict[str, float | str] = {}
    for e in entries or []:
        name = str(e.get("name", "")).lower().replace(" ", "_")
        score = (e.get("score") or {})
        value = score.get("value")
        if isinstance(value, (int, float)):
            out[f"tt_{name}_score"] = round(value / 2.0, 2)
        out[f"tt_{name}_tag"] = str(e.get("tag", ""))
    return out


def _extract_balance(bs_rows) -> dict[str, float | None]:
    rows = [r for r in bs_rows or [] if r.get("endDate")]
    rows.sort(key=lambda r: r["endDate"], reverse=True)
    if not rows:
        return {}
    latest = rows[0]
    debt, equity = latest.get("balTdeb"), latest.get("balTeq")
    de = round(debt / equity, 3) if isinstance(debt, (int, float)) and isinstance(equity, (int, float)) and equity else None
    return {
        "tt_balance_period": latest.get("displayPeriod"),
        "tt_debt_cr": debt,
        "tt_equity_cr": equity,
        "tt_debt_to_equity": de,
    }


def _scalars(obj: dict, keys: tuple) -> dict:
    return {k: obj[k] for k in keys if isinstance(obj.get(k), (int, float))}


def extract_profile(pp: dict) -> dict:
    out: dict = {}
    quote = pp.get("securityQuote") or {}
    out.update(_scalars(quote, ("price", "o", "h", "l", "c", "vol")))
    info = pp.get("securityInfo") or {}
    gic = info.get("gic") or {}
    if isinstance(gic, dict):
        out["tt_gics_sector"] = gic.get("sector") or gic.get("gicsSector")
        out["tt_gics_industry"] = gic.get("industry") or gic.get("gicsIndustry")
    ratios = info.get("ratios")
    if isinstance(ratios, dict):
        out.update({f"tt_{k}": v for k, v in ratios.items() if isinstance(v, (int, float))})
    out.update(_clean_scorecard(pp.get("scorecard")))
    out.update(_extract_balance(pp.get("balancesheet-normal-annual")))
    income = pp.get("income-normal-annual") or []
    if income:
        rows = sorted([r for r in income if r.get("endDate")], key=lambda r: r["endDate"], reverse=True)
        latest = {k: v for k, v in rows[0].items() if k.startswith("inc") and isinstance(v, (int, float))}
        out["tt_income_period"] = rows[0].get("displayPeriod")
        out.update(latest)
    return {k: v for k, v in out.items() if v is not None}


def _cache_path(symbol: str) -> Path:
    return FUNDAMENTALS_DIR / f"{symbol.upper()}.json"


def _cache_fresh(path: Path, ttl_days: int) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched = payload.get("tickertape", {}).get("fetched_at")
        if not fetched:
            return False
        return datetime.now(timezone.utc) - datetime.fromisoformat(fetched) < timedelta(days=ttl_days)
    except Exception:
        return False


def enrich_symbol(symbol: str, refresh: bool = False, ttl_days: int = TTL_DAYS) -> dict:
    path = _cache_path(symbol)
    base = {}
    if path.exists():
        try:
            base = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            base = {}
    if not refresh and _cache_fresh(path, ttl_days):
        return base
    found = _search(symbol)
    if not found:
        raise ValueError(f"tickertape search returned nothing for {symbol}")
    time.sleep(POLITE_DELAY_SEC)
    pp = _page_props(found["slug"])
    profile = extract_profile(pp)
    profile["slug"] = found["slug"]
    profile["sid"] = found.get("sid")
    profile["sector_search"] = found.get("sector")
    profile["market_cap_cr"] = found.get("marketCap")
    profile["fetched_at"] = datetime.now(timezone.utc).isoformat()
    base["symbol"] = symbol.upper()
    base.setdefault("metrics", {})
    base["tickertape"] = profile
    FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base, indent=2), encoding="utf-8")
    return base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tickertape")
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
            payload = enrich_symbol(symbol, refresh=args.refresh)
            tt = payload.get("tickertape", {})
            print(json.dumps({"symbol": symbol.upper(), "de": tt.get("tt_debt_to_equity"), "keys": len(tt)}))
            ok += 1
        except Exception as exc:
            print(json.dumps({"symbol": symbol.upper(), "error": str(exc)[:200]}))
            fail += 1
    print(f"ok={ok} failed={fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
