#!/usr/bin/env python3
"""Build the bilingual article site.

    content/     Thai articles     ->  docs/
    content-en/  English articles  ->  docs/en/

Usage:  python3 build.py

English articles are matched to Thai ones by filename. An article with no
translation simply does not appear in the English edition.
"""

import html
import json
import os
import re
import shutil
import sys
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
ASSETS = os.path.join(ROOT, "assets")
CONFIG = os.path.join(ROOT, "site.json")

EDITIONS = [
    # key, source dir,  output dir,  html lang, label
    ("th", "content",    "",   "th", "TH"),
    ("en", "content-en", "en", "en", "EN"),
]


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

    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)",
                  lambda m: '<img src="%s" alt="%s">' % (m.group(2), m.group(1)), text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                  lambda m: '<a href="%s">%s</a>' % (m.group(2), m.group(1)), text)
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
    i, n = 0, len(text.replace("\r\n", "\n").split("\n"))

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            i += 1
            block = []
            while i < n and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>%s</code></pre>"
                       % html.escape("\n".join(block), quote=False))
            continue

        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            out.append("<hr>")
            i += 1
            continue

        m = re.match(r"(#{1,6})\s+(.*)", stripped)
        if m:
            level = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (level, inline(m.group(2).strip()), level))
            i += 1
            continue

        if stripped.startswith(">"):
            block = []
            while i < n and lines[i].strip().startswith(">"):
                block.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote><p>%s</p></blockquote>" % inline(" ".join(block)))
            continue

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

        # paragraph: the first line is always consumed, which guarantees forward
        # progress for text that looks like a block marker but is not one (a
        # "#hashtag", say, which the heading branch above deliberately skipped).
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


def lang_switch(current, links):
    """The TH | EN control. `links` maps edition key -> href (None if missing)."""
    parts = []
    for key, _src, _out, _lang, label in EDITIONS:
        href = links.get(key)
        if key == current:
            parts.append('<span class="on">%s</span>' % label)
        elif href:
            parts.append('<a href="%s">%s</a>' % (href, label))
        else:
            parts.append('<span class="off" title="not available">%s</span>' % label)
    return '<nav class="lang">%s</nav>' % "".join(parts)


def page(cfg, edition, title, body, is_home=False, article_date=None,
         description="", filename="index.html", category="", links=None):
    key, _src, outdir, htmllang, _label = edition
    robots = ('\n  <meta name="robots" content="noindex, nofollow">'
              if cfg.get("noindex") else "")
    site_title = html.escape(cfg.get("title", "Articles"), quote=True)
    head_title = site_title if is_home else "%s — %s" % (html.escape(title), site_title)

    switch = lang_switch(key, links or {})

    if is_home:
        tagline = cfg.get("tagline", "").strip()
        header = ('<header class="site">\n'
                  '  %s\n'
                  '  <h1>%s</h1>%s\n'
                  '</header>'
                  % (switch, site_title,
                     ('\n  <p class="tagline">%s</p>' % html.escape(tagline, quote=False))
                     if tagline else ""))
    else:
        meta = '<p class="date">%s</p>' % pretty_date(article_date) if article_date else ""
        kicker = ('<p class="kicker">%s</p>\n  ' % html.escape(category)) if category else ""
        back = cfg.get("back_label", {}).get(key, site_title)
        header = ('<header class="article">\n'
                  '  <div class="topbar">\n'
                  '    <p class="back"><a href="./index.html">← %s</a></p>\n'
                  '    %s\n'
                  '  </div>\n'
                  '  %s<h1>%s</h1>\n'
                  '  %s\n'
                  '</header>'
                  % (html.escape(back), switch, kicker, html.escape(title), meta))

    # Search engines and link previews (Facebook, LINE, Messenger) read these.
    desc = html.escape(description or cfg.get("tagline", ""), quote=True)
    base = cfg.get("url", "").rstrip("/")
    prefix = "%s/%s" % (base, outdir) if outdir else base
    canonical = "%s/%s" % (prefix, filename) if base else ""

    extra = ""
    if desc:
        extra += '\n  <meta name="description" content="%s">' % desc
    if canonical:
        extra += '\n  <link rel="canonical" href="%s">' % canonical
        extra += '\n  <meta property="og:url" content="%s">' % canonical
        # tell search engines the two editions are the same page
        for okey, _s, oout, olang, _l in EDITIONS:
            if (links or {}).get(okey) or okey == key:
                opre = "%s/%s" % (base, oout) if oout else base
                extra += ('\n  <link rel="alternate" hreflang="%s" href="%s/%s">'
                          % (olang, opre, filename))
    extra += '\n  <meta property="og:title" content="%s">' % html.escape(
        title if not is_home else cfg.get("title", ""), quote=True)
    if desc:
        extra += '\n  <meta property="og:description" content="%s">' % desc
    extra += '\n  <meta property="og:type" content="%s">' % (
        "website" if is_home else "article")
    extra += '\n  <meta property="og:site_name" content="%s">' % html.escape(
        cfg.get("title", ""), quote=True)
    extra += '\n  <meta name="twitter:card" content="summary">'

    return """<!doctype html>
<html lang="%s">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">%s%s
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
""" % (htmllang, robots, extra, head_title, header, body,
       date.today().year, html.escape(cfg.get("author", ""), quote=False))


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def load_articles(directory, cfg, key):
    """Read one edition's articles, newest first."""
    path = os.path.join(ROOT, directory)
    if not os.path.isdir(path):
        return []
    cats = cfg.get("categories", {})
    articles = []
    for name in sorted(os.listdir(path)):
        if not name.endswith(".md") or name.startswith("."):
            continue
        with open(os.path.join(path, name), "r", encoding="utf-8") as fh:
            meta, body = parse_front_matter(fh.read())
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", name[:-3])
        category = meta.get("category", "")
        if key != "th" and category:
            category = cats.get(category, {}).get(key, category)
        articles.append({
            "file": name,
            "slug": slug,
            "title": meta.get("title") or slug.replace("-", " ").title(),
            "date": meta.get("date", ""),
            "category": category,
            "body": body,
        })
    articles.sort(key=lambda a: (a["date"], a["title"]), reverse=True)
    return articles


def main():
    with open(CONFIG, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    editions = {}
    for ed in EDITIONS:
        editions[ed[0]] = load_articles(ed[1], cfg, ed[0])

    if not editions["th"]:
        sys.exit("No articles in content/.")

    # which slugs exist in which edition, so the switch can link or grey out
    have = dict((k, set(a["slug"] for a in v)) for k, v in editions.items())

    if os.path.isdir(DOCS):
        for name in os.listdir(DOCS):
            if name == ".git":
                continue
            p = os.path.join(DOCS, name)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    else:
        os.makedirs(DOCS)

    built = {}
    for ed in EDITIONS:
        key, src, outdir, _lang, _label = ed
        articles = editions[key]
        if not articles:
            print("%s: no articles in %s/ — edition skipped" % (key.upper(), src))
            built[key] = 0
            continue

        target = os.path.join(DOCS, outdir) if outdir else DOCS
        if not os.path.isdir(target):
            os.makedirs(target)

        for art in articles:
            links = {}
            for okey, _s, oout, _l, _lb in EDITIONS:
                if okey == key or art["slug"] not in have.get(okey, ()):
                    continue
                if oout and not outdir:
                    links[okey] = "./%s/%s.html" % (oout, art["slug"])
                elif outdir and not oout:
                    links[okey] = "../%s.html" % art["slug"]
                else:
                    links[okey] = "./%s.html" % art["slug"]
            out = page(cfg, ed, art["title"], markdown(art["body"]),
                       article_date=art["date"] or None,
                       description=excerpt(art["body"], 155),
                       filename=art["slug"] + ".html",
                       category=art["category"], links=links)
            with open(os.path.join(target, art["slug"] + ".html"), "w",
                      encoding="utf-8") as fh:
                fh.write(out)

        items = []
        for art in articles:
            bits = []
            if art["category"]:
                bits.append('<span class="cat">%s</span>' % html.escape(art["category"]))
            if art["date"]:
                bits.append(pretty_date(art["date"]))
            meta = ('<p class="date">%s</p>' % " · ".join(bits)) if bits else ""
            items.append('    <li>\n'
                         '      <a class="title" href="./%s.html">%s</a>\n'
                         '      %s\n'
                         '      <p class="excerpt">%s</p>\n'
                         '    </li>'
                         % (art["slug"], html.escape(art["title"]), meta,
                            html.escape(excerpt(art["body"]))))
        listing = '<ul class="articles">\n%s\n  </ul>' % "\n".join(items)

        home_links = {}
        for okey, _s, oout, _l, _lb in EDITIONS:
            if okey == key or not editions.get(okey):
                continue
            if oout and not outdir:
                home_links[okey] = "./%s/index.html" % oout
            elif outdir and not oout:
                home_links[okey] = "../index.html"
            else:
                home_links[okey] = "./index.html"

        with open(os.path.join(target, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(page(cfg, ed, cfg.get("title", "Articles"), listing,
                          is_home=True, filename="index.html", links=home_links))

        shutil.copyfile(os.path.join(ASSETS, "style.css"),
                        os.path.join(target, "style.css"))
        built[key] = len(articles)

    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    base = cfg.get("url", "").rstrip("/")
    if cfg.get("noindex"):
        robots_txt = "User-agent: *\nDisallow: /\n"
    else:
        robots_txt = "User-agent: *\nAllow: /\n"
        if base:
            robots_txt += "Sitemap: %s/sitemap.xml\n" % base
    with open(os.path.join(DOCS, "robots.txt"), "w", encoding="utf-8") as fh:
        fh.write(robots_txt)

    if base and not cfg.get("noindex"):
        urls = []
        for ed in EDITIONS:
            key, _s, outdir, _l, _lb = ed
            if not editions.get(key):
                continue
            pre = "%s/%s" % (base, outdir) if outdir else base
            urls.append("<url><loc>%s/</loc></url>" % pre)
            for art in editions[key]:
                when = ("<lastmod>%s</lastmod>" % art["date"]) if art["date"] else ""
                urls.append("<url><loc>%s/%s.html</loc>%s</url>"
                            % (pre, art["slug"], when))
        with open(os.path.join(DOCS, "sitemap.xml"), "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                     '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                     + "\n".join("  " + u for u in urls) + "\n</urlset>\n")

    print("Built:  TH %d article(s)   EN %d article(s)"
          % (built.get("th", 0), built.get("en", 0)))
    missing = have["th"] - have.get("en", set())
    if missing:
        print("\nNo English translation yet for %d article(s):" % len(missing))
        for slug in sorted(missing):
            print("  - %s" % slug)
    print("\nSearch engines: %s"
          % ("BLOCKED (noindex)" if cfg.get("noindex") else "ALLOWED"))


if __name__ == "__main__":
    main()
