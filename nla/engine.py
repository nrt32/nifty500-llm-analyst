import pandas as pd

MAX_POSITION_PCT = 10.0
MAX_SECTOR_PCT = 30.0
MAX_POSITIONS = 20
STOP_ATR_MULT = 2.0
STOP_FLOOR_PCT = 0.08
STOP_CAP_PCT = 0.20

BLEND_QUANT_W = 0.65
BLEND_LLM_W = 0.35
CONFLICT_GAP = 30.0


def volatility_proxy(hist: pd.DataFrame, symbol: str, window: int = 14, as_of=None) -> float | None:
    series = hist[hist["symbol"] == symbol]
    if as_of is not None:
        series = series[series["date"].astype(str) <= str(as_of)]
    closes = series.sort_values("date")["close"].tail(window + 1)
    if len(closes) < window:
        return None
    rets = closes.pct_change().dropna()
    if rets.empty or rets.abs().mean() == 0:
        return None
    return float(rets.abs().mean())


def stop_pct_from_vol(vol: float | None) -> float | None:
    if vol is None:
        return None
    return round(min(max(STOP_ATR_MULT * vol, STOP_FLOOR_PCT), STOP_CAP_PCT), 4)


def _cycle_multiplier(rs: pd.DataFrame | None, sector: str | None) -> float:
    if rs is None or rs.empty or not sector or sector not in set(rs["sector"]):
        return 1.0
    row = rs[rs["sector"] == sector]
    pct = float(row["rs_score"].iloc[0]) / 100.0
    return round(0.92 + 0.13 * pct, 4)


def evaluate(
    candidates: pd.DataFrame,
    holdings: pd.DataFrame,
    sector_map: pd.DataFrame,
    hist: pd.DataFrame | None = None,
    rs: pd.DataFrame | None = None,
    memos: dict[str, dict] | None = None,
    fundamentals: dict[str, dict] | None = None,
) -> pd.DataFrame:
    memos = memos or {}
    fundamentals = fundamentals or {}
    sym_sector = dict(zip(sector_map["symbol"].astype(str), sector_map["sector"].astype(str)))
    out = candidates.copy()
    out["quant_score"] = out["momentum_score"]
    convictions: list[float | None] = []
    stances: list[str] = []
    blends: list[float] = []
    flags: list[str] = []
    mults: list[float] = []
    stops: list[float | None] = []
    for _, r in out.iterrows():
        symbol = str(r["symbol"])
        memo = memos.get(symbol)
        conviction = None
        stance = "-"
        if memo and memo.get("conviction") is not None:
            try:
                conviction = float(memo["conviction"])
                stance = str(memo.get("stance", "-"))
            except (TypeError, ValueError):
                conviction = None
        quant = float(r["quant_score"])
        if conviction is None:
            blended = quant
            flags.append("")
        else:
            blended = BLEND_QUANT_W * quant + BLEND_LLM_W * conviction
            gap = abs(quant - conviction)
            flags.append("HUMAN_REVIEW" if gap >= CONFLICT_GAP else "")
        convictions.append(conviction)
        stances.append(stance)
        blends.append(blended)
        sector = sym_sector.get(symbol)
        mult = _cycle_multiplier(rs, sector)
        fund = (fundamentals.get(symbol) or {}).get("metrics", {})
        roce = fund.get("roce")
        pe = fund.get("stock p/e")
        if isinstance(roce, (int, float)) and roce < 8:
            mult *= 0.93
        if isinstance(pe, (int, float)) and pe > 60:
            mult *= 0.95
        if int(r.get("obs", 999)) < 130:
            mult *= 0.92
        mults.append(round(mult, 4))
        vol = volatility_proxy(hist, symbol) if hist is not None else None
        stops.append(stop_pct_from_vol(vol))
    out["llm_conviction"] = convictions
    out["llm_stance"] = stances
    out["blended_score"] = [round(b, 1) for b in blends]
    out["flag"] = flags
    out["final_score"] = [round(b * m, 1) for b, m in zip(blends, mults)]
    out["cycle_mult"] = mults
    out["stop_pct"] = stops
    out["sector_tag"] = [sym_sector.get(str(s), "-") for s in out["symbol"]]
    out["suggested_weight_pct"] = 0.0
    if hist is not None:
        eligible = out[out["flag"] != "HUMAN_REVIEW"].sort_values("final_score", ascending=False)
        sel = eligible.head(MAX_POSITIONS)
        inv_vol: dict[str, float] = {}
        for _, r in sel.iterrows():
            vol = volatility_proxy(hist, str(r["symbol"]), window=21)
            inv_vol[str(r["symbol"])] = 1.0 / vol if vol and vol > 0 else 0.0
        total_inv = sum(inv_vol.values())
        sector_used: dict[str, float] = {}
        for _, r in sel.iterrows():
            symbol = str(r["symbol"])
            raw = (inv_vol.get(symbol, 0.0) / total_inv * 100.0) if total_inv > 0 else 0.0
            w = min(raw, MAX_POSITION_PCT)
            sec = str(r["sector_tag"])
            if sec != "-":
                used = sector_used.get(sec, 0.0)
                if used + w > MAX_SECTOR_PCT:
                    w = max(0.0, MAX_SECTOR_PCT - used)
                sector_used[sec] = used + w
            out.loc[out["symbol"] == symbol, "suggested_weight_pct"] = round(w, 2)
    return out
