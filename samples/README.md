# Public-domain sample library

PocketBooks ships **no books**. It ships a short, curated list of works that
are in the public domain, plus a builder that turns them into clean, text-layer
PDFs in your own library folder.

```bash
cd backend
source .venv/bin/activate
python -m app.samples
```

This downloads the plain-text source of each title, removes the distributor's
header and license footer, and renders a PDF with a title page, an embedded
table of contents, and proper chapter marks into
`~/Audiobooks/PDFs/Public Domain Samples/`. The whole set is about 3 MB and
takes a few seconds.

| Option | Effect |
|---|---|
| `python -m app.samples --list` | Print the catalog with rights information |
| `python -m app.samples --only the-time-machine` | Build a single title (repeatable) |
| `python -m app.samples --dest ~/Books` | Build somewhere other than the library |
| `python -m app.samples --force` | Rebuild titles that already exist |

The machine-readable catalog is [`catalog.json`](catalog.json). Nothing is
cached inside the repository, so the generated PDFs never end up in Git.

## The titles

| Title | Author | First published | Genre | Chapters | Approx. listening time |
|---|---|---|---|---|---|
| [The War of the Worlds](https://www.gutenberg.org/ebooks/36) | H. G. Wells | 1898 | Science fiction | 27 | ~6.7 h |
| [The Time Machine](https://www.gutenberg.org/ebooks/35) | H. G. Wells | 1895 | Science fiction | 17 | ~3.6 h |
| [A Princess of Mars](https://www.gutenberg.org/ebooks/62) | Edgar Rice Burroughs | 1912 | Science fiction | 28 | ~7.2 h |
| [The Thirty-Nine Steps](https://www.gutenberg.org/ebooks/558) | John Buchan | 1915 | Spy thriller | 10 | ~4.2 h |
| [The Riddle of the Sands](https://www.gutenberg.org/ebooks/2360) | Erskine Childers | 1903 | Spy thriller | 29 | ~11.8 h |

Listening times are estimates at roughly 165 words per minute. All five titles
are English.

*The War of the Worlds* is the same public-domain test book used by the related
[Kokoro PDF Audiobook](https://github.com/outspokenhardcore/kokoro-pdf-audiobook)
project, so output from both projects can be compared directly.

## Rights

Every title is in the public domain in both the United States and the European
Union:

| Title | Author died | United States | European Union / Germany |
|---|---|---|---|
| The War of the Worlds | 1946 | Published 1898, before the 1930 cut-off | Term (70 years *p.m.a.*) expired end of 2016 |
| The Time Machine | 1946 | Published 1895, before the 1930 cut-off | Term expired end of 2016 |
| A Princess of Mars | 1950 | First published 1912, before the 1930 cut-off | Term expired end of 2020 |
| The Thirty-Nine Steps | 1940 | Published 1915, before the 1930 cut-off | Term expired end of 2010 |
| The Riddle of the Sands | 1922 | Published 1903, before the 1930 cut-off | Term expired end of 1992 |

The German term is set by [§ 64 UrhG](https://www.gesetze-im-internet.de/urhg/__64.html)
(70 years after the author's death); other EU states apply the same term under
Directive 2006/116/EC.

Source texts come from [Project Gutenberg](https://www.gutenberg.org). The
builder removes the Project Gutenberg header, footer, and license text, so the
generated PDFs contain the public-domain work and the PocketBooks title page
only, and carry no Project Gutenberg trademark or licensed material. If you
redistribute the generated PDFs, you are redistributing the public-domain work.

## Why generated PDFs instead of scans

Scanned public-domain books carry OCR errors, library stamps, and running
headers that end up in the audio. The builder produces a PDF with a real text
layer, one chapter per outline entry, and no front matter, which is the input
PocketBooks handles best. If you want to test extraction against a hard,
real-world scan instead, use any scanned public-domain PDF of your choice — the
pipeline handles it, it is just noisier.

## Adding your own title

Add an entry to [`catalog.json`](catalog.json) with the fields shown there and
run `python -m app.samples --only <slug>`. Only add works that are in the public
domain in your jurisdiction, and record the evidence in the `rights` block. Do
not add books you merely own a copy of.
