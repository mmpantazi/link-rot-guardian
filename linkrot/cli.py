"""linkrot command line: check, fix."""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from urllib.parse import urldefrag

from . import __version__, extract, patch, wayback
from .check import Checker

DEFAULT_IGNORE = [r"^https?://(localhost|127\.|0\.0\.0\.0|\[::1\]|10\.|192\.168\.)",
                  r"^https?://([^/]*\.)?example\.(com|org|net)(/|$)", r"^https?://[^/]*\.(invalid|test|local)(/|$)",
                  r"^https?://[^/]*\{", r"^https?://[^/]*\$"]
ICON = {"ok": "ok  ", "moved": "MOVE", "redirected": "redir", "dead": "DEAD", "soft404": "SOFT",
        "blocked": "????", "error": "ERR "}


def build_parser():
    ap = argparse.ArgumentParser(prog="linkrot", description="Find dead links, suggest Wayback replacements, patch after approval.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, hlp in (("check", "report dead and moved links"), ("fix", "check, then patch files after approval")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("paths", nargs="*", help="Markdown/HTML files or folders")
        p.add_argument("--urls", metavar="FILE", help="text file with one URL per line (report only)")
        p.add_argument("--since", metavar="GIT_REF", help="only scan files changed since GIT_REF (e.g. origin/main)")
        p.add_argument("--root", default=".", help="site root for /absolute local links (default: .)")
        p.add_argument("--ignore", action="append", default=[], metavar="REGEX", help="skip URLs matching REGEX (repeatable)")
        p.add_argument("--ignore-file", metavar="FILE", default=".linkrotignore",
                       help="file with one ignore REGEX per line (default: .linkrotignore if present)")
        p.add_argument("--timeout", type=float, default=15)
        p.add_argument("--workers", type=int, default=16)
        p.add_argument("--no-soft", action="store_true", help="do not look for soft-404/parked pages")
        p.add_argument("--no-local", action="store_true", help="do not check relative file links")
        p.add_argument("--cache", default=os.environ.get("LINKROT_CACHE", os.path.expanduser("~/.cache/linkrot/wayback.json")))
        p.add_argument("--allow-private", action="store_true", help="also check localhost/private-network URLs")
        p.add_argument("--no-archive", action="store_true", help="do not query the Wayback Machine")
    c = sub.choices["check"]
    c.add_argument("--format", choices=["text", "json", "md"], default="text")
    c.add_argument("--fail-on", choices=["dead", "moved", "never"], default="dead",
                   help="what makes the exit code 1 (default: dead; moved also counts permanent redirects)")
    c.add_argument("--show-ok", action="store_true", help="also list healthy and temporarily redirected links")
    f = sub.choices["fix"]
    f.add_argument("--yes", action="store_true", help="apply every proposal without asking")
    f.add_argument("--dry-run", action="store_true", help="only print the diff")
    f.add_argument("--no-moved", action="store_true", help="do not propose permanent-redirect updates")
    return ap


def log(msg):
    print(msg, file=sys.stderr)


def changed_since(ref):
    out = subprocess.run(["git", "-c", "safe.directory=*", "diff", "--name-only", "--diff-filter=d", ref, "--"],
                         capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"git diff {ref} failed: {out.stderr.strip()}")
    top = subprocess.run(["git", "-c", "safe.directory=*", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True).stdout.strip()
    return {os.path.realpath(os.path.join(top, n)) for n in out.stdout.split("\n") if n}


def collect(args):
    links = []
    only = changed_since(args.since) if args.since else None
    for path in extract.iter_files(args.paths):
        if only is not None and os.path.realpath(path) not in only:
            continue
        links.extend(extract.extract_file(path))
    if args.urls:
        links.extend(extract.read_url_list(args.urls))
    ignore = [re.compile(r) for r in (DEFAULT_IGNORE[1:3] if args.allow_private else DEFAULT_IGNORE) + args.ignore + read_ignore_file(args.ignore_file)]
    web = [l for l in links if l.kind == "web" and not any(r.search(l.url) for r in ignore)]
    local = []
    if not args.no_local:
        for l in links:
            if l.kind == "local" and l.file != args.urls:
                why = extract.local_problem(l, args.root)
                if why:
                    local.append((l, why))
    return links, web, local


def run_checks(args, web):
    checker = Checker(timeout=args.timeout, workers=args.workers, soft=not args.no_soft)
    tty = sys.stderr.isatty()

    def prog(done, total):
        if tty:
            print(f"\rchecking {done}/{total}", end="", file=sys.stderr, flush=True)
    results = checker.check_all([l.url for l in web if not wayback.is_archive_url(l.url)], prog)
    if tty and results:
        print(file=sys.stderr)
    return results


def with_archive(args, web, results):
    """Map each problem link to a Wayback snapshot (or None)."""
    if args.no_archive:
        return {}
    wb = wayback.Wayback(args.cache)
    found = {}
    todo = sum(1 for l in web if results.get(l.url) and results[l.url].problem)
    if todo:
        log(f"looking up {todo} dead link(s) in the Wayback Machine (about 1-8 s each)...")
    for l in web:
        r = results.get(l.url)
        if r and r.problem:
            hint = patch.added_date(l.file, l.url) if os.path.isfile(l.file) and l.end else None
            key = (l.file, l.start, l.url)
            found[key] = wb.closest(l.url, hint)
    return found


def summarize(results):
    counts = defaultdict(int)
    for r in results.values():
        counts[r.verdict] += 1
    return counts


def cmd_check(args):
    links, web, local = collect(args)
    results = run_checks(args, web)
    snaps = with_archive(args, web, results)
    rows = []
    for l in web:
        r = results.get(l.url)
        if not r:
            continue
        s = snaps.get((l.file, l.start, l.url))
        rows.append({"file": l.file, "line": l.line, "url": l.url, "verdict": r.verdict, "status": r.status,
                     "detail": r.detail, "final_url": r.final_url if r.verdict in ("moved", "redirected") else "",
                     "archive": wayback.replay_url(s) if s else "", "archive_date": s.date if s else ""})
    for l, why in local:
        rows.append({"file": l.file, "line": l.line, "url": l.url, "verdict": "dead", "status": 0,
                     "detail": why, "final_url": "", "archive": "", "archive_date": ""})
    failing = {"dead": ("dead", "soft404"), "moved": ("dead", "soft404", "moved"), "never": ()}[args.fail_on]
    bad = [r for r in rows if r["verdict"] in failing]
    if args.format == "json":
        print(json.dumps(rows, indent=2))
    elif args.format == "md":
        print_md(rows)
    else:
        print_text(rows, args.show_ok)
    return 1 if bad else 0


def print_text(rows, show_ok):
    by_file = defaultdict(list)
    for r in rows:
        if show_ok or r["verdict"] not in ("ok", "redirected"):
            by_file[r["file"]].append(r)
    for f, items in by_file.items():
        print(f)
        for r in sorted(items, key=lambda r: r["line"]):
            print(f"  {r['line']:>4}  {ICON[r['verdict']]}  {r['url']}")
            if r["detail"]:
                print(f"          {r['detail']}")
            if r["final_url"]:
                print(f"          -> {r['final_url']}")
            if r["archive"]:
                print(f"          archive ({r['archive_date']}): {r['archive']}")
            elif r["verdict"] in ("dead", "soft404") and r["url"].startswith("http"):
                print("          no archived copy found")
    counts = defaultdict(int)
    for r in rows:
        counts[r["verdict"]] += 1
    print("\n" + ", ".join(f"{n} {v}" for v, n in sorted(counts.items())) if counts else "no links found")


def print_md(rows):
    print("| File | Line | Link | Problem | Suggested replacement |\n|---|---|---|---|---|")
    for r in rows:
        if r["verdict"] == "ok":
            continue
        fix = r["final_url"] or r["archive"] or ""
        print(f"| {r['file']} | {r['line']} | {r['url']} | {r['verdict']}: {r['detail']} | {fix} |")


def keep_fragment(old, new):
    frag = urldefrag(old)[1]
    return new if not frag or "#" in new else f"{new}#{frag}"


def read_ignore_file(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]


def make_proposals(args, web, results, snaps):
    props = []
    for l in web:
        r = results.get(l.url)
        if not r or not l.end:
            continue
        if r.verdict == "moved" and not args.no_moved:
            props.append(patch.Proposal(l, keep_fragment(l.url, r.final_url), r.detail, "moved"))
        elif r.problem:
            s = snaps.get((l.file, l.start, l.url))
            if s:
                props.append(patch.Proposal(l, wayback.replay_url(s), f"{r.detail}; snapshot from {s.date}", "archive"))
    return props


def ask(prompt):
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "q"


def cmd_fix(args):
    if not args.paths:
        log("fix needs at least one file or folder")
        return 2
    args.urls = None
    links, web, _ = collect(args)
    results = run_checks(args, web)
    snaps = with_archive(args, web, results)
    props = make_proposals(args, web, results, snaps)
    if not props:
        print("nothing to fix")
        return 0
    interactive = not (args.yes or args.dry_run) and sys.stdin.isatty()
    if not (args.yes or args.dry_run or interactive):
        log("stdin is not a terminal: showing the diff only (use --yes to apply)")
        args.dry_run = True
    by_file = defaultdict(list)
    for p in props:
        by_file[p.link.file].append(p)
    applied = quit_ = 0
    for path, items in by_file.items():
        text = patch.read(path)
        lines = text.splitlines()
        stale = [p for p in items if not patch.is_current(text, p.link)]
        if stale:
            log(f"{path} changed since it was scanned; skipping {len(stale)} proposal(s), rerun to retry")
            items = [p for p in items if p not in stale]
        chosen, all_ = [], args.yes
        for p in sorted(items, key=lambda p: p.link.start):
            if interactive and not all_:
                print(f"\n{path}:{p.link.line}  [{p.kind}] {p.reason}")
                print(f"  {lines[p.link.line - 1].strip()[:160]}")
                print(f"  - {p.link.url}\n  + {p.new_url}")
                ans = ask("apply? [y]es / [n]o / [a]ll in rest / [q]uit > ")
                if ans == "q":
                    quit_ = 1
                    break
                if ans == "a":
                    all_ = True
                elif ans != "y":
                    continue
            chosen.append(p)
        if chosen:
            new = patch.apply(text, chosen)
            if args.dry_run or not (args.yes or interactive):
                print(patch.diff(path, text, new), end="")
            else:
                patch.write(path, new)
                applied += len(chosen)
        if quit_:
            break
    if not args.dry_run:
        print(f"\npatched {applied} link(s)")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "check" and not args.paths and not args.urls:
        log("give a folder, file, or --urls FILE")
        return 2
    return {"check": cmd_check, "fix": cmd_fix}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
