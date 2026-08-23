from pathlib import Path

import markdown

from nla.config import REPORTS_DIR, SITE_DIR
from nla.report import write_report

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - nifty500-llm-analyst</title>
<style>
body { font-family: system-ui, -apple-system, sans-serif; max-width: 880px;
       margin: 2rem auto; padding: 0 1rem; line-height: 1.55;
       color: #222; background: #fafafa; }
h1, h2, h3 { line-height: 1.25; }
table { border-collapse: collapse; margin: 1rem 0; }
th, td { border: 1px solid #ccc; padding: 0.35rem 0.6rem; text-align: left; }
th { background: #f0f0f0; }
code { background: #eee; padding: 0.1rem 0.3rem; border-radius: 4px; }
pre { background: #eee; padding: 0.75rem; border-radius: 4px; overflow-x: auto; }
blockquote { color: #555; border-left: 3px solid #ccc; margin-left: 0; padding-left: 1rem; }
li { margin: 0.2rem 0; }
footer { margin-top: 3rem; font-size: 0.85rem; color: #777; }
</style>
</head>
<body>
{body}
<footer>Not investment advice.</footer>
</body>
</html>
"""


def render_html(source: Path) -> str:
    text = source.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    title = source.stem.replace("_", " ").replace("-", " ")
    return PAGE_TEMPLATE.replace("{title}", title).replace("{body}", body)


def build_site() -> int:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[float, str]] = []
    for src in sorted(REPORTS_DIR.rglob("*.md")):
        rel = src.with_suffix(".html").relative_to(REPORTS_DIR)
        dst = SITE_DIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(render_html(src), encoding="utf-8")
        entries.append((src.stat().st_mtime, rel.as_posix()))
    items = "\n".join(
        f'<li><a href="{href}">{href}</a></li>'
        for _, href in sorted(entries, key=lambda e: e[0], reverse=True)
    )
    if items:
        body = "<h1>nifty500-llm-analyst</h1>\n<ul>\n" + items + "\n</ul>"
    else:
        body = "<h1>nifty500-llm-analyst</h1>\n<p>No reports yet.</p>"
    write_report(SITE_DIR / "index.html", PAGE_TEMPLATE.replace("{title}", "Reports").replace("{body}", body))
    return len(entries)


def main() -> None:
    print(build_site())


if __name__ == "__main__":
    main()
