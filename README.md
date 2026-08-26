# Background Music

A small static site for publishing articles. No server, no database, no build
tools to install — just Python 3, which macOS already has.

## Publishing a change, start to finish

```sh
python3 build.py          # regenerate docs/ from content/
git add -A
git commit -m "Add new article"
git push
```

GitHub Pages redeploys within about a minute.

## Adding an article by hand

Create `content/YYYY-MM-DD-short-title.md`:

```markdown
---
title: "The title as it should appear"
date: 2026-08-26
category: เพลง
---

Your text here.
```

The date orders the homepage — newest first. `category` is the label shown
above the title and beside the date on the homepage; the four in use are
`เพลง`, `ศิลปิน`, `อัลบั้ม`, and `Story`, but any text works. Leave the line
out and no label is shown. Then run `python3 build.py`.

`import_notes.py --update` preserves the category line, so refreshing an
article from Notes will not wipe it.

## Importing from Apple Notes

Your articles live in the **Background Music** folder in Notes. To pull in
whatever is new:

```sh
python3 import_notes.py
python3 build.py
```

Other folders, and a listing of what exists:

```sh
python3 import_notes.py "literature"
python3 import_notes.py --list
```

The importer reads a copy of the Notes database, never the live one, so it is
safe to run while Notes is open. Notes whose body is empty (title only) are
skipped, and files already in `content/` are never overwritten — so if you edit
an article here, a later import will not clobber it. The flip side: editing the
note in Notes afterwards will *not* update the file. Delete the file in
`content/` and re-import to pick up changes.

**Images are not imported.** macOS blocks access to the Notes media folder
without Full Disk Access, so pictures embedded in a note are dropped. To add one
back, put the image file in `assets/img/` and write a line like
`![caption](img/filename.jpg)` in the article.

## Importing from Facebook

1. On Facebook: **Settings → Your information → Download your information**
2. Select **Posts** only, format **JSON**, then request the download.
3. When the archive arrives, unzip it and find the folder holding
   `your_posts__check_ins__photos_and_videos_1.json` (the exact name varies).
4. Run:

```sh
python3 import_fb.py ~/Downloads/your-export/posts/
```

Each post becomes a file in `content/`. Posts shorter than 40 characters and
posts with no text are skipped. Existing files are never overwritten, so you can
re-run this after a later export and only the new posts come in.

Then edit the titles in `content/` to taste and run `python3 build.py`.

## Removing an article

Delete its file from `content/`, rebuild, and push:

```sh
rm content/2025-02-08-the-girl-from-ipanema.md
python3 build.py
git add -A && git commit -m "Remove an article" && git push
```

`build.py` wipes `docs/` and regenerates it, so the page disappears from the
site and from the homepage list. Do not delete the file in `docs/` by hand — the
next build would put it back.

One caveat: the article is still in Apple Notes, so `import_notes.py` would
bring it back. Either move the note out of the Background Music folder, or just
remember not to re-import it.

## Settings

`site.json`:

| Key       | Meaning                                                     |
|-----------|-------------------------------------------------------------|
| `title`   | Site name, shown in the header and browser tab              |
| `tagline` | One line under the site name on the homepage                |
| `author`  | Name in the footer copyright                                |
| `url`     | Public address of the site; used for sitemap and link previews |
| `lang`    | Page language for browsers and screen readers (`th`)         |
| `noindex` | `true` keeps the site out of Google; `false` allows indexing |

Changing `noindex` takes effect on the next `python3 build.py`.

## Layout

```
site.json        settings
content/         your articles, one .md file each  <- you edit these
assets/          style.css                          <- edit to restyle
build.py         content/ -> docs/
import_notes.py  Apple Notes -> content/
import_fb.py     Facebook JSON -> content/
docs/            generated output — GitHub Pages serves this; never edit by hand
```

Everything in `docs/` is overwritten on every build.

## Previewing locally

```sh
cd docs && python3 -m http.server 8000
```

Then open http://localhost:8000 — press Ctrl-C to stop. This is only for your
own machine; it is not how the site is published.
