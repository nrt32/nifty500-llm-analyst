import pandas as pd

MOMENTUM_LOOKBACKS = (21, 63, 126)


def momentum_ranks(prices: pd.DataFrame) -> pd.DataFrame:
    raise NotImplementedError("Phase 1")


def quality_scores(fundamentals: pd.DataFrame) -> pd.DataFrame:
    raise NotImplementedError("Phase 1")


def composite_score(prices: pd.DataFrame, fundamentals: pd.DataFrame) -> pd.DataFrame:
    raise NotImplementedError("Phase 1")
