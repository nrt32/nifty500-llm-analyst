import pandas as pd

METRICS_HIGHER_BETTER = {"roce", "roe", "ebit_margin", "eps_growth", "revenue_growth"}
METRICS_LOWER_BETTER = {"debt_to_equity", "pe"}


def _sector_relative_scores(df: pd.DataFrame, sector_col: str = "sector") -> pd.DataFrame:
    out = df.copy()
    for metric in METRICS_HIGHER_BETTER | METRICS_LOWER_BETTER:
        if metric not in out.columns:
            continue
        # sector-relative percentile, handling NaN
        def ranker(g):
            if g[metric].notna().sum() < 3:
                return pd.Series([50.0] * len(g), index=g.index)
            # for lower-better, invert ranking
            ascending = metric in METRICS_LOWER_BETTER
            # need to handle Financials D/E special case later
            pct = g[metric].rank(pct=True, ascending=ascending) * 100
            return pct

        out[f"{metric}_pct"] = out.groupby(sector_col, group_keys=False).apply(ranker, include_groups=False)
    # Handle Financial Services D/E exclusion: set to neutral 50
    if "debt_to_equity_pct" in out.columns:
        mask = out[sector_col] == "Financial Services"
        out.loc[mask, "debt_to_equity_pct"] = 50.0
    pct_cols = [c for c in out.columns if c.endswith("_pct")]
    if pct_cols:
        out["quality_score"] = out[pct_cols].mean(axis=1, skipna=True).round(1)
        out["quality_score"] = out["quality_score"].fillna(50.0)
    else:
        out["quality_score"] = 50.0
    return out


def compute_quality_scores(fundamentals: dict[str, dict], sector_map: pd.DataFrame) -> pd.DataFrame:
    rows = []
    sector_lookup = dict(zip(sector_map["symbol"].astype(str), sector_map["sector"].astype(str)))
    for symbol, payload in fundamentals.items():
        # Try screener metrics first, then tickertape
        screener = payload.get("metrics", {}) if isinstance(payload, dict) else {}
        tt = payload.get("tickertape", {}) if isinstance(payload, dict) else {}
        # Extract with fallbacks
        roce = screener.get("roce") if isinstance(screener.get("roce"), (int, float)) else tt.get("tt_roe")
        roe = screener.get("roe") if isinstance(screener.get("roe"), (int, float)) else tt.get("tt_roe")
        # D/E from tickertape (now available), lower is better
        de = tt.get("tt_debt_to_equity")
        # P/E: prefer screener stock p/e, fallback to tt_pe
        pe = screener.get("stock p/e") if isinstance(screener.get("stock p/e"), (int, float)) else tt.get("tt_pe")
        # Ebit margin: incEbi / incTrev
        ebit_margin = None
        if isinstance(tt.get("incEbi"), (int, float)) and isinstance(tt.get("incTrev"), (int, float)) and tt["incTrev"] != 0:
            ebit_margin = round(tt["incEbi"] / tt["incTrev"] * 100, 2)
        # For now, eps_growth and revenue_growth require prior year - not yet stored, skip
        sector = sector_lookup.get(symbol, "Unclassified")
        rows.append({
            "symbol": symbol,
            "sector": sector,
            "roce": roce,
            "roe": roe,
            "debt_to_equity": de,
            "pe": pe,
            "ebit_margin": ebit_margin,
        })
    if not rows:
        return pd.DataFrame(columns=["symbol", "sector", "quality_score"])
    df = pd.DataFrame(rows)
    # Filter to only sectors with at least 3 members for meaningful relative ranking; others get neutral
    df = _sector_relative_scores(df, sector_col="sector")
    return df[["symbol", "sector", "roce", "roe", "debt_to_equity", "pe", "ebit_margin", "quality_score"]]


def quality_scores(fundamentals: pd.DataFrame) -> pd.DataFrame:
    raise NotImplementedError("Use compute_quality_scores with dict payloads")
