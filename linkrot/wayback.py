"""Find Wayback Machine snapshots, preferring the one closest to a date."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .check import UA

CDX = os.environ.get("LINKROT_CDX", "https://web.archive.org/cdx/search/cdx")
AVAIL = os.environ.get("LINKROT_AVAILABLE", "https://archive.org/wayback/available")
REPLAY = os.environ.get("LINKROT_REPLAY", "https://web.archive.org/web")


@dataclass
class Snapshot:
    url: str
    timestamp: str

    @property
    def date(self):
        t = self.timestamp
        return f"{t[:4]}-{t[4:6]}-{t[6:8]}"


def _get_json(url, timeout=30, tries=3):
    for i in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace").strip()
                return json.loads(body) if body else []
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and i < tries - 1:
                time.sleep(5 * (i + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            if i < tries - 1:
                time.sleep(2)
                continue
            return None
    return None


class Wayback:
    def __init__(self, cache_path=None, min_interval=1.0):
        self.cache_path = cache_path
        self.min_interval = min_interval
        self._last = 0.0
        self.cache = {}
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path) as fh:
                    self.cache = json.load(fh)
            except (OSError, ValueError):
                self.cache = {}

    def _throttle(self):
        wait = self._last + self.min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def closest(self, url, hint=None):
        """Snapshot with HTTP 200 closest to `hint` (YYYYMMDD), else the newest."""
        key = f"{url}|{hint or ''}"
        if key in self.cache:
            v = self.cache[key]
            return Snapshot(url, v) if v else None
        snap = self._cdx(url, hint) or self._available(url, hint)
        if snap is not None or not self._failed:
            self.cache[key] = snap.timestamp if snap else None
            self._save()
        return snap

    _failed = False

    def _cdx(self, url, hint):
        q = {"url": url, "output": "json", "filter": "statuscode:200", "limit": "1",
             "fastLatest": "true"}
        if hint:
            q.update(sort="closest", closest=hint)
            q.pop("fastLatest")
        else:
            q["limit"] = "-1"
        self._throttle()
        rows = _get_json(f"{CDX}?{urllib.parse.urlencode(q)}")
        self._failed = rows is None
        if not rows or len(rows) < 2:
            return None
        return Snapshot(url, rows[-1][1] if not hint else rows[1][1])

    def _available(self, url, hint):
        q = {"url": url}
        if hint:
            q["timestamp"] = hint
        self._throttle()
        data = _get_json(f"{AVAIL}?{urllib.parse.urlencode(q)}")
        snap = ((data or {}).get("archived_snapshots") or {}).get("closest") if isinstance(data, dict) else None
        if snap and snap.get("available") and str(snap.get("status")) == "200":
            self._failed = False
            return Snapshot(url, snap["timestamp"])
        return None

    def _save(self):
        if not self.cache_path:
            return
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w") as fh:
            json.dump(self.cache, fh)


def replay_url(snap):
    return f"{REPLAY}/{snap.timestamp}/{snap.url}"


def is_archive_url(url):
    host = urllib.parse.urlsplit(url).netloc.lower()
    return host in ("web.archive.org", "archive.org", "archive.ph", "archive.today", "webcache.googleusercontent.com")
