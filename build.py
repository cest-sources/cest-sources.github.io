"""Turn the cest-sources DokuWiki backup into a static site."""

from __future__ import annotations

import html
import re
import shutil
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGES = ROOT / "backups" / "2026-10-11" / "cest-sources.org" / "htdocs" / "data" / "pages"
MEDIA = ROOT / "backups" / "2026-10-11" / "cest-sources.org" / "htdocs" / "data" / "media"
OUT = Path(__file__).resolve().parent
SKIP = {"wiki", "playground"}
CONTACT = "moritz.zaiss@tuebingen.mpg.de"

HEADING = re.compile(r"^(={2,6})\s*(.*?)\s*={2,6}\s*$")
LIST_ITEM = re.compile(r"^(\s{2,})([*-])\s+(.*)$")
RULE = re.compile(r"^\s*-{4,}\s*$")
CODE = re.compile(r"<code(?:\s+([A-Za-z0-9_+-]+))?>(.*?)</code>", re.I | re.S)


def clean_id(raw: str) -> str:
    text = raw.strip().lower().replace("/", ":")
    out = []
    for char in text:
        if char.isalnum() or char in ":._-":
            out.append(char)
        else:
            out.append("_")
    collapsed = re.sub(r"_+", "_", "".join(out))
    collapsed = re.sub(r"[:_]+(?=:)|(?<=:)[:_]+", ":", collapsed)
    return collapsed.strip(":_")


def load_pages() -> dict[str, str]:
    pages: dict[str, str] = {}
    for path in PAGES.rglob("*.txt"):
        rel = path.relative_to(PAGES).as_posix()[:-4]
        if rel.split("/", 1)[0] in SKIP:
            continue
        pages[rel] = path.read_text(encoding="utf-8")
    return pages


TIER1 = 2
TIER_FORMATS = ["%s.", "%s.%d", "%s.%d.%d", "%s.%d.%d.%d", "%s.%d.%d.%d.%d"]


def heading_marker(title: str) -> tuple[int, str]:
    match = re.match(r"^(-+)\s+(.*)$", title.strip())
    if not match:
        return 0, title.strip()
    return len(match.group(1)), match.group(2).strip()


class HeadingNumbers:
    """Same scheme as the DokuWiki numberedheadings plugin, tier 1 = heading level 2."""

    def __init__(self) -> None:
        self.counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    def next(self, level: int) -> str:
        self.counts[level] = self.counts.get(level, 0) + 1
        for deeper in range(level + 1, 6):
            self.counts[deeper] = 0
        tier = level - TIER1 + 1
        if tier < 1:
            return ""
        values = [self.counts[i] for i in range(1, 6)]
        picked = values[max(0, TIER1 - 1) : max(0, TIER1 - 1) + tier]
        if tier <= len(TIER_FORMATS):
            return TIER_FORMATS[tier - 1] % tuple(picked)
        return ".".join(str(number) for number in picked)


def pretty_name(page_id: str) -> str:
    if page_id == "glucocest":
        return "glucoCEST"
    words = page_id.replace("_", " ").replace("-", " ").split()
    return " ".join(word[:1].upper() + word[1:] for word in words)


def plain_text(text: str) -> str:
    text = re.sub(r"\{\{.*?\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]+\|)?(.*?)\]\]", r"\1", text)
    text = re.sub(r"\*+|/+|''|__", "", text)
    return re.sub(r"\s+", " ", text).strip(" -")


def href_for(page_id: str) -> str:
    return "index.html" if page_id == "start" else f"{page_id}.html"


def find_media(name: str) -> str | None:
    rel = name.strip().lstrip(":").replace(":", "/")
    candidate = MEDIA / rel
    if candidate.is_file():
        return rel.replace("\\", "/")
    wanted = rel.lower()
    for path in MEDIA.rglob("*"):
        if path.is_file() and path.relative_to(MEDIA).as_posix().lower() == wanted:
            return path.relative_to(MEDIA).as_posix()
    return None


class Renderer:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.missing: list[str] = []

    def render_page(self, page_id: str, source: str) -> tuple[str, str, str]:
        codes: list[str] = []

        def stash_code(match: re.Match[str]) -> str:
            lang = match.group(1) or ""
            body = html.escape(match.group(2).strip("\n"))
            klass = f" class=\"code {html.escape(lang, quote=True)}\"" if lang else ' class="code"'
            codes.append(f"<pre{klass}><code>{body}</code></pre>")
            return f"\n\n@@CODE{len(codes) - 1}@@\n\n"

        text = CODE.sub(stash_code, source.replace("\r\n", "\n"))
        blocks: list[tuple[str, str]] = []
        paragraph: list[str] = []
        items: list[tuple[str, str, str]] = []

        def flush_paragraph() -> None:
            if not paragraph:
                return
            chunks: list[str] = []
            buf: list[str] = []
            for line in paragraph:
                stripped = line.rstrip()
                if stripped.endswith("\\\\"):
                    buf.append(stripped[:-2].strip())
                    chunks.append(" ".join(part for part in buf if part))
                    buf = []
                else:
                    buf.append(line.strip())
            if buf:
                chunks.append(" ".join(part for part in buf if part))
            body = "<br>\n".join(self.inline(chunk) for chunk in chunks if chunk)
            if body:
                blocks.append(("p", f"<p>{body}</p>"))
            paragraph.clear()

        def flush_list() -> None:
            if not items:
                return
            blocks.append(("list", self.list_html(items)))
            items.clear()

        for raw_line in text.split("\n"):
            code_match = re.fullmatch(r"@@CODE(\d+)@@", raw_line.strip())
            if code_match:
                flush_paragraph()
                flush_list()
                blocks.append(("code", codes[int(code_match.group(1))]))
                continue
            heading = HEADING.match(raw_line.strip())
            if heading:
                flush_paragraph()
                flush_list()
                level = 7 - len(heading.group(1))
                blocks.append((f"h{level}", heading.group(2).strip().strip("=").strip()))
                continue
            if RULE.match(raw_line):
                flush_paragraph()
                flush_list()
                blocks.append(("hr", "<hr>"))
                continue
            item = LIST_ITEM.match(raw_line)
            if item:
                flush_paragraph()
                items.append(item.groups())
                continue
            if not raw_line.strip():
                flush_paragraph()
                flush_list()
                continue
            flush_list()
            paragraph.append(raw_line)
        flush_paragraph()
        flush_list()

        h1 = next((body for kind, body in blocks if kind == "h1"), "")
        if blocks and blocks[0][0][:1] == "h" and blocks[0][0][1:].isdigit():
            title = plain_text(blocks[0][1])
        elif h1:
            title = plain_text(h1)
        else:
            title = pretty_name(page_id)
        used: dict[str, int] = {}
        rendered: list[str] = []
        toc: list[tuple[int, str, str]] = []
        numbers = HeadingNumbers()
        for kind, body in blocks:
            if kind[:1] == "h" and kind[1:].isdigit():
                level = int(kind[1])
                dashes, clean = heading_marker(body)
                shown = f"{numbers.next(level)} {clean}".strip() if dashes == 1 else clean
                plain = re.sub(r"\{\{.*?\}\}|\[\[.*?\]\]", "", clean)
                plain = re.sub(r"[\[\]{}*/_']", "", plain)
                base = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "section"
                used[base] = used.get(base, 0) + 1
                anchor = base if used[base] == 1 else f"{base}-{used[base]}"
                label = self.inline(shown)
                if level <= 3:
                    toc.append((level, anchor, label))
                rendered.append(f'<h{level} id="{anchor}">{label}</h{level}>')
            else:
                rendered.append(body)
        toc_html = ""
        if len([kind for kind, _ in blocks if kind[:1] == "h" and kind[1:].isdigit()]) >= 3 and toc:
            rows = []
            for level, anchor, label in toc:
                rows.append(f'<li class="toc-{level}"><a href="#{anchor}">{label}</a></li>')
            toc_html = '<div class="toc"><div class="toc-title">Table of Contents</div><ul>' + "".join(rows) + "</ul></div>"
        body_html = "\n".join(rendered)
        has_heading = any(kind[:1] == "h" and kind[1:].isdigit() for kind, _ in blocks)
        if not has_heading:
            anchor = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "page"
            body_html = f'<h1 id="{anchor}">{html.escape(title)}</h1>\n' + body_html
        return title, toc_html, body_html

    def list_html(self, items: list[tuple[str, str, str]]) -> str:
        parts: list[str] = []
        stack: list[tuple[int, str]] = []

        def tag_for(marker: str) -> str:
            return "ol" if marker == "-" else "ul"

        def open_list(level: int, marker: str) -> None:
            tag = tag_for(marker)
            parts.append(f"<{tag}>")
            stack.append((level, tag))

        def close_one() -> None:
            _, tag = stack.pop()
            parts.append(f"</{tag}>")

        for spaces, marker, text in items:
            level = max(0, (len(spaces) - 2) // 2)
            tag = tag_for(marker)
            while stack and (stack[-1][0] > level or (stack[-1][0] == level and stack[-1][1] != tag)):
                close_one()
            if not stack or stack[-1][0] < level:
                open_list(level, marker)
            parts.append(f"<li>{self.inline(text)}</li>")
        while stack:
            close_one()
        return "".join(parts)

    def inline(self, text: str, autolink: bool = True) -> str:
        slots: list[str] = []

        def slot(fragment: str) -> str:
            slots.append(fragment)
            return f"\x00{len(slots) - 1}\x00"

        text = re.sub(r"</?su[bp]>", lambda match: slot(match.group(0).lower()), text, flags=re.I)
        text = re.sub(r"\{\{(.*?)\}\}", lambda match: slot(self.media(match.group(1))), text, flags=re.S)
        text = re.sub(r"\[\[(.*?)\]\]", lambda match: slot(self.link(match.group(1))), text, flags=re.S)
        text = html.escape(text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text, flags=re.S)
        text = re.sub(r"__(.+?)__", r"<u>\1</u>", text, flags=re.S)
        text = re.sub(r"(?<!:)//(.+?)//", r"<em>\1</em>", text, flags=re.S)
        text = re.sub(r"''(.+?)''", r"<code>\1</code>", text, flags=re.S)

        def link_url(match: re.Match[str]) -> str:
            url = match.group(0).rstrip(".,;:)")
            tail = match.group(0)[len(url) :]
            return f'<a class="urlextern" href="{html.escape(url, quote=True)}">{html.escape(url)}</a>{html.escape(tail)}'

        if autolink:
            text = re.sub(r"https?://[^\s<]+", link_url, text, flags=re.I)
        for _ in range(3):
            text = re.sub(r"\x00(\d+)\x00", lambda match: slots[int(match.group(1))], text)
        return text

    def media(self, body: str) -> str:
        body = body.strip()
        if body.lower().startswith("youtube>"):
            rest = body.split(">", 1)[1]
            spec, _, caption = rest.partition("|")
            ident, _, size = spec.partition("?")
            width, height = "640", "360"
            sized = re.fullmatch(r"(\d+)x(\d+)", size.strip())
            if sized:
                width, height = sized.group(1), sized.group(2)
            title = html.escape(caption.strip() or "YouTube video", quote=True)
            src = "https://www.youtube-nocookie.com/embed/" + urllib.parse.quote(ident.strip())
            return (
                f'<span class="video"><iframe width="{width}" height="{height}" src="{src}" '
                f'title="{title}" loading="lazy" allowfullscreen></iframe></span>'
            )
        if body.lower().startswith("contact>"):
            subject = "CEST sources message"
            for part in body.split(">", 1)[1].split("|"):
                if part.startswith("subj="):
                    subject = part[5:]
            query = urllib.parse.quote(subject)
            return f'<a class="mail" href="mailto:{CONTACT}?subject={query}">{CONTACT}</a>'
        spec, _, caption = body.partition("|")
        spec = spec.strip().lstrip(":").replace("&amp;", "&")
        name, _, params = spec.partition("?")
        found = find_media(name)
        label = caption.strip() or Path(name).name
        if not found:
            self.missing.append(name.strip())
            return html.escape(label)
        src = "media/" + found
        if Path(found).suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
            return f'<a href="{html.escape(src, quote=True)}">{html.escape(label)}</a>'
        width = ""
        numbers = re.findall(r"\d+", params)
        if numbers:
            width = f' width="{numbers[0]}"'
        alt = html.escape(caption.strip(), quote=True)
        image = f'<img src="{html.escape(src, quote=True)}" alt="{alt}"{width}>'
        if caption.strip():
            return f'<span class="media">{image}<span class="caption">{html.escape(caption.strip())}</span></span>'
        return image

    def link(self, body: str) -> str:
        target, separator, label = body.partition("|")
        target = target.strip()
        label_source = label if separator else target
        label_html = self.inline(label_source, autolink=False) if separator else ""
        if not separator:
            label_html = html.escape(target)
        lower = target.lower()
        if lower.startswith(("http://", "https://", "ftp://", "mailto:")):
            scheme, _, rest = target.partition(":")
            href = scheme.lower() + ":" + rest
            return f'<a class="urlextern" href="{html.escape(href, quote=True)}">{label_html}</a>'
        if ">" in target and "://" not in target:
            kind, rest = target.split(">", 1)
            kind = kind.lower()
            if kind == "wp":
                href = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(rest.replace(" ", "_"))
            elif kind == "doku":
                href = "https://www.dokuwiki.org/" + rest.lstrip("/")
            else:
                return label_html
            return f'<a class="interwiki" href="{html.escape(href, quote=True)}">{label_html}</a>'
        page, _, fragment = target.partition("#")
        page_id = clean_id(page)
        if page_id in self.pages:
            href = href_for(page_id)
            if fragment:
                anchor = re.sub(r"[^a-z0-9]+", "-", fragment.lower()).strip("-")
                href += f"#{anchor}"
            return f'<a class="wikilink" href="{html.escape(href, quote=True)}">{label_html}</a>'
        self.missing.append(target)
        return label_html


def page_template(title: str, toc: str, body: str, nav: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — CEST sources</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header>
  <a class="brand" href="index.html"><img src="assets/cest-logo.png" alt="CEST sources"></a>
  <span class="tag">Chemical Exchange Saturation Transfer</span>
</header>
<div class="layout">
  <nav>{nav}</nav>
  <main>
    {toc}
    <article>{body}</article>
  </main>
</div>
<footer>
  <div class="affiliation">
    <a class="fau" href="https://www.fau.de/"><img src="assets/fau-logo.svg" alt="FAU — Friedrich-Alexander-Universität Erlangen-Nürnberg"></a>
    <p>
      <a href="https://www.uk-erlangen.de/kontakt/visitenkarte/moritz-zaiss-9472/">Prof. Dr. rer. nat. Moritz Zaiss</a><br>
      Professur für multimodale Bildgebung in der klinischen Forschung<br>
      Neuroradiologisches Institut, Universitätsklinikum Erlangen<br>
      Friedrich-Alexander-Universität Erlangen-Nürnberg
    </p>
  </div>
</footer>
</body>
</html>
"""


STYLE = """
:root {
  --text: #333;
  --link: #2b73b7;
  --wiki: #080;
  --border: #ccc;
  --paper: #fff;
  --site: #fbfaf9;
  --alt: #eee;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--site);
  color: var(--text);
  font: 15px/1.55 "Segoe UI", "Lucida Grande", Verdana, sans-serif;
}
header {
  display: flex;
  align-items: center;
  gap: 1rem;
  background: #19477a;
  color: #fff;
  padding: 0.55rem 1.4rem;
}
.brand img { height: 74px; width: auto; display: block; background: #e7f4fb; }
.tag { color: #d5e2f0; font-size: 0.95rem; }
.layout { display: flex; align-items: flex-start; gap: 1.5rem; max-width: 1200px; margin: 0 auto; padding: 1.2rem; }
nav { width: 320px; flex: none; background: var(--paper); border: 1px solid var(--border); padding: 0.8rem 1rem 0.8rem 0.6rem; }
nav ul { margin: 0; padding-left: 1.35rem; }
nav li { margin: 0.28rem 0; }
nav a { color: var(--wiki); text-decoration: none; }
nav a:hover, nav a.current { text-decoration: underline; }
nav a.current { font-weight: 600; }
main { flex: 1; min-width: 0; background: var(--paper); border: 1px solid var(--border); padding: 1rem 1.4rem 2rem; }
h1, h2, h3, h4, h5 { color: #19477a; line-height: 1.3; font-weight: 600; }
h1 { font-size: 1.7rem; border-bottom: 1px solid var(--border); padding-bottom: 0.25rem; }
h2 { font-size: 1.35rem; }
h3 { font-size: 1.15rem; }
a.wikilink { color: var(--wiki); }
a.urlextern, a.interwiki, a.mail { color: var(--link); }
.toc { float: right; width: 14rem; margin: 0 0 1rem 1rem; background: var(--alt); border: 1px solid var(--border); padding: 0.5rem 0.8rem; font-size: 0.86rem; }
.toc-title { font-weight: 600; margin-bottom: 0.3rem; }
.toc ul { list-style: none; margin: 0; padding: 0; }
.toc a { color: var(--text); text-decoration: none; }
.toc a:hover { text-decoration: underline; }
.toc-3 { padding-left: 0.8rem; }
img { height: auto; vertical-align: middle; margin: 0.25rem 0.45rem 0.25rem 0; }
span.media { display: inline-block; margin: 0.4rem 0.8rem 0.4rem 0; vertical-align: top; }
span.media .caption { display: block; color: #666; font-size: 0.82rem; }
.video { display: block; margin: 0.8rem 0; }
.video iframe { max-width: 100%; border: 0; }
pre.code { background: #f7f9fa; border: 1px solid var(--border); padding: 0.7rem 0.9rem; overflow-x: auto; }
code { font-family: Consolas, "Courier New", monospace; font-size: 0.92em; }
ul, ol { padding-left: 1.3rem; }
hr { border: 0; border-top: 1px solid var(--border); }
footer { background: #fff; border-top: 1px solid var(--border); padding: 1.1rem 1.4rem 1.4rem; }
.affiliation { max-width: 1200px; margin: 0 auto; display: flex; align-items: center; gap: 1.4rem; }
.affiliation img { height: 54px; width: auto; display: block; }
.affiliation p { margin: 0; color: #333; font-size: 0.92rem; line-height: 1.45; }
.affiliation a { color: var(--link); text-decoration: none; }
.affiliation a:hover { text-decoration: underline; }
@media (max-width: 800px) {
  .layout { display: block; }
  nav { width: auto; margin-bottom: 1rem; }
  .toc { float: none; width: auto; }
  .affiliation { display: block; }
  .affiliation img { margin-bottom: 0.7rem; }
}
"""


def main() -> None:
    pages = load_pages()
    renderer = Renderer(pages)
    rendered: dict[str, tuple[str, str, str]] = {}
    for page_id, source in pages.items():
        rendered[page_id] = renderer.render_page(page_id, source)

    nav_ids = ["start"] + sorted((page_id for page_id in pages if page_id != "start"), key=lambda item: rendered[item][0].lower())
    for old in OUT.glob("*.html"):
        old.unlink()
    media_out = OUT / "media"
    if media_out.exists():
        shutil.rmtree(media_out)
    shutil.copytree(MEDIA, media_out)
    (OUT / "style.css").write_text(STYLE, encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    for page_id, (title, toc, body) in rendered.items():
        links = []
        for other in nav_ids:
            current = ' class="current"' if other == page_id else ""
            links.append(f"<li><a{current} href=\"{href_for(other)}\">{html.escape(rendered[other][0])}</a></li>")
        html_page = page_template(title, toc, body, "<ul>\n" + "\n".join(links) + "\n</ul>")
        (OUT / href_for(page_id)).write_text(html_page, encoding="utf-8")

    targets = {href_for(page_id) for page_id in pages}
    broken = []
    for page in OUT.glob("*.html"):
        for match in re.findall(r'href="([^"]+)"', page.read_text(encoding="utf-8")):
            if match.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = match.split("#", 1)[0]
            if path and path not in targets and not (OUT / path).exists():
                broken.append(f"{page.name} -> {match}")
    print(f"pages: {len(pages)}")
    print(f"media files: {sum(1 for item in media_out.rglob('*') if item.is_file())}")
    if renderer.missing:
        print("unresolved: " + ", ".join(sorted(set(renderer.missing))))
    if broken:
        print("broken links:")
        print("\n".join(broken))
    else:
        print("internal links ok")


if __name__ == "__main__":
    main()
