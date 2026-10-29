# Link Rot Guardian

Finds dead, moved and "soft-dead" links in Markdown or static-site folders (and plain URL lists), looks up the Wayback Machine snapshot **closest to when you wrote the link**, and patches your files only after you approve each change.

Python 3.9+, standard library only.

## Install

```bash
pip install .          # gives you the `linkrot` command
# or run without installing:
python3 -m linkrot --help
```

## Use

```bash
linkrot check docs/                       # report; exit code 1 if anything is dead
linkrot check docs/ --format md > rot.md  # table for an issue / PR comment (or --format json)
linkrot check --urls urls.txt             # check a list of URLs, one per line
linkrot fix docs/                         # interactive: y / n / a(ll rest) / q per change
linkrot fix docs/ --dry-run               # print a unified diff only
linkrot fix docs/ --yes                   # apply everything (non-terminal runs show a diff unless --yes)
```

Verdicts: `DEAD` (404/410, DNS gone, refused, missing local file), `SOFT` (200 but "page not found" title, parked/for-sale domain, or redirect to the site front page), `MOVE` (permanent 301/308 redirect), `????` (403/429/999: could not verify, never treated as dead), `ERR` (timeouts, 5xx, TLS problems). Temporary redirects are hidden unless `--show-ok`.

`fix` proposes two kinds of change:
- **moved**: replace the URL by its permanent-redirect target (e.g. `selenium.dev` -> `www.selenium.dev`).
- **archive**: replace a dead URL by `https://web.archive.org/web/<timestamp>/<url>`. The snapshot is the HTTP-200 capture closest to the date the link first appeared in the file (from `git log -S`, falling back to file modification time), so you get the page as the author saw it, not a later error page.

Only the exact URL span is replaced (labels, titles and surrounding text are untouched); links in code blocks are ignored; `&` is escaped in HTML attributes.

Useful options: `--ignore REGEX` (repeatable), `--root DIR` for `/absolute` local links, `--no-archive`, `--no-moved`, `--no-local`, `--no-soft`, `--workers N`, `--timeout S`, `--allow-private` (check localhost/LAN URLs; skipped by default along with example.com). Wayback answers are cached in `~/.cache/linkrot/wayback.json` (`--cache`, `LINKROT_CACHE`).

Supported files: `.md .markdown .mdx .txt .html .htm`; hidden folders and `node_modules` are skipped.

## Test

```bash
python3 -m unittest discover -s tests -t .
```
Tests run against a local HTTP server; no internet needed.

## Limits
- Wayback has no official rate limit; lookups are sequential (1 s apart) with backoff, so many dead links take time. Results are cached.
- Sites behind bot protection answer 403; those are reported as unverified, not dead.
- Soft-404 detection is heuristic (title and parked-domain phrases); review `SOFT` items before approving.
