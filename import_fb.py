#!/usr/bin/env python3
"""Convert a Facebook 'Download your information' posts export into content/*.md

Usage:
    python3 import_fb.py path/to/your_posts__check_ins__photos_and_videos_1.json
    python3 import_fb.py path/to/posts_folder/

Existing files in content/ are never overwritten, so re-running after adding new
posts only brings in what is missing.
"""

import glob
import json
import os
import re
import sys
import unicodedata
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(ROOT, "content")

# Facebook exports JSON with UTF-8 bytes escaped as Latin-1 code points, which
# turns Thai (and every other non-ASCII script) into mojibake. This undoes it.
def fix_encoding(text):
    if not isinstance(text, str):
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def slugify(text, fallback):
    """ASCII slug for the URL. Non-Latin titles fall back to a dated slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:60].strip("-") or fallback


def extract_posts(payload):
    """Facebook has shipped several shapes for this file; accept the common ones."""
    if isinstance(payload, dict):
        for key in ("status_updates", "posts", "your_posts", "status_updates_v2"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        return []
    return payload if isinstance(payload, list) else []


def post_text(entry):
    """Pull the body out of an entry, checking both places FB puts it."""
    chunks = []
    for item in entry.get("data") or []:
        if isinstance(item, dict) and item.get("post"):
            chunks.append(fix_encoding(item["post"]))
    for att in entry.get("attachments") or []:
        for item in att.get("data") or []:
            if isinstance(item, dict):
                media = item.get("media") or {}
                desc = media.get("description") or item.get("text")
                if desc:
                    chunks.append(fix_encoding(desc))
    return "\n\n".join(c.strip() for c in chunks if c and c.strip())


def derive_title(body, stamp):
    """Return (title, body).

    A short opening line is treated as the post's heading and lifted out of the
    body, so it does not render twice. A long opening line is a real paragraph:
    it stays put and the title is a trimmed copy of it.
    """
    parts = body.strip().split("\n", 1)
    first = re.sub(r"^#+\s*", "", parts[0].strip())
    rest = parts[1] if len(parts) > 1 else ""

    if not first:
        return "Post from " + stamp.strftime("%d %B %Y"), body
    if len(first) <= 80:
        return first, rest.strip()
    return first[:80].rsplit(" ", 1)[0] + "…", body


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)

    target = sys.argv[1]
    if os.path.isdir(target):
        files = sorted(glob.glob(os.path.join(target, "*.json")))
    else:
        files = [target]

    if not files:
        sys.exit("No .json files found at: %s" % target)

    os.makedirs(CONTENT, exist_ok=True)

    entries = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            print("  skipped %s (%s)" % (os.path.basename(path), exc))
            continue
        entries.extend(extract_posts(payload))

    print("Found %d entries across %d file(s)." % (len(entries), len(files)))

    written = skipped_empty = skipped_existing = 0
    seen = set()

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue

        body = post_text(entry)
        if len(body.strip()) < 40:          # link-shares, check-ins, one-liners
            skipped_empty += 1
            continue

        ts = entry.get("timestamp") or 0
        stamp = datetime.fromtimestamp(ts) if ts else datetime.now()
        day = stamp.strftime("%Y-%m-%d")

        title = fix_encoding(entry.get("title") or "").strip()
        if title:
            body = body.strip()
        else:
            title, body = derive_title(body, stamp)
        title = re.sub(r"\s+", " ", title).strip()

        slug = slugify(title, "post-%d" % (index + 1))
        name = "%s-%s" % (day, slug)
        while name in seen:                 # two posts, same day, same slug
            index += 1
            name = "%s-%s-%d" % (day, slug, index)
        seen.add(name)

        path = os.path.join(CONTENT, name + ".md")
        if os.path.exists(path):
            skipped_existing += 1
            continue

        safe_title = title.replace('"', "'")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('---\ntitle: "%s"\ndate: %s\n---\n\n%s\n' % (safe_title, day, body.strip()))
        written += 1
        print("  + %s.md" % name)

    print("\nImported %d, already present %d, skipped %d (too short / no text)."
          % (written, skipped_existing, skipped_empty))
    if written:
        print("\nReview the files in content/, then run:  python3 build.py")


if __name__ == "__main__":
    main()
