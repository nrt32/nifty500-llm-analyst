import argparse
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import markdown

from nla.config import DATA_DIR, REPORTS_DIR, SITE_DIR
from nla.report import write_report

IST = timezone(timedelta(hours=5, minutes=30))

SECTIONS = [
    ("weekly", "Weekly Quant Screens", "Full screen + score-engine recommendations. Regenerated Sundays 09:00 IST."),
    ("daily", "Daily Scan Logs", "Ingestion health for each trading session: sources, backfills, ledger settlements."),
    ("scorecard", "Paper Scorecards", "Monthly validation - did the paper tranches beat the market?"),
    ("review", "Human Review Queue", "Quant-vs-LLM conflicts that need your judgment before acting."),
]

STAGE_BADGES = {
    "Leading": "good",
    "Pullback": "info",
    "Improving": "info",
    "Weakening": "warn",
    "Lagging": "bad",
}

CSS = """
:root{--bg:#0d1117;--panel:#161b22;--panel2:#1c2128;--border:#30363d;--text:#e6edf3;--muted:#8b949e;
--accent:#58a6ff;--green:#3fb950;--red:#f85149;--amber:#d29922;--purple:#bc8cff;}
*{box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);
margin:0;line-height:1.6;}
.wrap{max-width:1000px;margin:0 auto;padding:0 1.25rem;}
nav{position:sticky;top:0;z-index:10;background:rgba(13,17,23,.92);backdrop-filter:blur(8px);
border-bottom:1px solid var(--border);}
nav .wrap{display:flex;align-items:center;justify-content:space-between;height:56px;}
nav .brand{font-weight:700;color:var(--text);text-decoration:none;font-size:1.05rem;}
nav .brand span{color:var(--accent)}
nav .links a{color:var(--muted);text-decoration:none;margin-left:1.1rem;font-size:.92rem;}
nav .links a:hover{color:var(--accent)}
h1{font-size:1.9rem;margin:.2rem 0;}
h2{font-size:1.25rem;border-bottom:1px solid var(--border);padding-bottom:.45rem;margin-top:2.2rem;}
h3{font-size:1.05rem;}
a{color:var(--accent);}
.hero{padding:2.2rem 0 1rem;}
.hero p{color:var(--muted);max-width:46rem;margin:.4rem 0 1rem;}
.chips{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.9rem;}
.chip{background:var(--panel);border:1px solid var(--border);border-radius:999px;padding:.28rem .8rem;
font-size:.82rem;color:var(--muted);}
.chip b{color:var(--text)}
section{margin:2.4rem 0;}
section .sub{color:var(--muted);font-size:.9rem;margin:.15rem 0 .8rem;}
.card{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:.75rem 1rem;
margin:.45rem 0;background:var(--panel);border:1px solid var(--border);border-radius:10px;
text-decoration:none;transition:border-color .15s, transform .15s;}
.card:hover{border-color:var(--accent);transform:translateY(-1px);}
.card .t{font-weight:600;color:var(--text)}
.card .d{color:var(--muted);font-size:.85rem;font-variant-numeric:tabular-nums;}
table{border-collapse:collapse;margin:1rem 0;width:100%;font-size:.92rem;}
th,td{border:1px solid var(--border);padding:.42rem .65rem;text-align:left;}
th{background:var(--panel2);color:var(--muted);font-weight:600;white-space:nowrap;}
tr:nth-child(even) td{background:rgba(255,255,255,.02)}
tr:hover td{background:rgba(88,166,255,.06)}
.num.pos{color:var(--green);font-variant-numeric:tabular-nums;}
.num.neg{color:var(--red);font-variant-numeric:tabular-nums;}
.badge{display:inline-block;padding:.12rem .55rem;border-radius:999px;font-size:.78rem;font-weight:600;}
.badge.good{background:rgba(63,185,80,.15);color:var(--green);}
.badge.info{background:rgba(88,166,255,.15);color:var(--accent);}
.badge.warn{background:rgba(210,153,34,.15);color:var(--amber);}
.badge.bad{background:rgba(248,81,73,.15);color:var(--red);}
.badge.review{background:rgba(188,140,255,.18);color:var(--purple);}
.stance{display:inline-block;padding:.1rem .5rem;border-radius:6px;font-weight:600;font-size:.85rem;}
.stance.buy,.stance.avoid{background:rgba(248,81,73,.15);color:var(--red);}
.stance.hold{background:rgba(210,153,34,.15);color:var(--amber);}
code{background:var(--panel2);padding:.1rem .35rem;border-radius:4px;font-size:.88em;}
pre{background:var(--panel);padding:.8rem;border-radius:8px;overflow-x:auto;border:1px solid var(--border);}
blockquote{color:var(--muted);border-left:3px solid var(--border);margin-left:0;padding-left:1rem;}
footer{margin-top:3.5rem;padding:1.2rem 0 2rem;border-top:1px solid var(--border);
font-size:.85rem;color:var(--muted);}
@media(max-width:720px){nav .links{display:none}.hero h1{font-size:1.5rem}}
"""

NAV = """<nav><div class="wrap"><a class="brand" href="{root}index.html">nifty<span>500</span>-llm-analyst</a>
<div class="links">{links}</div></div></nav>"""


def _read_status() -> dict:
    try:
        return json.loads((DATA_DIR / "status.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ledger_count() -> int:
    try:
        lines = (DATA_DIR / "ledger" / "paper_ledger.csv").read_text(encoding="utf-8").strip().splitlines()
        return max(0, len(lines) - 1)
    except Exception:
        return 0


def _stats_chips() -> str:
    status = _read_status()
    chips = [
        ("Universe", str(status.get("liquid_universe", "-"))),
        ("Last ingest", str(status.get("date", "-"))),
        ("Price source", str(status.get("price_source", "-"))),
        ("Paper entries", str(_ledger_count())),
        ("Updated", datetime.now(IST).strftime("%d %b %Y, %H:%M IST")),
    ]
    return '<div class="chips">' + "".join(f'<span class="chip">{k}: <b>{v}</b></span>' for k, v in chips) + "</div>"


def _nav(root: str, index_mode: bool) -> str:
    if index_mode:
        links = "".join(f'<a href="#{key}">{label}</a>' for key, label, _ in SECTIONS)
        return NAV.replace("{root}", root).replace("{links}", links)
    return NAV.replace("{root}", root).replace("{links}", '<a href="index.html">All reports</a>')


def enhance(html: str) -> str:
    html = re.sub(
        r"<td>([+-]\d+(?:\.\d+)?%)</td>",
        lambda m: f'<td><span class="num {"pos" if m.group(1).startswith("+") else "neg"}">{m.group(1)}</span></td>',
        html,
    )
    for word, cls in STAGE_BADGES.items():
        html = html.replace(f"<td>{word}</td>", f'<td><span class="badge {cls}">{word}</span></td>')
    html = html.replace("<td>HUMAN_REVIEW</td>", '<td><span class="badge review">HUMAN REVIEW</span></td>')
    html = re.sub(
        r"<td>(avoid|hold|buy)/(\d+)</td>",
        lambda m: f'<td><span class="stance {m.group(1)}">{m.group(1)}</span>/{m.group(2)}</td>',
        html,
    )
    html = re.sub(r"<td>-(?:\.00)?%</td>", '<td><span style="color:var(--muted)">-</span></td>', html)
    return html


def render_page(source: Path) -> tuple[str, str]:
    text = source.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    body = enhance(body)
    match = re.search(r"^#\s+(.+)$", text, re.M)
    title = match.group(1).strip() if match else source.stem
    page = (
        PAGE_TEMPLATE.replace("{title}", title)
        .replace("{body}", body)
        .replace("{css}", CSS)
        .replace("{root}", "../")
        .replace("{nav}", _nav("../", False))
    )
    return title, page


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - nifty500-llm-analyst</title>
<style>{css}</style>
</head>
<body>
{nav}
<div class="wrap">
{body}
<footer>Pipeline: daily 19:15 IST · weekly Sundays 09:00 IST · monthly scorecard. Not investment advice.</footer>
</div>
</body>
</html>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nifty500-llm-analyst - research dashboard</title>
<style>{css}</style>
</head>
<body>
{nav}
<div class="wrap">
<div class="hero">
<h1>nifty<span style="color:var(--accent)">500</span>-llm-analyst</h1>
<p>Deterministic research pipeline over the most liquid NSE names: quant factors propose,
an LLM analyst critiques every candidate, a hard-rule score engine decides -
and each hypothetical trade is paper-ledgered against the market until it earns trust.</p>
{_chips}
</div>
{body}
<footer>Pipeline: daily 19:15 IST · weekly Sundays 09:00 IST · monthly scorecard. Not investment advice.</footer>
</div>
</body>
</html>
"""


def build_site() -> int:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    sections: dict[str, list[tuple]] = {}
    count = 0
    for src in sorted(REPORTS_DIR.rglob("*.md")):
        rel_dir = src.parent.relative_to(REPORTS_DIR).as_posix()
        top = rel_dir.split("/")[0]
        text = src.read_text(encoding="utf-8")
        title, page = render_page(src)
        rel_html = src.with_suffix(".html").relative_to(REPORTS_DIR)
        dst = SITE_DIR / rel_html
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(page, encoding="utf-8")
        entry = (_sort_key(src, text), title, _date_of(text), rel_html.as_posix())
        for key, *_ in SECTIONS:
            if top == key or rel_dir.startswith(key):
                sections.setdefault(key, []).append(entry)
                break
        else:
            sections.setdefault(top, []).append(entry)
        count += 1
    parts: list[str] = []
    for key, heading, blurb in SECTIONS:
        items = sorted(sections.get(key, []), key=lambda e: e[0], reverse=True)
        if not items:
            continue
        parts.append(f'<section id="{key}"><h2>{heading} <span style="color:var(--muted);font-weight:400">· {len(items)}</span></h2>')
        parts.append(f'<p class="sub">{blurb}</p>')
        for _, title, date_str, href in items:
            label = f"{title}"
            when = f'<span class="d">{date_str}</span>' if date_str else ""
            parts.append(f'<a class="card" href="{href}"><span class="t">{label}</span>{when}</a>')
        parts.append("</section>")
    for key, entries in sections.items():
        if any(k == key for k, _, _ in SECTIONS):
            continue
        items = sorted(entries, key=lambda e: e[0], reverse=True)
        parts.append(f'<section id="{key}"><h2>{key.title()}</h2>')
        for _, title, date_str, href in items:
            parts.append(f'<a class="card" href="{href}"><span class="t">{title}</span></a>')
        parts.append("</section>")
    index_body = "\n".join(parts)
    index = (
        INDEX_TEMPLATE.replace("{body}", index_body)
        .replace("{css}", CSS)
        .replace("{_chips}", _stats_chips())
        .replace("{nav}", _nav("", True))
    )
    write_report(SITE_DIR / "index.html", index)
    stale = [
        p
        for p in SITE_DIR.rglob("*.html")
        if p.name != "index.html" and not (REPORTS_DIR / p.relative_to(SITE_DIR)).with_suffix(".md").exists()
    ]
    for p in stale:
        p.unlink()
    return count


def _title_of(text: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", text, re.M)
    return match.group(1).strip() if match else fallback


def _date_of(text: str) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text[:600])
    if match:
        return match.group(1)
    match = re.search(r"(20\d{2}-W\d{2})", text[:600])
    return match.group(1) if match else ""


def _sort_key(path: Path, text: str):
    iso = _date_of(text[:600])
    parsed = None
    try:
        if len(iso) == 10:
            parsed = datetime.fromisoformat(iso).timestamp()
        else:
            week = re.match(r"(20\d{2})-W(\d{2})", iso)
            if week:
                parsed = datetime.fromisocalendar(int(week.group(1)), int(week.group(2)), 1).timestamp()
    except ValueError:
        parsed = None
    return (parsed if parsed is not None else path.stat().st_mtime, path.name)


def main() -> None:
    print(build_site())


if __name__ == "__main__":
    main()
