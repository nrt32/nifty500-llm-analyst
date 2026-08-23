import pandas as pd

MAX_POSITION_PCT = 10.0
MAX_SECTOR_PCT = 30.0
MAX_POSITIONS = 20
STOP_ATR_MULT = 2.0


def evaluate(candidates: pd.DataFrame, holdings: pd.DataFrame, sector_map: pd.DataFrame) -> pd.DataFrame:
    raise NotImplementedError("Phase 3")
