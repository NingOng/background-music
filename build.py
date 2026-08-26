#!/usr/bin/env python3
"""Build the static article site: content/*.md  ->  docs/*.html

Usage:  python3 build.py
"""

import html
import json
import os
import re
import shutil
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(ROOT, "content")
DOCS = os.path.join(ROOT, "docs")
ASSETS = os.path.join(ROOT, "assets")
CONFIG = os.path.join(ROOT, "site.json")


# --------------------------------------------------------------------------
# front matter
# --------------------------------------------------------------------------

def parse_front_matter(text):
    """Split a '---' delimited YAML-ish header off the top of a document."""
    meta = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end == -1:
        return meta, text
    header = text[3:end]
    body = text[end + 4:].lstrip("\n")
    for line in header.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    return meta, body


# --------------------------------------------------------------------------
# markdown -> html  (small subset, no dependencies)
# --------------------------------------------------------------------------

CODE_TOKEN = "\x00CODE%d\x00"
BR_TOKEN = "\x00BR\x00"
BARE_URL = re.compile(r'(?<!["\'=>])\b(https?://[^\s<>"\']+)')


def autolink(match):
    """Link a bare URL, leaving trailing sentence punctuation outside the link.

    Wikipedia titles such as Blackbird_(Beatles_song) end in a parenthesis that
    belongs to the URL, so a closing paren is only trimmed when it is unbalanced.
    """
    url = match.group(1)
    tail = ""
    while url and url[-1] in ".,;:!?":
        tail = url[-1] + tail
        url = url[:-1]
    while url.endswith(")") and url.count(")") > url.count("("):
        tail = ")" + tail
        url = url[:-1]
    return '<a href="%s">%s</a>%s' % (url, url, tail)


def inline(text):
    """Inline formatting. Input is raw text; output is escaped HTML."""
    spans = []

    def stash(match):
        spans.append(match.group(1))
        return CODE_TOKEN % (len(spans) - 1)

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text, quote=False)

    # images before links: ![alt](src)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)\)",
        lambda m: '<img src="%s" alt="%s">' % (m.group(2), m.group(1)),
        text,
    )
    # links: [text](url)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: '<a href="%s">%s</a>' % (m.group(2), m.group(1)),
        text,
    )
    # bare urls (common in pasted Facebook posts)
    text = BARE_URL.sub(autolink, text)

    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)

    for i, code in enumerate(spans):
        text = text.replace(CODE_TOKEN % i, "<code>%s</code>" % html.escape(code, quote=False))
    return text


def markdown(text):
    """Convert a subset of Markdown to HTML."""
    out = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # fenced code
        if stripped.startswith("```"):
            i += 1
            block = []
            while i < n and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(block), quote=False))
            continue

        # horizontal rule
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            out.append("<hr>")
            i += 1
            continue

        # heading
        m = re.match(r"(#{1,6})\s+(.*)", stripped)
        if m:
            level = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (level, inline(m.group(2).strip()), level))
            i += 1
            continue

        # blockquote
        if stripped.startswith(">"):
            block = []
            while i < n and lines[i].strip().startswith(">"):
                block.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote><p>%s</p></blockquote>" % inline(" ".join(block)))
            continue

        # lists
        bullet = re.match(r"[-*+]\s+(.*)", stripped)
        number = re.match(r"\d+[.)]\s+(.*)", stripped)
        if bullet or number:
            tag = "ul" if bullet else "ol"
            pattern = r"[-*+]\s+(.*)" if bullet else r"\d+[.)]\s+(.*)"
            items = []
            while i < n and lines[i].strip():
                m = re.match(pattern, lines[i].strip())
                if not m:
                    break
                items.append("<li>%s</li>" % inline(m.group(1).strip()))
                i += 1
            out.append("<%s>%s</%s>" % (tag, "".join(items), tag))
            continue

        # paragraph: consume until a blank line or the start of another block.
        # The first line is always consumed, which guarantees forward progress
        # for text that looks like a block marker but is not one (a "#hashtag",
        # say, which the heading branch above deliberately did not match).
        block = []
        while i < n and lines[i].strip():
            s = lines[i].strip()
            if block and (re.match(r"#{1,6}\s", s)
                          or s.startswith((">", "```"))
                          or re.match(r"([-*+]|\d+[.)])\s+", s)):
                break
            block.append(s)
            i += 1
        if block:
            # a single newline inside a paragraph is a real line break,
            # matching how people write posts on Facebook
            out.append("<p>%s</p>" % inline(BR_TOKEN.join(block)).replace(BR_TOKEN, "<br>\n"))

    return "\n".join(out)


# --------------------------------------------------------------------------
# page template
# --------------------------------------------------------------------------

def excerpt(body, limit=180):
    """Plain-text preview of an article for the index page."""
    text = re.sub(r"```.*?```", " ", body, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*`_-]", " ", text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def pretty_date(value):
    try:
        y, m, d = [int(p) for p in str(value).split("-")[:3]]
        return date(y, m, d).strftime("%d %B %Y").lstrip("0")
    except Exception:
        return str(value)


def page(cfg, title, body, is_home=False, article_date=None):
    robots = '\n  <meta name="robots" content="noindex, nofollow">' if cfg.get("noindex") else ""
    site_title = html.escape(cfg.get("title", "Articles"), quote=True)
    head_title = site_title if is_home else "%s — %s" % (html.escape(title), site_title)

    if is_home:
        header = (
            '<header class="site">\n'
            '  <h1>%s</h1>\n'
            '  <p class="tagline">%s</p>\n'
            '</header>' % (site_title, html.escape(cfg.get("tagline", ""), quote=False))
        )
    else:
        meta = '<p class="date">%s</p>' % pretty_date(article_date) if article_date else ""
        header = (
            '<header class="article">\n'
            '  <p class="back"><a href="./index.html">← %s</a></p>\n'
            '  <h1>%s</h1>\n'
            '  %s\n'
            '</header>' % (site_title, html.escape(title), meta)
        )

    return """<!doctype html>
<html lang="%s">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">%s
  <title>%s</title>
  <link rel="stylesheet" href="./style.css">
</head>
<body>
  <main>
%s
%s
  </main>
  <footer>
    <p>&copy; %s %s</p>
  </footer>
</body>
</html>
""" % (cfg.get("lang", "en"), robots, head_title, header, body,
       date.today().year, html.escape(cfg.get("author", ""), quote=False))


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def main():
    with open(CONFIG, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    if not os.path.isdir(CONTENT):
        sys.exit("No content/ directory. Create it and add .md files.")

    articles = []
    for name in sorted(os.listdir(CONTENT)):
        if not name.endswith(".md") or name.startswith("."):
            continue
        with open(os.path.join(CONTENT, name), "r", encoding="utf-8") as fh:
            raw = fh.read()
        meta, body = parse_front_matter(raw)
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", name[:-3])
        articles.append({
            "slug": slug,
            "title": meta.get("title") or slug.replace("-", " ").title(),
            "date": meta.get("date", ""),
            "body": body,
        })

    articles.sort(key=lambda a: (a["date"], a["title"]), reverse=True)

    # clean output dir but keep the git metadata that may live alongside it
    if os.path.isdir(DOCS):
        for name in os.listdir(DOCS):
            if name == ".git":
                continue
            path = os.path.join(DOCS, name)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    else:
        os.makedirs(DOCS)

    for art in articles:
        out = page(cfg, art["title"], markdown(art["body"]),
                   article_date=art["date"] or None)
        with open(os.path.join(DOCS, art["slug"] + ".html"), "w", encoding="utf-8") as fh:
            fh.write(out)

    # index
    if articles:
        items = []
        for art in articles:
            meta = ('<p class="date">%s</p>' % pretty_date(art["date"])) if art["date"] else ""
            items.append(
                '    <li>\n'
                '      <a class="title" href="./%s.html">%s</a>\n'
                '      %s\n'
                '      <p class="excerpt">%s</p>\n'
                '    </li>' % (art["slug"], html.escape(art["title"]), meta,
                               html.escape(excerpt(art["body"])))
            )
        listing = '<ul class="articles">\n%s\n  </ul>' % "\n".join(items)
    else:
        listing = '<p class="empty">No articles yet. Add a .md file to content/ and run the build again.</p>'

    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(cfg, cfg.get("title", "Articles"), listing, is_home=True))

    shutil.copyfile(os.path.join(ASSETS, "style.css"), os.path.join(DOCS, "style.css"))

    # tells GitHub Pages to serve the files as-is
    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    with open(os.path.join(DOCS, "robots.txt"), "w", encoding="utf-8") as fh:
        fh.write("User-agent: *\nDisallow: /\n" if cfg.get("noindex")
                 else "User-agent: *\nAllow: /\n")

    print("Built %d article(s) into docs/" % len(articles))
    for art in articles:
        print("  - %s.html  %s" % (art["slug"], art["title"]))
    if cfg.get("noindex"):
        print("\nSearch engines: BLOCKED (noindex). Set \"noindex\": false in site.json to allow.")
    else:
        print("\nSearch engines: ALLOWED.")


if __name__ == "__main__":
    main()
