import pandas as pd

MOMENTUM_LOOKBACKS = (21, 63, 126)

MIN_OBS = {21: 25, 63: 70, 126: 130}


def momentum_ranks(prices: pd.DataFrame) -> pd.DataFrame:
    close = prices.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index()
    obs = close.notna().sum()
    rows: list[pd.Series] = []
    last = close.iloc[-1]
    for lb in MOMENTUM_LOOKBACKS:
        if len(close) <= lb:
            continue
        ret = close.pct_change(lb).iloc[-1]
        for symbol in close.columns:
            count = int(obs.get(symbol, 0))
            if count < MIN_OBS[lb] or pd.isna(ret.get(symbol)):
                continue
            rows.append(pd.Series({"symbol": symbol, "lookback": lb, "ret": ret[symbol]}))
    if not rows:
        return pd.DataFrame(columns=["symbol", "price", "obs", "ret_21d", "ret_63d", "ret_126d", "momentum_score", "momentum_rank"])
    long = pd.DataFrame(rows)
    long["pct"] = long.groupby("lookback")["ret"].rank(pct=True)
    wide = long.pivot(index="symbol", columns="lookback", values=["ret", "pct"])
    out = pd.DataFrame(index=wide.index)
    for lb in MOMENTUM_LOOKBACKS:
        col = f"ret_{lb}d"
        out[col] = wide[("ret", lb)] if ("ret", lb) in wide.columns else float("nan")
    pct_cols = [("pct", lb) for lb in MOMENTUM_LOOKBACKS if ("pct", lb) in wide.columns]
    out["momentum_score"] = (wide[pct_cols].mean(axis=1) * 100).round(1)
    out["price"] = last.reindex(out.index)
    out["obs"] = obs.reindex(out.index).astype(int)
    out = out.reset_index().rename(columns={"index": "symbol"})
    out["momentum_rank"] = out["momentum_score"].rank(ascending=False, method="min").astype(int)
    return out.sort_values("momentum_rank").reset_index(drop=True)[
        ["symbol", "price", "obs", "ret_21d", "ret_63d", "ret_126d", "momentum_score", "momentum_rank"]
    ]


def quality_scores(fundamentals: pd.DataFrame) -> pd.DataFrame:
    raise NotImplementedError("Phase 1: needs fundamentals source design (screener.in cache)")


def composite_score(prices: pd.DataFrame, fundamentals: pd.DataFrame) -> pd.DataFrame:
    raise NotImplementedError("Phase 1: lands with quality factors")
