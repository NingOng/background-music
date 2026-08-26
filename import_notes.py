#!/usr/bin/env python3
"""Import a folder of Apple Notes into content/*.md

Usage:
    python3 import_notes.py                      # folder "Background Music"
    python3 import_notes.py "Some Other Folder"
    python3 import_notes.py --list               # show every folder and its note count

Notes are stored as gzipped protobuf inside a SQLite database. This reads a
copy of that database, never the live one. Existing files in content/ are left
alone, so re-running only brings in notes you have not imported yet.
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
# helpers
# --------------------------------------------------------------------------

def slugify(text, fallback):
    """ASCII slug for the URL; Thai and other non-Latin titles use the fallback."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:60].strip("-") or fallback


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
    args = [a for a in sys.argv[1:]]
    con, tmp = open_db()

    try:
        if "--list" in args:
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

        print("Folder %r: %d note(s).\n" % (folder, len(rows)))
        os.makedirs(CONTENT, exist_ok=True)

        written = empty = existing = 0
        short_ones = []
        seen = set()

        for index, (pk, title, blob, stamp) in enumerate(rows):
            title = (title or "").strip()
            body = note_text(blob) if blob else None
            if body is None:
                print("  ! %-45s could not be decoded" % title[:45])
                continue

            # Notes repeats the title as the first line of the body.
            lines = body.split("\n", 1)
            if lines[0].strip() == title:
                body = lines[1] if len(lines) > 1 else ""
            # U+FFFC marks where an image sat; the image itself lives outside
            # the database, so drop the marker rather than render a tofu box.
            body = body.replace("\ufffc", "")
            body = re.sub(r"[ \t]+\n", "\n", body)
            body = re.sub(r"\n{3,}", "\n\n", body)
            body = body.strip()

            if not body:
                empty += 1
                print("  - %-45s title only, skipped" % title[:45])
                continue

            when = datetime.fromtimestamp((stamp or 0) + APPLE_EPOCH)
            day = when.strftime("%Y-%m-%d")

            slug = slugify(title, "note-%d" % pk)
            name = "%s-%s" % (day, slug)
            while name in seen:
                name = "%s-%s-%d" % (day, slug, pk)
            seen.add(name)

            path = os.path.join(CONTENT, name + ".md")
            if os.path.exists(path):
                existing += 1
                continue

            with open(path, "w", encoding="utf-8") as fh:
                fh.write('---\ntitle: "%s"\ndate: %s\n---\n\n%s\n'
                         % (title.replace('"', "'"), day, body))
            written += 1

            flag = "  (short)" if len(body) < SHORT else ""
            if flag:
                short_ones.append((name, title, len(body)))
            print("  + %-45s %5d chars%s" % (title[:45], len(body), flag))

        print("\nImported %d, already present %d, title-only %d."
              % (written, existing, empty))

        if short_ones:
            print("\nThese are short enough that they may be jottings rather than\n"
                  "articles. Delete any you do not want to publish:\n")
            for name, title, length in short_ones:
                print("  content/%s.md   (%s, %d chars)" % (name, title, length))

        print("\nNext:  python3 build.py")

    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
