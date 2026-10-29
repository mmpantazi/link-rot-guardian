"""Turn findings into replacement proposals and apply approved ones by exact span."""
import difflib
import html
import os
import subprocess
from dataclasses import dataclass


@dataclass
class Proposal:
    link: object
    new_url: str
    reason: str
    kind: str  # "moved" or "archive"


def apply(text, proposals):
    for p in sorted(proposals, key=lambda p: p.link.start, reverse=True):
        new = html.escape(p.new_url, quote=False) if p.link.in_html_attr else p.new_url
        text = text[:p.link.start] + new + text[p.link.end:]
    return text


def read(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def write(path, text):
    tmp = path + ".linkrot-tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def diff(path, old, new):
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        f"a/{path}", f"b/{path}"))


def added_date(path, url):
    """YYYYMMDD when `url` first appeared in `path` (git history), else file mtime."""
    d = os.path.dirname(os.path.abspath(path))
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "log", "-S" + url, "--format=%ad",
                              "--date=format:%Y%m%d", "--", os.path.abspath(path)],
                             cwd=d, capture_output=True, text=True, timeout=30).stdout.split()
        if out:
            return out[-1]
    except (OSError, subprocess.SubprocessError):
        pass
    import time
    return time.strftime("%Y%m%d", time.localtime(os.path.getmtime(path)))
