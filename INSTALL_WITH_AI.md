# Install and use PocketBooks with an AI coding assistant

These are ready-to-paste prompts for Claude Code, Copilot CLI, Cursor, Codex, or
any other agentic assistant with shell access. Each prompt is self-contained,
states the guard rails, and ends in a result you can check.

The ordinary manual path is in the [README quick start](README.md#quick-start);
nothing here replaces it, the prompts just drive the same commands for you.

> **Before you start:** clone the repository first and run the assistant from
> inside the checkout on a Mac. Never let an assistant upload
> your PDFs, audio, API token, or server configuration anywhere.

---

## 1 · Install the server and build the sample library

```text
Set up this PocketBooks repository on my Mac and leave me with a
running server and a working sample library.

Read README.md and SETUP.md first. Verify that this is macOS
and that Python 3.11 and ffmpeg are available; if ffmpeg is missing, tell me to
run `brew install ffmpeg` instead of installing system software yourself.

Then, in backend/: create a .venv with python3.11, install
requirements.txt into it, create ~/Audiobooks/PDFs, and run
`python -m app.samples` to build the public-domain starter library. Confirm
that five PDFs exist in ~/Audiobooks/PDFs/Public Domain Samples.

Finally start the server with `python -m app.serve`, wait until it is
listening, and show me the API bearer token it prints together with the LAN URL
I should enter in the iPhone app.

Rules: explain that first setup downloads Python packages and, on first
conversion, the Pocket TTS model from Hugging Face (no account needed). Do not use sudo, do not install global Python
packages, do not modify files outside this repository and ~/Audiobooks, and do
not print or store the token anywhere except the terminal. The server is for a
trusted LAN or Tailscale only — never suggest port forwarding or exposing it to
the internet. If a command fails, show the actual error and propose the
smallest safe fix before retrying.
```

**Done when:** the server prints a token, `http://<mac-ip>:8725/api/library`
answers with the five sample books, and nothing was installed system-wide.

---

## 2 · Build and install the iPhone app

```text
Generate and build the PocketBooks iOS client in this repository for my iPhone.

Read ios/README.md and docs/APP_STORE.md first. Check that Xcode and xcodegen
are installed; if xcodegen is missing, tell me to run `brew install xcodegen`.

Ask me for the bundle identifier I want to use and for my Apple Developer Team
ID. Replace the example identifier `com.example.pocketbooks` in ios/project.yml
with mine, run `xcodegen generate` in ios/, and verify the project builds for
the iOS Simulator with
`xcodebuild -project PocketBooks.xcodeproj -scheme PocketBooks -destination 'generic/platform=iOS Simulator' build CODE_SIGNING_ALLOWED=NO`.

Then tell me exactly which steps I have to do in Xcode myself: select my team,
pick my iPhone, and run. Afterwards remind me to enter the server URL and the
bearer token in the app's settings screen.

Rules: never commit my Team ID, bundle identifier, device name, provisioning
profile, or token. Do not change Swift source files unless the build fails, and
if it does, show me the compiler error before changing anything.
```

**Done when:** the simulator build succeeds and you know the two values to type
into the app.

---

## 3 · Make the first audiobook

```text
Walk me through turning one book into audio with my running PocketBooks server,
using the API so we can see what is happening.

My server URL and bearer token are in my shell as $PBS_URL and $PBS_TOKEN —
read them from the environment, never print the token.

1. List the library and show me the five public-domain sample books with their
   ids and estimated runtimes.
2. Ask me which one to start and which voice to use.
3. Call the prepare endpoint for that book first, then show me the detected
   chapter titles so I can confirm the text looks right before any audio is
   generated.
4. Start the conversion, then poll the job status every 30 seconds and report
   progress as a short line: percent done, current chapter, estimated time
   left. Stop polling when I say so or when the job is finished.
5. Tell me when enough audio exists to start listening on the iPhone — I do not
   have to wait for the whole book.

Rules: do not start more than one conversion. Do not download the finished M4B
unless I ask. Do not process any PDF I did not pick.
```

**Done when:** the iPhone can start playing while the Mac is still generating.

---

## 4 · Add your own public-domain book

```text
Add one public-domain book to the PocketBooks sample catalog in this repository.

Read samples/README.md and samples/catalog.json first so you follow the exact
field layout.

I will give you a title. Before adding anything, verify and show me the
evidence that the work is in the public domain in both the United States and
the European Union: first publication year, the author's year of death, and the
source URL of the plain-text edition. If either jurisdiction is unclear, stop
and tell me — do not add the entry.

Then add the catalog entry, run `python -m app.samples --only <slug>` from
backend/ with the venv active, and verify the generated PDF: it must open, have
a table of contents with sensible chapter titles, and start with the first
chapter rather than with front matter. Finally run `python -m pytest -q
tests/test_samples.py`.

Rules: only public-domain works. Never add a book because I own a copy of it.
Do not commit any PDF into the repository.
```

**Done when:** `python -m app.samples --list` shows the new title and the
sample tests pass.

---

## 5 · Diagnose a problem

```text
Something is wrong with my PocketBooks setup. Diagnose it before changing
anything.

Work through this in order and report what you find after each step:
1. Is the backend process running, and what does `GET /api/monitoring` return?
2. Does `GET /api/library` return books, or is the library folder empty?
3. For the failing book: what does the prepare step report — how many pages,
   chapters, and chunks, and does the extraction report flag anything?
4. Check the server log in ~/Library/Logs/ for the newest errors.
5. Is ffmpeg on PATH, and does `ffmpeg -version` work?

Only after you have shown me the findings, propose exactly one fix and explain
what it changes. Do not delete the audio cache, the state database, or anything
in ~/Library/Application Support/PocketBooks without asking me first. Redact
the bearer token from everything you print.
```

**Done when:** you have a named cause, not a guess.

---

## Guard rails worth repeating

- **Nothing leaves the Mac.** PDFs, prepared text, audio, and the token stay
  local. Do not let an assistant paste them into a chat, an issue, or a gist.
- **No sudo, no global installs.** Everything belongs in `backend/.venv`.
- **Private network only.** The API is bearer-protected, but it is still meant
  for a trusted LAN or a Tailscale tailnet, never a public address.
- **Your books are your responsibility.** Only process files you are allowed to
  process. The shipped catalog contains public-domain works only.
