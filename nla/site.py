import argparse
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import markdown

from nla.config import REPORTS_DIR, SITE_DIR
from nla.report import write_report

IST = timezone(timedelta(hours=5, minutes=30))

SECTIONS = [
    ("weekly", "Weekly Quant Screens", "Full screen + score-engine recommendations. Regenerated Sundays 09:00 IST."),
    ("daily", "Daily Scan Logs", "Ingestion health for each trading session: sources, backfills, ledger settlements."),
    ("scorecard", "Paper Scorecards", "Monthly validation - did the paper tranches beat the market?"),
    ("review", "Human Review Queue", "Quant-vs-LLM conflicts that need your judgment before acting."),
]

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - nifty500-llm-analyst</title>
<style>
{css}
</style>
</head>
<body>
<nav><a href="{root}index.html">&larr; All reports</a></nav>
{body}
<footer>Not investment advice.</footer>
</body>
</html>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nifty500-llm-analyst</title>
<style>
{css}
.hero p {{ color:#555; max-width: 46rem; }}
section {{ margin: 2rem 0; }}
section h2 {{ border-bottom: 2px solid #ddd; padding-bottom: .3rem; }}
section .sub {{ color:#666; font-size:.9rem; margin:.2rem 0 .6rem; }}
.card {{ display:block; padding:.6rem .8rem; margin:.35rem 0; background:#fff;
        border:1px solid #e0e0e0; border-radius:8px; text-decoration:none; }}
.card:hover {{ border-color:#888; }}
.card .t {{ font-weight:600; color:#111; }}
.card .d {{ color:#666; font-size:.85rem; }}
</style>
</head>
<body>
<div class="hero">
<h1>nifty500-llm-analyst</h1>
<p>Deterministic research pipeline for the top-1000 most liquid NSE stocks.
Quant factors propose, an LLM analyst critiques, a score engine decides -
and every hypothetical trade is paper-ledgered for validation. Not investment advice.</p>
</div>
{body}
<footer>Generated {generated}. Pipeline runs daily 19:15 IST and weekly Sundays 09:00 IST via GitHub Actions.</footer>
</body>
</html>
"""

CSS = """body { font-family: system-ui, -apple-system, sans-serif; max-width: 920px;
       margin: 2rem auto; padding: 0 1rem; line-height: 1.55;
       color: #222; background: #fafafa; }
h1, h2, h3 { line-height: 1.25; }
table { border-collapse: collapse; margin: 1rem 0; width: 100%; }
th, td { border: 1px solid #ccc; padding: 0.35rem 0.6rem; text-align: left; }
th { background: #f0f0f0; }
tr:nth-child(even) td { background: #f7f7f7; }
code { background: #eee; padding: 0.1rem 0.3rem; border-radius: 4px; }
pre { background: #eee; padding: 0.75rem; border-radius: 4px; overflow-x: auto; }
blockquote { color: #555; border-left: 3px solid #ccc; margin-left: 0; padding-left: 1rem; }
li { margin: 0.2rem 0; }
nav { margin-bottom: 1rem; font-size: .95rem; }
nav a { text-decoration: none; color: #0366d6; }
footer { margin-top: 3rem; font-size: 0.85rem; color: #777; }
"""


def _title_of(md_text: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", md_text, re.M)
    return match.group(1).strip() if match else fallback


def _date_of(md_text: str) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", md_text)
    if match:
        return match.group(1)
    match = re.search(r"(20\d{2}-W\d{2})", md_text)
    return match.group(1) if match else ""


def _sort_key(path: Path, text: str):
    iso = _date_of(text[:600])
    try:
        parsed = datetime.fromisoformat(iso).timestamp() if len(iso) == 10 else None
    except ValueError:
        parsed = None
    week = re.match(r"(20\d{2})-W(\d{2})", iso)
    if week:
        from datetime import datetime as _dt

        parsed = _dt.fromisocalendar(int(week.group(1)), int(week.group(2)), 1).timestamp()
    return (parsed or path.stat().st_mtime, path.name)


def render_page(source: Path) -> tuple[str, str]:
    text = source.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    title = _title_of(text, source.stem.replace("_", " ").replace("-", " "))
    page = PAGE_TEMPLATE.replace("{title}", title).replace("{body}", body).replace("{css}", CSS).replace("{root}", "../")
    return title, page


def build_site() -> int:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    sections: dict[str, list[tuple]] = {}
    count = 0
    for src in sorted(REPORTS_DIR.rglob("*.md")):
        rel_dir = src.parent.relative_to(REPORTS_DIR).as_posix()
        top = rel_dir.split("/")[0] if "/" in rel_dir else rel_dir
        text = src.read_text(encoding="utf-8")
        title, page = render_page(src)
        rel_html = src.with_suffix(".html").relative_to(REPORTS_DIR)
        dst = SITE_DIR / rel_html
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(page, encoding="utf-8")
        entry = (_sort_key(src, text), title, _date_of(text), rel_html.as_posix())
        placed = False
        for key, *_ in SECTIONS:
            if top == key or rel_dir.startswith(key):
                sections.setdefault(key, []).append(entry)
                placed = True
                break
        if not placed:
            sections.setdefault(top, []).append(entry)
        count += 1
    parts: list[str] = []
    for key, heading, blurb in SECTIONS:
        items = sorted(sections.get(key, []), key=lambda e: e[0], reverse=True)
        if not items:
            continue
        parts.append(f"<section id=\"{key}\"><h2>{heading}</h2><p class=\"sub\">{blurb}</p>")
        for _, title, date_str, href in items:
            label = f"{title} <span class='d'>· {date_str}</span>" if date_str else title
            parts.append(f"<a class=\"card\" href=\"{href}\"><span class=\"t\">{label}</span></a>")
        parts.append("</section>")
    for key, entries in sections.items():
        if any(k == key for k, _, _ in SECTIONS):
            continue
        items = sorted(entries, key=lambda e: e[0], reverse=True)
        parts.append(f"<section id=\"{key}\"><h2>{key.title()}</h2>")
        for _, title, date_str, href in items:
            parts.append(f"<a class=\"card\" href=\"{href}\"><span class=\"t\">{title}</span></a>")
        parts.append("</section>")
    generated = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    index = INDEX_TEMPLATE.replace("{body}", "\n".join(parts)).replace("{css}", CSS).replace("{generated}", generated)
    write_report(SITE_DIR / "index.html", index)
    stale = [
        p
        for p in SITE_DIR.rglob("*.html")
        if p.name != "index.html" and not (REPORTS_DIR / p.relative_to(SITE_DIR)).with_suffix(".md").exists()
    ]
    for p in stale:
        p.unlink()
    return count


def main() -> None:
    print(build_site())


if __name__ == "__main__":
    main()
