# PocketBooks landing page

The page for **https://www.mauli.app/pocketbooks** — one static HTML file, no
build step, no dependencies, no third-party requests.

```text
web/site/            →  rsync  →  <docroot>/pocketbooks/
├── index.html          the whole page, CSS and copy inline
└── assets/             app screenshots, icon, share image
```

| File | Purpose |
|---|---|
| [`site/`](site) | The finished page. Deploy this folder as-is. |
| [`deploy.sh`](deploy.sh) | Uploads `site/` into the mauli.app subdirectory |
| [`assets/`](assets) | Source screenshots, mirrored into `site/assets/` |
| [`LOVABLE_BRIEF.md`](LOVABLE_BRIEF.md) | Optional: rebuild the page in Lovable instead |

## Why a single file

mauli.app is a static site on a managed host, so `mauli.app/pocketbooks` is
literally a folder in the document root. A plain HTML page with **relative**
asset paths works there immediately — no base path to configure, nothing to
build, nothing that can break on a host without Node.

That also keeps a promise the page itself makes: it loads **zero** external
resources. No web fonts, no analytics, no CDN. Verified in a browser —
`performance.getEntriesByType('resource')` returns nothing off-origin. Which
means no cookie banner and nothing to declare.

## What is on it

Hero, the "two minutes instead of six hours" band, six feature cards, the five
app screenshots, three setup steps, the public-domain starter library, a
privacy section, and an FAQ. Bilingual: English by default, German via the
EN/DE toggle, which also follows the browser language on first visit and
remembers the choice.

## Preview it

```bash
cd web && python3 -m http.server 8911
# open http://127.0.0.1:8911/site/
```

## Deploy it

`deploy.sh` reads every connection detail from the environment, so no hostname,
user, path or key ever lands in this public repository. The values for
mauli.app are kept outside the repo in `~/.config/pocketbooks/web.env`:

```bash
source ~/.config/pocketbooks/web.env
web/deploy.sh
```

If that file is missing, recreate it with the five variables the script needs:

```bash
export KB_WEB_SSH_HOST=your-server.example
export KB_WEB_SSH_USER=youruser
export KB_WEB_SSH_PORT=22
export KB_WEB_REMOTE_DIR=/path/to/docroot/pocketbooks
export KB_WEB_SSH_KEY=~/.ssh/id_ed25519        # optional
```

The script creates the target directory if needed and syncs with `--delete`.
It excludes the separately published `catalogue/` directory from the sync
and from deletion.

## Publishing the demo catalogue

The demo catalogue for PocketBooks has not been generated or published yet.
The sample books are rendered with this app's voices, so it is a separate
catalogue from the KokoroBooks one.

The app's *Try the demo library* button expects a pre-rendered catalogue at
`https://www.mauli.app/pocketbooks/catalogue/`. Build and publish it from a
library that contains **only** the public-domain samples:

```bash
cd backend
PBS_LIBRARY_DIR=~/Audiobooks/PDFs/"Public Domain Samples" \
PBS_DATA_DIR=/tmp/catalogue-build \
  .venv/bin/python -m app.catalogue export --dest /tmp/catalogue --voice 1

.venv/bin/python -m app.catalogue check --dest /tmp/catalogue
```

The books have to be generated first — export only picks up finished jobs and
names every title it skips. Roughly 1 GB and six to ten hours of one-off generation
for all five titles on an M2 Pro (about 33 hours of audio at roughly 3–5× real time).

For an incremental update, merge the new entries into the live manifest by
`book_id` so previously published titles stay listed. Upload the new book and
cover files first, without `--delete`, and verify that they are served before
replacing `catalogue.json`.

Check the result the same way the app will read it:

```bash
curl -s https://www.mauli.app/pocketbooks/catalogue/catalogue.json | head
ffprobe -v error -show_entries format=duration \
  "https://www.mauli.app/pocketbooks/catalogue/books/<id>/hls/index.m3u8"
```

## Refreshing the screenshots
The images come from the real app via
[`../ios/screenshots.sh`](../ios/screenshots.sh). After a UI change:

```bash
PBS_URL=http://127.0.0.1:8725 PBS_TOKEN=... ios/screenshots.sh
for f in ios/screenshots/iphone-16-pro-max/*.png; do
  name=$(basename "$f")
  cp "$f" "web/assets/$name" && sips -Z 1100 "web/assets/$name" >/dev/null
  cp "web/assets/$name" "web/site/assets/$name"
done
```

The share image `site/assets/og.png` is composed from the player screenshot;
regenerate it if the hero screenshot changes.

## If you would rather use Lovable

[`LOVABLE_BRIEF.md`](LOVABLE_BRIEF.md) still holds the full brief. Note that a
Lovable project builds a Vite app, which needs `base: '/pocketbooks/'` and a
router basename to survive on a subpath — the thing the static page avoids
entirely. Lovable can also publish to a subdomain such as
`pocketbooks.mauli.app` with one CNAME, if a second domain is easier for you
than a folder.
