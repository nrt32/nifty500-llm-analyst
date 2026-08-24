import json

import pandas as pd

from nla import llm_client
from nla.config import PRICE_DIR
from nla.memos import _packet

EXTENSION_CAP = 0.15
RSI_MIN, RSI_MAX = 45, 78
BREAKOUT_VOL_MULT = 1.5
BREAKOUT_PROXIMITY = 0.98
PULLBACK_TOUCH = 1.03
MAX_DEBATES = 12


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi14(closes: pd.Series) -> float | None:
    if len(closes) < 15:
        return None
    delta = closes.diff().dropna()
    gains = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    losses = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    if float(losses.iloc[-1]) == 0:
        return 100.0
    rs = float(gains.iloc[-1]) / float(losses.iloc[-1])
    return round(100 - 100 / (1 + rs), 1)


def _volume_ratio(symbol: str, price_dir=PRICE_DIR) -> float | None:
    paths = sorted(price_dir.glob("*.parquet"))[-21:]
    if not paths:
        return None
    vols: list[float] = []
    for p in paths[:-1]:
        try:
            df = pd.read_parquet(p, columns=["symbol", "volume"])
        except Exception:
            continue
        row = df[df["symbol"] == symbol]
        if not row.empty and float(row["volume"].iloc[0]) > 0:
            vols.append(float(row["volume"].iloc[0]))
    try:
        today_df = pd.read_parquet(paths[-1], columns=["symbol", "volume"])
        today_vol = float(today_df[today_df["symbol"] == symbol]["volume"].iloc[0])
    except Exception:
        return None
    if not vols or today_vol <= 0:
        return None
    median = pd.Series(vols).median()
    return round(today_vol / median, 2) if median > 0 else None


def technical_gate(hist: pd.DataFrame, symbol: str, volume_ratio: float | None) -> dict | None:
    series = hist[hist["symbol"] == symbol].sort_values("date")
    closes = series["close"]
    if len(closes) < 60:
        return None
    close = float(closes.iloc[-1])
    ema20 = float(_ema(closes, 20).iloc[-1])
    ema21 = float(_ema(closes, 21).iloc[-1])
    ema50 = float(_ema(closes, 50).iloc[-1])
    ema200 = float(_ema(closes, 200).iloc[-1]) if len(closes) >= 200 else None
    rsi = _rsi14(closes)
    high52 = float(closes.tail(252).max()) if len(closes) >= 252 else float(closes.max())
    extension = (close - ema21) / ema21 if ema21 else 9.9
    trend_ok = close > ema50 and (ema200 is None or close > ema200)
    rsi_ok = rsi is not None and RSI_MIN <= rsi <= RSI_MAX
    extension_ok = extension <= EXTENSION_CAP
    breakout = close >= BREAKOUT_PROXIMITY * high52 and (volume_ratio or 0) >= BREAKOUT_VOL_MULT
    lows_recent = series["low"].tail(5) if "low" in series.columns and not series["low"].isna().all() else None
    pullback_touch = lows_recent is not None and bool((lows_recent <= ema20 * PULLBACK_TOUCH).any())
    pullback = trend_ok and pullback_touch and close > ema20 and float(closes.pct_change(63).iloc[-1]) > 0
    style = "BREAKOUT" if breakout else ("PULLBACK" if pullback else "")
    checks = {
        "trend>EMA50/200": trend_ok,
        f"RSI {RSI_MIN}-{RSI_MAX}": rsi_ok,
        f"extension<={int(EXTENSION_CAP * 100)}%": extension_ok,
        "trigger": bool(style),
    }
    passed = all(checks.values())
    detail = {
        "rsi": rsi,
        "ext_pct": round(extension * 100, 1),
        "vol_ratio": volume_ratio,
        "dist_52w_high": round((close / high52 - 1) * 100, 2),
        "style": style,
    }
    return {"passed": passed, "checks": checks, "detail": detail} if passed else {"passed": False, "checks": checks, "detail": detail}


BULL_PROMPT = """You are the BULL researcher on an investment committee. Argue the case FOR including this stock as a new long position (positional horizon of months).

PACKET:
{packet}

GATE RESULT: it already passed strict technical gates ({gate_detail}). Do not repeat technicals - argue catalysts, sector strength, fundamentals, news.

Respond with ONLY JSON: {{"verdict":"include"|"exclude","conviction":0-100,"reason":"max 40 words"}}
Being honest matters more than being agreeable - exclude if you genuinely cannot defend it."""


BEAR_PROMPT = """You are the BEAR researcher on an investment committee. Your job is to find every reason to EXCLUDE this stock from a new long position (positional horizon of months). You are never agreeable by default.

PACKET:
{packet}

GATE DETAIL: {gate_detail}. Scrutinize overheated moves, weak fundamentals, bad news, liquidity traps, manipulation risk in smallcaps.

Respond with ONLY JSON: {{"verdict":"include"|"exclude","conviction":0-100,"reason":"max 40 words"}}"""


def _debate_side(prompt_template: str, packet: dict, gate_detail: str, side: str) -> dict:
    prompt = prompt_template.format(packet=json.dumps(packet, indent=1), gate_detail=gate_detail)
    default = {"verdict": "include" if side == "bull" else "exclude", "conviction": 50, "reason": "llm unavailable"}
    if not llm_client.available():
        return default
    try:
        raw = llm_client.complete(prompt)
    except Exception as exc:
        return {**default, "reason": str(exc)[:80]}
    parsed = llm_client.extract_json(raw)
    if not parsed or str(parsed.get("verdict")) not in ("include", "exclude"):
        return {**default, "reason": "unparseable response"}
    try:
        conviction = max(0, min(100, int(parsed.get("conviction"))))
    except (TypeError, ValueError):
        conviction = 50
    return {
        "verdict": str(parsed["verdict"]),
        "conviction": conviction,
        "reason": str(parsed.get("reason", ""))[:160],
    }


def judge(bull: dict, bear: dict) -> tuple[str, str]:
    if bull["verdict"] == "exclude":
        return "REJECTED", f"bull excluded: {bull['reason']}"
    if bear["verdict"] == "include":
        return "INCLUDED", "committee consensus"
    if bear["conviction"] >= 65:
        return "HUMAN_REVIEW", f"bear dissents: {bear['reason']}"
    return "INCLUDED", f"included despite bear note: {bear['reason']}"


def run_committee(mom: pd.DataFrame, hist: pd.DataFrame, smap: pd.DataFrame, fundamentals: dict[str, dict], week: str) -> tuple[list[dict], int]:
    sym_sector = dict(zip(smap["symbol"].astype(str), smap["sector"].astype(str)))
    sym_rs_rank = dict(zip(smap.get("sector"), smap.groupby("sector")["sector"].transform("count"))) if False else {}
    candidates = []
    for _, row in mom.head(40).iterrows():
        symbol = str(row["symbol"])
        vr = _volume_ratio(symbol)
        gate = technical_gate(hist, symbol, vr)
        if gate and gate["passed"]:
            row_dict = row.to_dict()
            row_dict["_gate"] = gate
            candidates.append(row_dict)
        if len(candidates) >= MAX_DEBATES:
            break
    results: list[dict] = []
    for i, cand in enumerate(candidates):
        symbol = str(cand["symbol"])
        if i:
            import time

            time.sleep(12)
        packet = _packet(symbol, cand, sym_sector.get(symbol, "-"), None, fundamentals.get(symbol))
        gate_detail = json.dumps(cand["_gate"]["detail"])
        bull = _debate_side(BULL_PROMPT, packet, gate_detail, "bull")
        bear = _debate_side(BEAR_PROMPT, packet, gate_detail, "bear")
        decision, why = judge(bull, bear)
        results.append(
            {
                "symbol": symbol,
                "sector": sym_sector.get(symbol, "-"),
                "style": cand["_gate"]["detail"]["style"],
                "momentum_score": float(cand["momentum_score"]),
                "gate_detail": cand["_gate"]["detail"],
                "checks": cand["_gate"]["checks"],
                "bull": bull,
                "bear": bear,
                "decision": decision,
                "why": why,
            }
        )
        print(f"committee {symbol}: {decision} (bull {bull['verdict']}/{bull['conviction']}, bear {bear['verdict']}/{bear['conviction']})", flush=True)
    return results, len(candidates)


def included_entries(results: list[dict]) -> list[str]:
    return [r["symbol"] for r in results if r["decision"] == "INCLUDED"]
