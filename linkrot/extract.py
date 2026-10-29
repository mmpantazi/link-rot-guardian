"""Find links in Markdown and HTML files, recording exact character spans."""
import html
import os
import re
from urllib.parse import unquote
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
        if self.url.startswith("//") or re.match(r"^[a-z][a-z0-9+.-]*:", low) or self.url == "#":
            return "skip"
        return "local"


def _mask(text, pattern):
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
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as fh:
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


def slug(heading):
    """GitHub-style heading anchor."""
    h = re.sub(r"<[^>]+>|[`*_~]|!?\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", heading).strip().lower()
    h = re.sub(r"[^\w\- ]", "", h, flags=re.U)
    return h.replace(" ", "-")


def find_anchors(path):
    """Set of anchor names defined in a Markdown/HTML file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return set()
    found = set(re.findall(r"""\b(?:id|name)\s*=\s*["']([^"']+)["']""", text))
    if os.path.splitext(path)[1].lower() in MD_EXT:
        seen = {}
        for m in re.finditer(r"^ {0,3}#{1,6}[ \t]+(.*?)[ \t#]*$", _mask(text, FENCE), re.M):
            base = slug(m.group(1))
            n = seen.get(base, 0)
            seen[base] = n + 1
            found.add(base if n == 0 else f"{base}-{n}")
    return found


def resolve(link, root):
    """Path of the local file a link points to, or None if it does not exist."""
    target = unquote(re.split(r"[?#]", link.url, 1)[0])
    if not target:
        return link.file
    base = os.path.abspath(root) if target.startswith("/") else os.path.dirname(os.path.abspath(link.file))
    full = os.path.normpath(os.path.join(base, target.lstrip("/")))
    if os.path.isfile(full):
        return full
    if os.path.isdir(full):
        for idx in ("index.html", "index.md", "README.md", "_index.md"):
            if os.path.isfile(os.path.join(full, idx)):
                return os.path.join(full, idx)
        return full
    for e in (".md", ".html", ".htm", ".markdown"):
        if os.path.isfile(full + e):
            return full + e
    return None


def local_problem(link, root, anchors=True):
    """Why a local link is broken ('' if fine)."""
    target = resolve(link, root)
    if target is None:
        return "local file not found"
    frag = unquote(link.url.split("#", 1)[1]) if "#" in link.url else ""
    if anchors and frag and os.path.isfile(target) and os.path.splitext(target)[1].lower() in MD_EXT | HTML_EXT:
        if frag.lower() not in {a.lower() for a in find_anchors(target)} and not re.match(r"^L\d+", frag):
            return f"anchor #{frag} not found in {os.path.relpath(target)}"
    return ""
