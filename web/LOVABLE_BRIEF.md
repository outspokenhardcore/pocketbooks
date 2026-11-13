# Building the landing page in Lovable

Four prompts. Paste them into [Lovable](https://lovable.dev) in order, check the
result after each one, then connect GitHub and deploy. All the copy is written
out here so Lovable has nothing to invent — the claims about this app have to
stay true.

Upload the five screenshots plus `icon-512.png` from [`assets/`](assets) into
the Lovable chat before sending prompt 1, so it can place them straight into
the mockups and use the icon in the header and favicon.

---

## Prompt 1 — the page

```text
Build a single-page marketing site for an iOS app called PocketBooks. It will
be served from the subpath https://www.mauli.app/pocketbooks, as static files.
Use Vite + React + Tailwind. No backend, no database, no auth, no Supabase, no
cookie banner, no analytics, no external fonts beyond system and Google Fonts.

POSITIONING
PocketBooks is a self-hosted audiobook system. A Python server on the user's
own Mac turns PDFs from a folder into speech with Pocket TTS
model, and this iPhone app starts playing while the Mac is still generating the
rest of the book. Nothing is uploaded anywhere. There is no account and no
cloud service. It is open source under AGPL-3.0.

The single most important idea: every other tool makes you wait hours for a
whole book to convert. This one lets you start listening after about two
minutes.

TONE
Precise, calm, technical-but-human. Short sentences. No exclamation marks, no
"revolutionary", no "AI-powered", no emoji in headings. The reader is a
developer or a technically confident Mac owner.

SECTIONS, in this order:

1. HERO
   Headline: "Your audiobook starts playing while your Mac is still making it."
   Subline: "PocketBooks turns the PDFs on your own Mac into spoken audiobooks
   and streams them to your iPhone as they are generated. No cloud. No account.
   No upload."
   Two buttons: primary "View on GitHub" linking to
   https://github.com/outspokenhardcore/pocketbooks, secondary "Join the TestFlight
   beta" linking to # for now.
   Small print under the buttons: "Requires a Mac. Free and
   open source, AGPL-3.0."
   Right side on desktop, below the text on mobile: the screenshot
   03-player-generating.png inside a simple rounded phone frame, no heavy
   drop shadow, no tilt.

2. THE WAIT, VISUALISED
   A full-width band with a single horizontal progress bar. The first 4% is
   filled in the accent blue and labelled "You start listening — under a
   minute". The
   rest is a muted track labelled "Generation continues — 6 h 41 min". One line
   underneath: "Chunked synthesis feeds a growing HLS stream, so playback can
   begin as soon as the first segments exist."

3. FEATURES — a four-column grid on desktop, one column on mobile:
   - "Your library, not a catalogue" — "Drop PDFs in a folder on your Mac. Each
     book is identified by its content hash, so renaming or moving it never
     loses your place."
   - "Read along while you listen" — "A synchronised transcript highlights the
     sentence being spoken. Tap any line to jump there."
   - "Real chapters" — "Chapter marks come from the PDF outline. Front matter,
     dedications and copyright pages are skipped."
   - "Six built-in voices" — "Pocket TTS runs locally on the CPU at 24 kHz.
     No voice cloning needed."
   - "Resume anywhere" — "Your position is stored per book on your own server
     and syncs between devices."
   - "Offline when it's done" — "The finished book is assembled into a tagged
     M4B with cover art and chapter marks."

4. SCREENSHOT GALLERY
   A horizontally scrollable row of phone mockups, each with a short caption
   under it:
   - 01-library.png — "Your own books, with covers and runtimes."
   - 03-player-generating.png — "Chapter 2 is playing. Chapter 6 does not exist
     yet."
   - 04-transcript.png — "The spoken sentence, highlighted as you listen."
   - 02-voices.png — "Six built-in voices."
   - 05-library-light.png — "Light and dark, system-wide."

5. HOW IT WORKS — three numbered steps, equal width:
   1. "Install the server" — "One clone, one virtualenv, one command on your
      Mac." Below it, a small code block showing exactly:
      pip install -r requirements.txt
      python -m app.serve
   2. "Add books" — "Drop your own PDFs into ~/Audiobooks/PDFs, or run one
      command to get five public-domain classics to start with." Code block:
      python -m app.samples
   3. "Listen" — "Build the iPhone app once, enter your server address and
      token, pick a book. Playback starts before generation finishes."

6. THE STARTER LIBRARY
   Intro line: "PocketBooks ships no books. It ships five that nobody owns."
   A responsive card grid of five books. Each card has a flat colour background
   matching its genre, the title in a serif face, the author, year and runtime.
   Use exactly these:
   - The War of the Worlds — H. G. Wells — 1898 — Science fiction — 6.7 h —
     background #0E1A2F, accent #5EEAD4
   - The Time Machine — H. G. Wells — 1895 — Science fiction — 3.6 h —
     background #0E1A2F, accent #5EEAD4
   - A Princess of Mars — Edgar Rice Burroughs — 1912 — Science fiction —
     7.2 h — background #0E1A2F, accent #5EEAD4
   - The Thirty-Nine Steps — John Buchan — 1915 — Spy thriller — 4.2 h —
     background #241610, accent #E8B059
   - The Riddle of the Sands — Erskine Childers — 1903 — Spy thriller — 11.8 h
     — background #241610, accent #E8B059
   Caption under the grid: "Public domain in both the United States and the
   European Union. The provenance of every title is documented in the
   repository." Link "documented in the repository" to
   https://github.com/outspokenhardcore/pocketbooks/blob/main/samples/README.md

7. PRIVACY — a short, calm block, no icons:
   "Your PDFs, the prepared text and the generated audio never leave your Mac.
   The app talks to exactly one host: the server address you type in. There is
   no analytics, no telemetry and no third-party network call. The API is
   protected by a bearer token and is meant for your home network or your
   Tailscale tailnet."

8. FAQ — an accordion with these five entries, answers one short paragraph each:
   - "Do I need a Mac?" — "Yes. Generation runs on your Mac's CPU, no GPU needed.
     The iPhone app is only the player."
   - "Is it in the App Store?" — "Not yet. The server is on GitHub today and
     the app is heading for TestFlight first."
   - "Does it work with my own books?" — "Any PDF with a real text layer.
     Scanned, image-only PDFs are rejected rather than guessed at. Only process
     books you are allowed to process."
   - "Does it clone voices?" — "No. It uses six built-in, openly licensed voices and
     has no cloning feature."
   - "What does it cost?" — "Nothing. It is open source under AGPL-3.0."

9. FOOTER
   Links: "GitHub", "Mauli App" to https://www.mauli.app/, "Datenschutz" to
   https://www.mauli.app/datenschutz.html. Line: "PocketBooks is an open-source
   project. AGPL-3.0."

DESIGN
Dark by default: background #0B0B0C, surfaces #151517, body text #E7E5E1,
muted text #9A958C, accent #0A84FF (the iOS system blue the app uses).
Headings in a modern geometric sans; book titles and the hero headline in a
serif face (Source Serif 4 or similar) to echo the app's typography.
Generous vertical rhythm, max content width 1200px, rounded corners 16px,
one-pixel hairline borders instead of shadows. No gradients on text. No
animation beyond a soft fade-in on scroll, and respect
prefers-reduced-motion.

QUALITY BAR
Semantic HTML with one h1 and correct heading order. All images need real alt
text describing what the screen shows. Colour contrast at least 4.5:1 for body
text. Keyboard-focusable accordion and buttons with a visible focus ring.
Lighthouse accessibility and best-practices should both be 100. Mobile first;
check 390px, 768px and 1440px.
```

---

## Prompt 2 — make it work under /pocketbooks

Send this immediately after prompt 1. Without it the deployed page is blank,
because every asset URL points at the domain root.

```text
This site will be served from the subpath https://www.mauli.app/pocketbooks/,
not from a domain root. Configure it for that:

1. In vite.config.ts set `base: '/pocketbooks/'`.
2. If react-router is used, set the router basename to '/pocketbooks'.
3. Make every internal link and asset reference relative or prefixed with the
   base — no hard-coded '/assets/...' or '/images/...' paths anywhere.
4. Add a public/.htaccess file so an Apache host rewrites deep links back to
   the single page, scoped to this subdirectory:

   RewriteEngine On
   RewriteBase /pocketbooks/
   RewriteRule ^index\.html$ - [L]
   RewriteCond %{REQUEST_FILENAME} !-f
   RewriteCond %{REQUEST_FILENAME} !-d
   RewriteRule . /pocketbooks/index.html [L]

5. Confirm that `npm run build` produces a dist/ folder whose index.html
   references assets as "./assets/..." or "/pocketbooks/assets/...".
```

---

## Prompt 3 — metadata and sharing

```text
Add the page metadata:

- <html lang="en">
- Title: "PocketBooks — self-hosted audiobooks that play while they generate"
- Meta description: "Turn the PDFs on your own Mac into
  audiobooks with Pocket TTS and start listening on your iPhone before
  generation finishes. Open source, no cloud, no account."
- Canonical URL https://www.mauli.app/pocketbooks/
- Open Graph and Twitter card tags using the same title and description, with
  the hero screenshot as the share image at 1200x630. Generate that image as a
  composition: dark background, the headline on the left, the player screenshot
  on the right.
- A favicon and apple-touch-icon from the uploaded app icon (icon-512.png and
  icon-180.png), which shows the PocketBooks mole on deep navy.
- JSON-LD of type SoftwareApplication: name PocketBooks, applicationCategory
  "BookApplication", operatingSystem "iOS, macOS", offers price "0", license
  "AGPL-3.0", url https://github.com/outspokenhardcore/pocketbooks.

Do not add Google Analytics, Plausible, or any other tracker.
```

---

## Prompt 4 — optional, a German version

mauli.app is a German site, so this may be worth it. Only send it once the
English page is final.

```text
Add a language toggle in the header with EN and DE. Keep English as the
default and the canonical version. Translate all copy into natural German —
not a literal translation. Keep these terms in English because they are
product or technical names: PocketBooks, Pocket TTS, HLS, M4B, PDF,
TestFlight, Tailscale, Open Source. Keep the book titles in English, since the
books themselves are English. Persist the choice in localStorage and set the
html lang attribute accordingly. Add hreflang alternate links.
```

---

## Then: connect GitHub and deploy

1. In Lovable, open **GitHub → Connect to GitHub** and create the repository
   `outspokende/pocketbooks-web`. Lovable keeps it in sync both ways, so you
   can also edit locally and push.
2. Clone it next to this repository and point the deploy script at it:

   ```bash
   git clone https://github.com/outspokende/pocketbooks-web.git
   export KB_WEB_REPO=$PWD/pocketbooks-web
   ```

3. Set the connection details for the mauli.app host (see
   [`README.md`](README.md)) and run:

   ```bash
   web/deploy.sh
   ```

4. Open https://www.mauli.app/pocketbooks/ and check: the page renders with
   styling (if it is unstyled, prompt 2 did not take effect), the screenshots
   load, and a reload on a deep link does not 404.

## Keeping it honest

Two claims on the page will become false if nothing changes, so check them on
every update:

- **"Not yet in the App Store."** Update the FAQ and swap the TestFlight button
  for a real link the moment that changes.
- **The runtimes and the chapter counts** come from the real pipeline. If the
  sample catalogue changes, regenerate them with
  `python -m app.samples --list` and the table in
  [`../samples/README.md`](../samples/README.md).
