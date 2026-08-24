import json
import sys
import time
from datetime import date
from pathlib import Path

import feedparser

from nla import llm_client
from nla.config import DATA_DIR

MEMOS_DIR = DATA_DIR / "memos"

NEWS_URL = "https://news.google.com/rss/search?q={query}+stock+India&hl=en-IN&gl=IN&ceid=IN:en"
POLITE_DELAY_SEC = 12.0
RATE_LIMIT_WAIT_SEC = 50.0


def _recent_news(symbol: str, limit: int = 5) -> list[str]:
    try:
        feed = feedparser.parse(NEWS_URL.format(query=f"{symbol}+shares"))
        return [e.title for e in feed.entries[:limit]]
    except Exception:
        return []


def _packet(symbol: str, mom_row, sector: str, rs_rank: int | None, fundamentals: dict | None) -> dict:
    metrics = {}
    if fundamentals:
        m = fundamentals.get("metrics", {})
        for k in ("roce", "roe", "stock p/e", "book value", "dividend yield"):
            if k in m:
                metrics[k] = m[k]
    return {
        "symbol": symbol,
        "sector": sector or "unmapped",
        "sector_rs_rank": rs_rank,
        "price": round(float(mom_row["price"]), 2),
        "ret_21d_pct": round(float(mom_row["ret_21d"]) * 100, 1),
        "ret_63d_pct": round(float(mom_row["ret_63d"]) * 100, 1),
        "ret_126d_pct": round(float(mom_row["ret_126d"]) * 100, 1) if mom_row.get("ret_126d") is not None else None,
        "momentum_score": float(mom_row["momentum_score"]),
        "history_sessions": int(mom_row["obs"]),
        "fundamentals": metrics,
        "recent_news_headlines": _recent_news(symbol),
    }


PROMPT_TEMPLATE = """You are an equity research analyst reviewing a positional (months-to-quarters) long-only candidate in Indian markets.

STOCK PACKET:
{packet}

Write a terse investment memo. Respond with ONLY a JSON object, no other text, with keys:
- "stance": one of "buy", "hold", "avoid"
- "conviction": integer 0-100
- "thesis": one paragraph max 80 words
- "risks": array of up to 3 short strings
- "news_sentiment": one of "positive", "neutral", "negative", "unknown"

Rules: momentum alone is not a reason to buy - flag overheated moves. If news is absent say sentiment unknown. Never invent numbers."""


def _cache_path(week: str, symbol: str) -> Path:
    return MEMOS_DIR / week / f"{symbol}.json"


def get_memo(
    symbol: str,
    mom_row,
    sector: str,
    rs_rank: int | None,
    week: str,
    fundamentals: dict | None = None,
    refresh: bool = False,
) -> dict | None:
    path = _cache_path(week, symbol)
    if path.exists() and not refresh:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if "error" not in cached:
                return cached
        except Exception:
            pass
    if not llm_client.available():
        return None
    packet = _packet(symbol, mom_row, sector, rs_rank, fundamentals)
    prompt = PROMPT_TEMPLATE.format(packet=json.dumps(packet, indent=1))
    try:
        raw = llm_client.complete(prompt)
    except Exception as exc:
        failure = {
            "symbol": symbol,
            "week": week,
            "generated_at": str(date.today()),
            "error": str(exc),
            "packet": {"symbol": symbol, "sector": sector or "unmapped"},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(failure, indent=2), encoding="utf-8")
        return {"error": str(exc), "stance": "hold", "conviction": None}
    parsed = llm_client.extract_json(raw)
    if not parsed:
        return {"error": "unparseable llm response", "raw_excerpt": raw[:400]}
    memo = {
        "symbol": symbol,
        "week": week,
        "generated_at": str(date.today()),
        "stance": str(parsed.get("stance", "hold")).lower(),
        "conviction": parsed.get("conviction"),
        "thesis": parsed.get("thesis", ""),
        "risks": parsed.get("risks", []),
        "news_sentiment": parsed.get("news_sentiment", "unknown"),
        "packet": packet,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(memo, indent=2), encoding="utf-8")
    return memo


def main(argv: list[str] | None = None) -> int:
    parser = argparse_stub()
    args = parser.parse_args(argv)
    from nla.factors import momentum_ranks
    from nla.history import load_close_history
    from nla.sector import load_sector_map

    hist = load_close_history()
    mom = momentum_ranks(hist).head(args.top)
    smap = load_sector_map()
    sym_sector = dict(zip(smap["symbol"], smap["sector"]))
    ok = skip = fail = 0
    for _, row in mom.iterrows():
        memo = get_memo(row["symbol"], row, sym_sector.get(row["symbol"]), None, args.week)
        if memo is None:
            skip += 1
        elif "error" in memo:
            fail += 1
        else:
            print(json.dumps({k: memo[k] for k in ("symbol", "stance", "conviction")}))
            ok += 1
    print(f"memos ok={ok} skipped={skip} failed={fail}")
    return 0


def argparse_stub():
    import argparse

    parser = argparse.ArgumentParser(prog="memos")
    parser.add_argument("--week", required=True)
    parser.add_argument("--top", type=int, default=10)
    return parser


if __name__ == "__main__":
    sys.exit(main())
