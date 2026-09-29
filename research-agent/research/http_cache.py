"""HTTP access with a disk cache.

Modes
  live    - always hit the network, never write the cache
  record  - hit the network and save every response (use while preparing the demo)
  replay  - serve only from cache; a miss raises CacheMiss (use on stage)

The fetcher is injectable so the same code can run on top of Flower's web_fetch
connector if the AgentApp runtime does not allow direct outbound HTTP.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional, Tuple

Fetcher = Callable[[str], Tuple[int, str]]  # url -> (status_code, body_text)
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


class CacheMiss(RuntimeError):
    pass


def urllib_fetcher(url: str, timeout: float = 20.0) -> Tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "CohortGuard-hackathon/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace") if e.fp else ""


class Http:
    def __init__(self, mode: str = "live", cache_dir: str | os.PathLike[str] | None = None,
                 fetcher: Optional[Fetcher] = None, min_interval: float = 0.4):
        assert mode in {"live", "record", "replay"}
        self.mode = mode
        self.cache_dir = os.fspath(cache_dir or DEFAULT_CACHE_DIR)
        self.fetcher = fetcher or urllib_fetcher
        self.min_interval = min_interval  # NCBI allows ~3 req/s without an API key
        self._last = 0.0
        os.makedirs(self.cache_dir, exist_ok=True)

    @staticmethod
    def build_url(base: str, params: Optional[dict] = None) -> str:
        if not params:
            return base
        clean = {k: v for k, v in params.items() if v is not None}
        return base + "?" + urllib.parse.urlencode(clean, doseq=True)

    def _path(self, url: str) -> str:
        return os.path.join(self.cache_dir, hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")

    def get(self, base: str, params: Optional[dict] = None) -> Tuple[int, str]:
        url = self.build_url(base, params)
        path = self._path(url)
        if self.mode == "replay":
            if not os.path.exists(path):
                raise CacheMiss(url)
            with open(path) as f:
                d = json.load(f)
            return d["status"], d["body"]
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        status, body = self.fetcher(url)
        self._last = time.time()
        if self.mode == "record":
            with open(path, "w") as f:
                json.dump({"url": url, "status": status, "body": body}, f)
        return status, body

    def get_json(self, base: str, params: Optional[dict] = None) -> Tuple[int, Optional[dict]]:
        status, body = self.get(base, params)
        if status != 200:
            return status, None
        try:
            return status, json.loads(body)
        except json.JSONDecodeError:
            return status, None
