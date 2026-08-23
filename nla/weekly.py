import sys
from datetime import date

from nla.config import REPORTS_DIR
from nla.report import write_report
from nla.site import build_site

PHASE_STATUS = """
| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Data ingestion skeleton + CI workflows | in progress |
| 1 | Factor engine + sector RS + plain weekly screen | planned |
| 2 | LLM analyst memos (opencode/Gemini) | planned |
| 3 | Score engine, sizing, stops, paper ledger | planned |
| 4 | Sector-cycle classifier + dynamic watchlist | planned |
| 5 | Weight tuning from paper results | planned |
"""


def week_slug(ref: date | None = None) -> str:
    d = ref or date.today()
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def render_report(slug: str) -> str:
    return (
        f"# Weekly Review {slug}\n\n"
        f"Generated {date.today().isoformat()} - Phase 0 placeholder report.\n\n"
        "## Pipeline phase status\n\n"
        f"{PHASE_STATUS.strip()}\n\n"
        "---\n"
        "_Not investment advice. Personal research tool for the repository owner._\n"
    )


def main() -> int:
    slug = week_slug()
    write_report(REPORTS_DIR / "weekly" / f"{slug}.md", render_report(slug))
    pages = build_site()
    print(f"weekly report written: reports/weekly/{slug}.md (site pages: {pages})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
