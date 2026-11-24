"""Decides what happens to each Roblox asset request.

How Roblox fetches assets (observed protocol):
  1. The client POSTs a JSON list to ``assetdelivery.roblox.com/v1/assets/batch``.
     Each entry has a ``requestId`` and an ``assetId`` (plus type hints).
  2. The answer is a JSON list whose entries carry the same ``requestId`` and a
     ``location`` URL on a CDN host (e.g. ``fts.rbxcdn.com``).
  3. The client downloads the bytes from that CDN URL.
  Older code paths use ``assetdelivery.roblox.com/v1/asset/?id=<id>`` directly.

Rules act at the right step:
  * replace -> rewrite ``assetId`` in step 1 (or ``id=`` in the legacy URL).
  * remove  -> drop the entry in step 1 (legacy URL: answer 404).
  * file    -> remember the requestId in step 1, learn its CDN URL in step 2, and
               answer the download in step 3 with the local file's bytes.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .logbus import log
from .rules import RuleSet, type_of_entry

ASSET_HOST = "assetdelivery.roblox.com"
CDN_HOSTS = frozenset({"fts.rbxcdn.com", "contentdelivery.roblox.com"})
INTERCEPT_HOSTS = frozenset({ASSET_HOST, *CDN_HOSTS})
BATCH_PATH = "/v1/assets/batch"
ROUTE_TTL = 600.0  # seconds a learned CDN URL -> local file route is kept


def _as_id(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))


def read_local_file(path: str) -> bytes | None:
    p = Path(path).expanduser()
    try:
        if not p.is_file():
            return None
        return p.read_bytes()
    except OSError:
        return None


class AssetRouter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, float]] = {}  # requestId -> (file, time)
        self._routes: dict[str, tuple[str, float]] = {}  # cdn url -> (file, time)

    def _expire(self) -> None:
        cutoff = time.monotonic() - ROUTE_TTL
        self._pending = {k: v for k, v in self._pending.items() if v[1] >= cutoff}
        self._routes = {k: v for k, v in self._routes.items() if v[1] >= cutoff}

    # step 1 -------------------------------------------------------------
    def rewrite_batch_request(self, body: bytes, rules: RuleSet) -> bytes | None:
        """Return a new request body, or None if nothing needs changing."""
        if not rules:
            return None
        try:
            entries = json.loads(body)
        except ValueError:
            return None
        if not isinstance(entries, list):
            return None
        changed = False
        kept: list[object] = []
        now = time.monotonic()
        for entry in entries:
            if not isinstance(entry, dict):
                kept.append(entry)
                continue
            asset = _as_id(entry.get("assetId"))
            hit = rules.lookup(asset, type_of_entry(entry))
            if hit is None:
                kept.append(entry)
                continue
            action, value = hit
            if action == "remove":
                log.emit("Remove", f"asset {asset} removed")
                changed = True
                continue
            if action == "file":
                req_id = str(entry.get("requestId", ""))
                if req_id:
                    with self._lock:
                        self._pending[req_id] = (str(value), now)
                    log.emit("File", f"asset {asset} will be served from {Path(str(value)).name}")
            elif action == "replace" and value != asset:
                entry["assetId"] = value
                log.emit("Replace", f"asset {asset} -> {value}")
                changed = True
            kept.append(entry)
        if not changed:
            return None
        return json.dumps(kept, separators=(",", ":")).encode("utf-8")

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending)

    # step 2 -------------------------------------------------------------
    def learn_batch_response(self, body: bytes) -> None:
        with self._lock:
            if not self._pending:
                return
        try:
            entries = json.loads(body)
        except ValueError:
            return
        if not isinstance(entries, list):
            return
        now = time.monotonic()
        with self._lock:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                req_id = str(entry.get("requestId", ""))
                pending = self._pending.pop(req_id, None)
                if pending is None:
                    continue
                urls: list[str] = []
                if isinstance(entry.get("location"), str):
                    urls.append(entry["location"])
                for loc in entry.get("locations") or []:
                    if isinstance(loc, dict) and isinstance(loc.get("location"), str):
                        urls.append(loc["location"])
                for url in urls:
                    self._routes[_strip_query(url)] = (pending[0], now)
            self._expire()

    # step 3 -------------------------------------------------------------
    def cdn_file_for(self, host: str, path: str) -> str | None:
        key = _strip_query(f"https://{host}{path}")
        with self._lock:
            hit = self._routes.get(key)
        return hit[0] if hit else None

    # legacy single-asset endpoint ----------------------------------------
    @staticmethod
    def rewrite_single(target: str, rules: RuleSet) -> tuple[str, str, str | None]:
        """For ``/v1/asset?id=``: returns (action, new_target, file)."""
        parts = urlsplit(target)
        if not parts.path.rstrip("/").lower().endswith("/v1/asset") or not rules:
            return "pass", target, None
        query = parse_qsl(parts.query, keep_blank_values=True)
        for index, (key, value) in enumerate(query):
            if key.lower() not in ("id", "assetid"):
                continue
            asset = _as_id(value)
            hit = rules.lookup(asset)
            if asset is None or hit is None:
                continue
            action, new_value = hit
            if action == "remove":
                log.emit("Remove", f"asset {asset} removed")
                return "remove", target, None
            if action == "file":
                log.emit("File", f"asset {asset} served from {Path(str(new_value)).name}")
                return "file", target, str(new_value)
            if action == "replace":
                query[index] = (key, str(new_value))
                log.emit("Replace", f"asset {asset} -> {new_value}")
                new = urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))
                return "replace", new, None
        return "pass", target, None
