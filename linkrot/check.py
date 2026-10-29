"""Check URLs: classify dead, moved, soft-404, blocked and healthy links."""
import http.client
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

UA = "Mozilla/5.0 (compatible; link-rot-guardian/0.1; +https://github.com/link-rot-guardian)"
PERMANENT = {301, 308}
BLOCKED = {401, 403, 429, 999, 451}
DEAD_STATUS = {404, 410}
SOFT_TITLE = re.compile(r"(page|file|document)? ?(not found|no longer (available|exists?))|\b404\b|"
                        r"does(n't| not) exist|error 404", re.I)
PARKED = re.compile(r"domain (name )?(is |may be )?(for sale|available)|buy this domain|"
                    r"this domain (has expired|is parked)|parked (free|domain)|"
                    r"account (has been )?suspended|sedo\.com/search|hugedomains", re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


@dataclass
class Result:
    url: str
    verdict: str  # ok moved redirected dead soft404 blocked error
    status: int = 0
    final_url: str = ""
    detail: str = ""
    chain: list = field(default_factory=list)

    @property
    def problem(self):
        return self.verdict in ("dead", "soft404")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _open(url, timeout, ctx):
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
                                               "Accept-Language": "en"})
    try:
        resp = opener.open(req, timeout=timeout)
        return resp.status, resp.headers, resp.read(49152), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4096)
        except Exception:
            body = b""
        return e.code, e.headers, body, None


def _soft(body, headers, url):
    ctype = (headers.get("Content-Type") or "").lower()
    if "html" not in ctype:
        return ""
    text = body.decode("utf-8", errors="ignore")
    m = TITLE.search(text)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    if title and SOFT_TITLE.search(title):
        return f"page title says '{title[:60]}'"
    visible = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    visible = re.sub(r"<[^>]+>", " ", visible)
    if len(visible) < 6000 and (p := PARKED.search(visible)):
        return f"looks like a parked/expired domain ('{p.group(0)}')"
    return ""


def _homepage_drop(orig, final):
    o, f = urllib.parse.urlsplit(orig), urllib.parse.urlsplit(final)
    return o.path.strip("/") != "" and f.path.strip("/") == "" and not f.query


class Checker:
    def __init__(self, timeout=15, workers=16, per_host=2, retries=1, max_redirects=8,
                 verify_tls=True, soft=True):
        self.timeout, self.workers, self.retries = timeout, workers, retries
        self.max_redirects, self.soft = max_redirects, soft
        self.ctx = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()
        self._sems, self._lock = {}, threading.Lock()
        self.per_host = per_host

    def _sem(self, host):
        with self._lock:
            return self._sems.setdefault(host, threading.Semaphore(self.per_host))

    def check_all(self, urls, progress=None):
        urls = list(dict.fromkeys(urls))
        out = {}

        def one(u):
            r = self.check(u)
            out[u] = r
            if progress:
                progress(len(out), len(urls))
        with ThreadPoolExecutor(self.workers) as ex:
            list(ex.map(one, urls))
        return out

    def check(self, url):
        host = urllib.parse.urlsplit(url).netloc
        with self._sem(host):
            return self._check(url)

    def _check(self, url):
        cur, chain, all_perm = url, [], True
        for _ in range(self.max_redirects + 1):
            res = self._fetch(cur)
            if isinstance(res, Result):
                res.url = url
                res.chain = chain
                return res
            status, headers, body = res
            if status in (301, 302, 303, 307, 308) and headers.get("Location"):
                nxt = urllib.parse.urldefrag(urllib.parse.urljoin(cur, headers["Location"]))[0]
                all_perm &= status in PERMANENT
                chain.append((status, nxt))
                cur = nxt
                continue
            return self._classify(url, cur, status, headers, body, chain, all_perm)
        return Result(url, "error", detail="too many redirects", chain=chain)

    def _fetch(self, url):
        for attempt in range(self.retries + 2):
            try:
                status, headers, body, _ = _open(url, self.timeout, self.ctx)
            except urllib.error.URLError as e:
                reason = e.reason
                if isinstance(reason, socket.gaierror):
                    return Result(url, "dead", detail="DNS lookup failed (domain gone?)")
                if isinstance(reason, ConnectionRefusedError):
                    return Result(url, "dead", detail="connection refused")
                if isinstance(reason, ssl.SSLError):
                    return Result(url, "error", detail=f"TLS problem: {reason}")
                err = f"{type(reason).__name__}: {reason}"
            except (TimeoutError, socket.timeout):
                err = "timeout"
            except (http.client.HTTPException, OSError, ValueError) as e:
                err = f"{type(e).__name__}: {e}"
            else:
                if status == 429 and attempt <= self.retries:
                    time.sleep(min(_retry_after(headers), 20))
                    continue
                if status >= 500 and attempt <= self.retries:
                    time.sleep(1.0)
                    continue
                return status, headers, body
            if attempt <= self.retries:
                time.sleep(0.5)
        return Result(url, "error", detail=err)

    def _classify(self, url, final, status, headers, body, chain, all_perm):
        if status in DEAD_STATUS:
            return Result(url, "dead", status, final, f"HTTP {status}", chain)
        if status in BLOCKED:
            why = "rate limited" if status == 429 else "blocks automated checks"
            return Result(url, "blocked", status, final, f"HTTP {status} ({why}), could not verify", chain)
        if status >= 400:
            verdict = "dead" if status < 500 and status != 405 else "error"
            return Result(url, verdict, status, final, f"HTTP {status}", chain)
        if self.soft:
            why = _soft(body, headers, final)
            if why:
                return Result(url, "soft404", status, final, why, chain)
        if chain:
            if _homepage_drop(url, final):
                return Result(url, "soft404", status, final, "redirects to the site's front page", chain)
            if all_perm and final != url:
                return Result(url, "moved", status, final, f"permanent redirect ({chain[0][0]})", chain)
            return Result(url, "redirected", status, final, f"temporary redirect ({chain[0][0]})", chain)
        return Result(url, "ok", status, final)


def _retry_after(headers):
    try:
        return max(1.0, float(headers.get("Retry-After", "")))
    except ValueError:
        return 3.0
