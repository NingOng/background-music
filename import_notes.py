#!/usr/bin/env python3
"""Import a folder of Apple Notes into content/*.md

Usage:
    python3 import_notes.py                  # add notes that are not in content/ yet
    python3 import_notes.py --update         # refresh articles from their notes
    python3 import_notes.py --list           # show every folder and its note count
    python3 import_notes.py "Other Folder"   # a folder other than Background Music

Two deliberately separate modes:

  default    Only creates files that do not exist. Never touches an article you
             have already edited by hand.

  --update   Only rewrites files that already exist, pulling in edits made in
             Notes. Never creates anything, so articles you deleted on purpose
             stay deleted. The "category:" line is preserved.

Articles are matched to their note by the id recorded in the front matter, so
renaming a note does not create a duplicate.

Notes are stored as gzipped protobuf inside a SQLite database. This reads a
copy of that database, never the live one.
"""

import gzip
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
import zlib
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(ROOT, "content")
NOTES_DB = os.path.expanduser(
    "~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite")

# Core Data stores timestamps as seconds since 2001-01-01.
APPLE_EPOCH = 978307200

# Notes shorter than this are flagged as probably being jottings, not articles.
SHORT = 300


# --------------------------------------------------------------------------
# protobuf: just enough to walk to the note text
# --------------------------------------------------------------------------

def _varint(buf, pos):
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _field(buf, want):
    """Return the bytes of the first length-delimited field numbered `want`."""
    pos, end = 0, len(buf)
    while pos < end:
        try:
            key, pos = _varint(buf, pos)
        except IndexError:
            return None
        fnum, wtype = key >> 3, key & 7
        if wtype == 2:
            ln, pos = _varint(buf, pos)
            if fnum == want:
                return buf[pos:pos + ln]
            pos += ln
        elif wtype == 0:
            _, pos = _varint(buf, pos)
        elif wtype == 5:
            pos += 4
        elif wtype == 1:
            pos += 8
        else:
            return None
    return None


def _decompress(blob):
    try:
        return gzip.GzipFile(fileobj=io.BytesIO(blob)).read()
    except Exception:
        pass
    for wbits in (-15, 15, 47):
        try:
            return zlib.decompress(blob, wbits)
        except Exception:
            continue
    return None


def note_text(blob):
    """NoteStoreProto.document(2) -> Note(3) -> note_text(2)"""
    raw = _decompress(bytes(blob))
    if raw is None:
        return None
    doc = _field(raw, 2)
    if doc is None:
        return None
    note = _field(doc, 3)
    if note is None:
        return None
    text = _field(note, 2)
    if text is None:
        return None
    return text.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# articles already in content/
# --------------------------------------------------------------------------

def read_front_matter(path):
    """Return the front-matter keys of an article already in content/."""
    meta = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (IOError, OSError):
        return meta
    if not text.startswith("---"):
        return meta
    end = text.find("\n---", 3)
    if end == -1:
        return meta
    for line in text[3:end].splitlines():
        line = line.strip()
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    return meta


def index_existing():
    """Map note id -> path and title -> path for what is already imported."""
    by_id, by_title = {}, {}
    if not os.path.isdir(CONTENT):
        return by_id, by_title
    for name in sorted(os.listdir(CONTENT)):
        if not name.endswith(".md") or name.startswith("."):
            continue
        path = os.path.join(CONTENT, name)
        meta = read_front_matter(path)
        if meta.get("note"):
            by_id[str(meta["note"])] = path
        if meta.get("title"):
            by_title[meta["title"].strip()] = path
    return by_id, by_title


def write_article(path, pk, title, day, body, category=""):
    """Write one article, keeping any category already assigned to it."""
    head = ["---", 'title: "%s"' % title.replace('"', "'"), "date: %s" % day]
    if category:
        head.append("category: %s" % category)
    head += ["note: %s" % pk, "---", "", body, ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(head))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def slugify(text, fallback):
    """ASCII slug for the URL; Thai and other non-Latin titles use the fallback."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:60].strip("-") or fallback


def clean(body):
    """Drop image markers and collapse the whitespace Notes leaves behind."""
    body = body.replace("￼", "")
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def open_db():
    """Work on a copy: the live database may be mid-write by the Notes app."""
    if not os.path.exists(NOTES_DB):
        sys.exit("Notes database not found at:\n  %s" % NOTES_DB)
    tmp = tempfile.mkdtemp(prefix="notes-")
    copy = os.path.join(tmp, "NoteStore.sqlite")
    try:
        shutil.copyfile(NOTES_DB, copy)
    except (IOError, OSError) as exc:
        sys.exit("Could not read the Notes database (%s).\n"
                 "Grant your terminal Full Disk Access in System Settings -> "
                 "Privacy & Security." % exc)
    return sqlite3.connect(copy), tmp


def list_folders(con):
    rows = con.execute("""
        SELECT f.ZTITLE2, COUNT(n.Z_PK)
        FROM ZICCLOUDSYNCINGOBJECT f
        LEFT JOIN ZICCLOUDSYNCINGOBJECT n
               ON n.ZFOLDER = f.Z_PK AND n.ZTITLE1 IS NOT NULL
        WHERE f.ZTITLE2 IS NOT NULL
        GROUP BY f.Z_PK ORDER BY f.ZTITLE2""").fetchall()
    print("Folders in Apple Notes:\n")
    for title, count in rows:
        print("  %-40s %3d note(s)" % (title, count))


# --------------------------------------------------------------------------

def main():
    args = list(sys.argv[1:])
    update = "--update" in args
    args = [a for a in args if not a.startswith("--")]

    con, tmp = open_db()
    try:
        if "--list" in sys.argv[1:]:
            list_folders(con)
            return

        folder = args[0] if args else "Background Music"

        row = con.execute(
            "SELECT Z_PK FROM ZICCLOUDSYNCINGOBJECT WHERE ZTITLE2 = ?",
            (folder,)).fetchone()
        if not row:
            print("No folder named %r. Available folders:\n" % folder)
            list_folders(con)
            sys.exit(1)

        rows = con.execute("""
            SELECT n.Z_PK, n.ZTITLE1, d.ZDATA,
                   COALESCE(n.ZCREATIONDATE3, n.ZMODIFICATIONDATE1)
            FROM ZICCLOUDSYNCINGOBJECT n
            JOIN ZICNOTEDATA d ON d.ZNOTE = n.Z_PK
            WHERE n.ZFOLDER = ? AND n.ZTITLE1 IS NOT NULL
            ORDER BY COALESCE(n.ZCREATIONDATE3, n.ZMODIFICATIONDATE1)""",
            (row[0],)).fetchall()

        print("Folder %r: %d note(s).  Mode: %s\n"
              % (folder, len(rows), "update existing" if update else "add new"))
        os.makedirs(CONTENT, exist_ok=True)

        by_id, by_title = index_existing()
        created = changed = same = skipped = empty = 0
        new_titles = []
        seen = set()

        for pk, title, blob, stamp in rows:
            pk = str(pk)
            title = (title or "").strip()
            body = note_text(blob) if blob else None
            if body is None:
                print("  ! %-45s could not be decoded" % title[:45])
                continue

            # Notes repeats the title as the first line of the body.
            lines = body.split("\n", 1)
            if lines[0].strip() == title:
                body = lines[1] if len(lines) > 1 else ""
            body = clean(body)

            if not body:
                empty += 1
                continue

            when = datetime.fromtimestamp((stamp or 0) + APPLE_EPOCH)
            day = when.strftime("%Y-%m-%d")

            path = by_id.get(pk) or by_title.get(title)

            if path and os.path.exists(path):
                if not update:
                    skipped += 1
                    continue
                meta = read_front_matter(path)
                before = open(path, "r", encoding="utf-8").read()
                write_article(path, pk, title, meta.get("date", day), body,
                              meta.get("category", ""))
                after = open(path, "r", encoding="utf-8").read()
                if before == after:
                    same += 1
                else:
                    changed += 1
                    print("  ~ %-45s updated" % title[:45])
                continue

            # no file for this note
            if update:
                new_titles.append(title)
                continue

            slug = slugify(title, "note-%s" % pk)
            name = "%s-%s" % (day, slug)
            while name in seen:
                name = "%s-%s-%s" % (day, slug, pk)
            seen.add(name)
            write_article(os.path.join(CONTENT, name + ".md"), pk, title, day, body)
            created += 1
            print("  + %-45s %5d chars%s"
                  % (title[:45], len(body), "  (short)" if len(body) < SHORT else ""))

        if update:
            print("\nUpdated %d, unchanged %d, title-only %d." % (changed, same, empty))
            if new_titles:
                print("\n%d note(s) in the folder have no article here:" % len(new_titles))
                for t in new_titles:
                    print("  - %s" % t)
                print("\nThese were either deleted on purpose or are new. To add them,\n"
                      "run without --update.")
        else:
            print("\nCreated %d, already present %d, title-only %d."
                  % (created, skipped, empty))

        print("\nNext:  python3 build.py")

    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
