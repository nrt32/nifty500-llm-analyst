import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
REFERENCE_DIR = DATA_DIR / "reference"
PRICE_DIR = DATA_DIR / "prices"
REPORTS_DIR = ROOT_DIR / "reports"
SITE_DIR = ROOT_DIR / "site"

for _directory in (DATA_DIR, REFERENCE_DIR, PRICE_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

UNIVERSE_CSV = REFERENCE_DIR / "universe_nifty500.csv"


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


LLM_PROVIDER = env("NLA_LLM_PROVIDER", "opencode")
MODEL = env("NLA_MODEL", "deepseek-v4-flash-free")
GEMINI_MODEL = env("NLA_GEMINI_MODEL", "gemini-2.0-flash")
OPENCODE_BASE_URL = env("OPENCODE_BASE_URL", "https://opencode.ai/zen/v1")
OPENCODE_API_KEY = env("OPENCODE_API_KEY")
GEMINI_API_KEY = env("GEMINI_API_KEY")
