import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import markdown

from nla.config import DATA_DIR, REPORTS_DIR, SITE_DIR
from nla.report import write_report

IST = timezone(timedelta(hours=5, minutes=30))

SECTIONS = [
    ("weekly", "Weekly Screens", "Your main decision page — updated every Sunday."),
    ("daily", "Daily Logs", "Health checks — did today's data arrive OK?"),
    ("scorecard", "Scorecards", "Is the system actually beating the market?"),
    ("review", "Reviews", "Stocks the robots disagreed on — you decide."),
    ("audit", "Audits", "Behind-the-scenes checks on the pipeline itself."),
]

STAGE_BADGES = {
    "Leading": "good",
    "Pullback": "info",
    "Improving": "info",
    "Weakening": "warn",
    "Lagging": "bad",
}

INFO_BOXES = {
    "This Week's Action Summary": (
        "A one-paragraph answer: do you need to do anything this week?",
        "If it says entries were added, those are paper trades already logged. If it says nothing was added, you don't need to act — just read why.",
    ),
    "Universe health": (
        "How much price history the system has and how many stocks it covers.",
        "Glance to confirm data is fresh. If coverage drops, wait a day before trusting the screen.",
    ),
    "Sector relative strength": (
        "Which groups of stocks (like Healthcare or Energy) are doing better than the average stock lately.",
        "Use it to see where the market's strength is. You don't trade sectors directly — it just adds context.",
    ),
    "Top 30 momentum (watchlist)": (
        "The 30 strongest trending stocks right now, by price momentum.",
        "This is a watchlist, not a buy list. Real buys only happen after the Entry Committee below agrees.",
    ),
    "Bottom 5 (avoid list)": (
        "The weakest trends — what to stay away from.",
        "Just for context. You don't need to act on these.",
    ),
    "Entry Committee": (
        "The strict filter + two AI researchers debating each candidate. One argues for buying, one against.",
        "Look at who got included vs sent for your review. Included names are the only ones that can enter the paper portfolio.",
    ),
    "Ranked portfolio (score engine)": (
        "The final ranking after mixing price momentum with AI opinions and risk rules.",
        "If you were to act manually, this tells you position size and stop-loss for each name.",
    ),
    "Event watchlist": (
        "Stocks that just hit a 52-week high or had unusual volume today.",
        "Useful as early alerts. Combine with the main screen — don't chase a spike alone.",
    ),
    "Paper ledger (shadow validation)": (
        "A pretend portfolio that tracks what would have happened if you followed the system.",
        "Check total entries and how many are still open. Real performance appears in Scorecards over months.",
    ),
}

CSS = """
:root{--bg:#f8fafc;--card:#ffffff;--border:#e2e8f0;--text:#0f172a;--muted:#64748b;--accent:#2563eb;--green:#16a34a;--red:#dc2626;--amber:#d97706;--purple:#7c3aed;--radius:14px}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);margin:0;line-height:1.65;-webkit-font-smoothing:antialiased}
.wrap{max-width:960px;margin:0 auto;padding:0 20px}
nav{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.82);backdrop-filter:blur(12px);border-bottom:1px solid var(--border)}
nav .wrap{display:flex;align-items:center;justify-content:space-between;height:58px}
nav .brand{font-weight:700;letter-spacing:-.02em;text-decoration:none;color:var(--text);font-size:1.05rem}
nav .brand span{color:var(--accent)}
nav .links{display:flex;gap:18px}
nav .links a{color:var(--muted);text-decoration:none;font-size:.9rem}
nav .links a:hover{color:var(--text)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.back{display:inline-flex;align-items:center;gap:6px;background:var(--card);border:1px solid var(--border);border-radius:999px;padding:7px 14px;font-size:.9rem;color:var(--text);text-decoration:none;box-shadow:0 1px 2px rgba(0,0,0,.05)}
.back:hover{background:var(--bg);text-decoration:none}
.hero{padding:42px 0 18px}
.hero h1{font-size:2.1rem;letter-spacing:-.03em;margin:0 0 8px}
.hero p.lead{color:var(--muted);max-width:620px;margin:0;font-size:1.02rem}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.chip{background:var(--card);border:1px solid var(--border);border-radius:999px;padding:6px 12px;font-size:.82rem;color:var(--muted)}
.chip b{color:var(--text);font-weight:600}
section{margin:28px 0}
section h2{font-size:1.05rem;letter-spacing:-.01em;margin:0 0 4px;display:flex;align-items:center;gap:10px}
section h2 .count{font-weight:400;color:var(--muted);font-size:.9rem}
section .sub{color:var(--muted);font-size:.88rem;margin:0 0 14px}
.card{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);text-decoration:none;color:inherit;transition:box-shadow .15s,transform .15s,border-color .15s}
.card:hover{border-color:#cbd5e1;box-shadow:0 4px 12px rgba(0,0,0,.06);transform:translateY(-1px);text-decoration:none}
.card .t{font-weight:600;font-size:.95rem}
.card .d{color:var(--muted);font-size:.82rem;white-space:nowrap}
.archive{margin-top:10px}
.archive summary{cursor:pointer;color:var(--muted);font-size:.88rem;padding:8px 0;list-style:none}
.archive summary::-webkit-details-marker{display:none}
.archive summary:before{content:"▸ ";display:inline-block;transition:transform .15s}
.archive[open] summary:before{transform:rotate(90deg)}
h1{font-size:1.7rem;letter-spacing:-.02em;margin:18px 0 6px}
h1 + p{color:var(--muted);margin-top:0}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:.88rem;background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin:14px 0}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--border)}
th{background:#f1f5f9;color:var(--muted);font-weight:600;font-size:.8rem;text-transform:uppercase;letter-spacing:.03em;white-space:nowrap}
tr:last-child td{border-bottom:none}
tr:hover td{background:#f8fafc}
.num.pos{color:var(--green);font-weight:600}
.num.neg{color:var(--red);font-weight:600}
.badge{display:inline-block;padding:3px 9px;border-radius:999px;font-size:.75rem;font-weight:600;letter-spacing:.01em}
.badge.good{background:#dcfce7;color:#166534}
.badge.info{background:#dbeafe;color:#1e40af}
.badge.warn{background:#fef3c7;color:#92400e}
.badge.bad{background:#fee2e2;color:#991b1b}
.badge.review{background:#ede9fe;color:#5b21b6}
.stance{font-weight:700;font-size:.82rem;padding:2px 7px;border-radius:999px}
.stance.buy{color:var(--green);background:#dcfce7}
.stance.avoid{color:var(--red);background:#fee2e2}
.stance.hold{color:var(--amber);background:#fef3c7}
details.report-section{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);margin:14px 0;overflow:hidden}
details.report-section summary{padding:14px 16px;font-weight:600;cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between;gap:12px}
details.report-section summary::-webkit-details-marker{display:none}
details.report-section summary:after{content:"›";color:var(--muted);transition:transform .15s;display:inline-block}
details.report-section[open] summary:after{transform:rotate(90deg)}
details.report-section .section-body{padding:14px 16px;border-top:1px solid var(--border)}
.info-box{background:#f8fafc;border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin:0 0 14px;font-size:.88rem;color:var(--muted)}
.info-box b{color:var(--text)}
code{background:#f1f5f9;padding:2px 6px;border-radius:6px;font-size:.85em}
pre{background:#0f172a;color:#e2e8f0;padding:14px;border-radius:12px;overflow:auto}
footer{margin-top:40px;padding:20px 0 32px;border-top:1px solid var(--border);color:var(--muted);font-size:.84rem}
@media(max-width:720px){nav .links{display:none}.hero h1{font-size:1.6rem}table{font-size:.82rem}th,td{padding:7px 8px}}
"""

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
<footer>Pipeline runs daily at 19:15 IST and every Sunday at 09:00 IST. Paper trades only — not investment advice.</footer>
</div>
</body>
</html>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nifty500-llm-analyst — dashboard</title>
<style>{css}</style>
</head>
<body>
{nav}
<div class="wrap">
<div class="hero">
<h1>nifty<span style="color:var(--accent)">500</span>-llm-analyst</h1>
<p class="lead">A rules-first research pipeline for India's most liquid stocks. Price momentum proposes, two AI researchers debate, and a risk engine decides — every idea is paper-traded until it earns trust.</p>
{_chips}
</div>
{body}
<footer>Pipeline runs daily at 19:15 IST and every Sunday at 09:00 IST. Paper trades only — not investment advice.</footer>
</div>
</body>
</html>
"""


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
        ("Universe", str(status.get("liquid_universe", "—"))),
        ("Last data", str(status.get("date", "—"))),
        ("Source", str(status.get("price_source", "—"))),
        ("Paper trades", str(_ledger_count())),
        ("Updated", datetime.now(IST).strftime("%d %b %Y · %H:%M IST")),
    ]
    return '<div class="chips">' + "".join(f'<span class="chip">{k} <b>{v}</b></span>' for k, v in chips) + "</div>"


def _nav(index_mode: bool) -> str:
    if index_mode:
        links = "".join(f'<a href="#{key}">{label}</a>' for key, label, _ in SECTIONS)
        return f'<nav><div class="wrap"><a class="brand" href="#">nifty<span>500</span>-llm-analyst</a><div class="links">{links}</div></div></nav>'
    return '<nav><div class="wrap"><a class="brand" href="../index.html">nifty<span>500</span>-llm-analyst</a><div class="links"><a class="back" href="../index.html">← All reports</a></div></div></nav>'


def enhance(html: str) -> str:
    html = re.sub(
        r"<td>([+-]\d+(?:\.\d+)?%)</td>",
        lambda m: f'<td><span class="num {"pos" if m.group(1).startswith("+") else "neg"}">{m.group(1)}</span></td>',
        html,
    )
    for word, cls in STAGE_BADGES.items():
        html = html.replace(f"<td>{word}</td>", f'<td><span class="badge {cls}">{word}</span></td>')
    html = html.replace("<td>HUMAN_REVIEW</td>", '<td><span class="badge review">HUMAN&nbsp;REVIEW</span></td>')
    html = re.sub(
        r"<td>(avoid|hold|buy)/(\d+)</td>",
        lambda m: f'<td><span class="stance {m.group(1)}">{m.group(1)}</span>/{m.group(2)}</td>',
        html,
    )
    return html


def _wrap_report_sections(html: str) -> str:
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", html, flags=re.S)
    if len(parts) <= 2:
        return html
    out = [parts[0]]
    for i in range(1, len(parts), 2):
        h2 = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        title = re.sub(r"<[^>]+>", "", h2).strip()
        info = INFO_BOXES.get(title, "")
        info_html = f'<div class="info-box"><b>{title}</b> — {info[0]}<br><span style="color:var(--muted)">{info[1]}</span></div>' if info else ""
        open_attr = " open" if title == "This Week's Action Summary" else ""
        out.append(f'<details class="report-section"{open_attr}><summary>{title}</summary><div class="section-body">{info_html}{body}</div></details>')
    return "".join(out)


def render_page(source: Path) -> tuple[str, str]:
    text = source.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    body = enhance(body)
    body = _wrap_report_sections(body)
    m = re.search(r"^#\s+(.+)$", text, re.M)
    title = m.group(1).strip() if m else source.stem
    page = PAGE_TEMPLATE.replace("{title}", title).replace("{body}", body).replace("{css}", CSS).replace("{nav}", _nav(False))
    return title, page


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
        if key == "daily" and len(items) > 7:
            recent, older = items[:7], items[7:]
            parts.append(f'<section id="{key}"><h2>{heading} <span class="count">· {len(items)}</span></h2><p class="sub">{blurb}</p>')
            for _, title, date_str, href in recent:
                parts.append(f'<a class="card" href="{href}"><span class="t">{title}</span><span class="d">{date_str}</span></a>')
            parts.append(f'<details class="archive"><summary>Show older — {len(older)} more</summary>')
            for _, title, date_str, href in older:
                parts.append(f'<a class="card" href="{href}"><span class="t">{title}</span><span class="d">{date_str}</span></a>')
            parts.append("</details></section>")
        else:
            parts.append(f'<section id="{key}"><h2>{heading} <span class="count">· {len(items)}</span></h2><p class="sub">{blurb}</p>')
            for _, title, date_str, href in items:
                parts.append(f'<a class="card" href="{href}"><span class="t">{title}</span><span class="d">{date_str}</span></a>')
            parts.append("</section>")
    for key, entries in sections.items():
        if any(k == key for k, _, _ in SECTIONS):
            continue
        items = sorted(entries, key=lambda e: e[0], reverse=True)
        parts.append(f'<section id="{key}"><h2>{key.title()}</h2>')
        for _, title, date_str, href in items:
            parts.append(f'<a class="card" href="{href}"><span class="t">{title}</span><span class="d">{date_str}</span></a>')
        parts.append("</section>")
    index_body = "\n".join(parts)
    index = INDEX_TEMPLATE.replace("{body}", index_body).replace("{css}", CSS).replace("{_chips}", _stats_chips()).replace("{nav}", _nav(True))
    write_report(SITE_DIR / "index.html", index)
    stale = [p for p in SITE_DIR.rglob("*.html") if p.name != "index.html" and not (REPORTS_DIR / p.relative_to(SITE_DIR)).with_suffix(".md").exists()]
    for p in stale:
        p.unlink()
    return count


def _date_of(text: str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text[:600])
    if m:
        return m.group(1)
    m = re.search(r"(20\d{2}-W\d{2})", text[:600])
    return m.group(1) if m else ""


def _sort_key(path: Path, text: str):
    iso = _date_of(text[:600])
    parsed = None
    try:
        if len(iso) == 10:
            parsed = datetime.fromisoformat(iso).timestamp()
        else:
            w = re.match(r"(20\d{2})-W(\d{2})", iso)
            if w:
                parsed = datetime.fromisocalendar(int(w.group(1)), int(w.group(2)), 1).timestamp()
    except ValueError:
        parsed = None
    return (parsed if parsed is not None else path.stat().st_mtime, path.name)


def main() -> None:
    print(build_site())


if __name__ == "__main__":
    main()
