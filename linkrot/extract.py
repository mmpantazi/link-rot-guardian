"""Find links in Markdown and HTML files, recording exact character spans."""
import html
import os
import re
from dataclasses import dataclass

MD_EXT = {".md", ".markdown", ".mdx", ".txt"}
HTML_EXT = {".html", ".htm"}
SKIP_DIRS = {".git", "node_modules", ".hg", ".svn", "__pycache__"}

FENCE = re.compile(r"^(?P<ind> {0,3})(?P<f>`{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}(?P=f)[`~]*[ \t]*$|\Z)",
                   re.S | re.M)
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)
INLINE = re.compile(r"\]\(\s*<?((?:[^()\s<>]|\([^()\s]*\))+)")
REFDEF = re.compile(r"^ {0,3}\[[^\]\n]+\]:[ \t]*<?(\S+?)>?(?:[ \t]+[\"'(].*)?$", re.M)
AUTOLINK = re.compile(r"<(https?://[^\s<>]+)>")
BARE = re.compile(r"(?<![\"'<=\w/])https?://[^\s<>\"'\]\[]+")
HTML_ATTR = re.compile(r"""\b(?:href|src)\s*=\s*(["'])(.*?)\1""", re.S | re.I)
TRAIL = ".,;:!?*_~"


@dataclass
class Link:
    file: str
    line: int
    start: int
    end: int
    url: str
    in_html_attr: bool = False

    @property
    def kind(self):
        low = self.url.lower()
        if low.startswith(("http://", "https://")):
            return "web"
        if self.url.startswith("//") or re.match(r"^[a-z][a-z0-9+.-]*:", low) or self.url.startswith("#"):
            return "skip"
        return "local"


def _mask(text, pattern, keep_group=None):
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _clean_bare(raw):
    url = raw
    while url and url[-1] in TRAIL:
        url = url[:-1]
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1]
    return url


def extract_text(text, path, is_html=False):
    links, taken = [], []

    def add(start, end, url, attr=False):
        if any(start < e and s < end for s, e in taken):
            return
        taken.append((start, end))
        line = text.count("\n", 0, start) + 1
        links.append(Link(path, line, start, end, html.unescape(url) if attr else url, attr))

    if not is_html:
        scan = _mask(_mask(text, FENCE), INLINE_CODE)
    else:
        scan = text
    for m in HTML_ATTR.finditer(scan):
        add(m.start(2), m.end(2), m.group(2), True)
    if not is_html:
        for pat in (INLINE, REFDEF, AUTOLINK):
            for m in pat.finditer(scan):
                add(m.start(1), m.end(1), m.group(1))
        for m in BARE.finditer(scan):
            url = _clean_bare(m.group(0))
            add(m.start(), m.start() + len(url), url)
    links.sort(key=lambda l: l.start)
    return links


def iter_files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for f in sorted(files):
                if os.path.splitext(f)[1].lower() in MD_EXT | HTML_EXT:
                    yield os.path.join(root, f)


def extract_file(path):
    ext = os.path.splitext(path)[1].lower()
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()
    return extract_text(text, path, is_html=ext in HTML_EXT)


def read_url_list(path):
    links = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            s = line.strip()
            if s and not s.startswith("#"):
                links.append(Link(path, n, 0, 0, s.split()[0]))
    return links


def local_exists(link, root):
    """Does a relative/site-root link point at an existing file or folder?"""
    target = re.split(r"[?#]", link.url, 1)[0]
    if not target:
        return True
    target = html.unescape(target) if link.in_html_attr else target
    from urllib.parse import unquote
    target = unquote(target)
    base = os.path.abspath(root) if target.startswith("/") else os.path.dirname(os.path.abspath(link.file))
    full = os.path.normpath(os.path.join(base, target.lstrip("/")))
    if os.path.isfile(full):
        return True
    if os.path.isdir(full):
        return True
    return any(os.path.isfile(full + e) for e in (".md", ".html", ".htm", ".markdown"))
