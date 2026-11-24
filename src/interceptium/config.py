"""Settings plus a library of rule configs (one JSON file per config).

Layout inside the per-user data folder::

    settings.json        port, options and "enabled_configs"
    configs/<Name>.json  {"replacement_rules": [...]}  (Fleasion-compatible)

Any number of configs can be enabled at once; their rules are combined in
the order they are listed in ``enabled_configs``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .rules import RuleSet, compile_rules, iter_rules

DEFAULT_PORT = 58443
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_CONFIG_BYTES = 32 * 1024 * 1024


def decode_json_bytes(raw: bytes) -> Any:
    """Parse JSON that may be UTF-8 (with/without BOM) or UTF-16/32."""
    for encoding in ("utf-8-sig", "utf-16", "utf-32", "cp1252"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, ValueError):
            continue
    raise ValueError("not a JSON file")


def normalize_config(data: Any) -> dict:
    if isinstance(data, dict):
        rules = data.get("replacement_rules")
        if not isinstance(rules, list):
            data = dict(data)
            data["replacement_rules"] = []
        return data
    if isinstance(data, list):
        return {"replacement_rules": data}
    return {"replacement_rules": []}


def safe_config_name(name: str) -> str:
    name = _BAD_NAME.sub("_", name).strip().strip(".")
    if not name:
        raise ValueError("config name is empty")
    return name[:80]


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class Settings:
    port: int = DEFAULT_PORT
    clear_cache_on_launch: bool = True
    auto_capture_launches: bool = True
    enabled_configs: list[str] = field(default_factory=list)


class ConfigStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or paths.data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.folder = self.root / "configs"
        self.folder.mkdir(exist_ok=True)
        self.settings_path = self.root / "settings.json"
        self._lock = threading.RLock()
        self._cache_key: tuple | None = None
        self._cache: RuleSet = RuleSet()
        self.settings = self._load_settings()
        if not self.names():
            self.save_config("Default", {"replacement_rules": []})
            self.settings.enabled_configs = ["Default"]
            self.save()

    # -- settings ----------------------------------------------------------
    def _load_settings(self) -> Settings:
        try:
            raw = decode_json_bytes(self.settings_path.read_bytes())
        except (OSError, ValueError):
            return Settings()
        if not isinstance(raw, dict):
            return Settings()
        port = raw.get("port", DEFAULT_PORT)
        enabled = raw.get("enabled_configs") or []
        return Settings(
            port=port if isinstance(port, int) and 1024 <= port <= 65535 else DEFAULT_PORT,
            clear_cache_on_launch=bool(raw.get("clear_cache_on_launch", True)),
            auto_capture_launches=bool(raw.get("auto_capture_launches", True)),
            enabled_configs=[str(n) for n in enabled if isinstance(n, str)],
        )

    def save(self) -> None:
        with self._lock:
            s = self.settings
            _atomic_write_json(self.settings_path, {
                "port": s.port,
                "clear_cache_on_launch": s.clear_cache_on_launch,
                "auto_capture_launches": s.auto_capture_launches,
                "enabled_configs": s.enabled_configs,
            })

    # -- config files ------------------------------------------------------
    def path_for(self, name: str) -> Path:
        return self.folder / f"{safe_config_name(name)}.json"

    def names(self) -> list[str]:
        return sorted((p.stem for p in self.folder.glob("*.json")), key=str.lower)

    def load_config(self, name: str) -> dict:
        try:
            raw = self.path_for(name).read_bytes()
            if len(raw) > MAX_CONFIG_BYTES:
                return {"replacement_rules": []}
            return normalize_config(decode_json_bytes(raw))
        except (OSError, ValueError):
            return {"replacement_rules": []}

    def save_config(self, name: str, data: dict) -> None:
        with self._lock:
            _atomic_write_json(self.path_for(name), normalize_config(data))
            self._cache_key = None

    def is_enabled(self, name: str) -> bool:
        return name in self.settings.enabled_configs

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            current = [n for n in self.settings.enabled_configs if n != name]
            if enabled:
                current.append(name)
            self.settings.enabled_configs = current
            self._cache_key = None
        self.save()

    def create(self, name: str) -> str:
        name = safe_config_name(name)
        if self.path_for(name).exists():
            raise ValueError(f"a config named {name!r} already exists")
        self.save_config(name, {"replacement_rules": []})
        return name

    def _unique(self, name: str) -> str:
        base, n = safe_config_name(name), 2
        candidate = base
        while self.path_for(candidate).exists():
            candidate = f"{base} ({n})"
            n += 1
        return candidate

    def rename(self, old: str, new: str) -> str:
        new = safe_config_name(new)
        if new == old:
            return new
        if self.path_for(new).exists():
            raise ValueError(f"a config named {new!r} already exists")
        with self._lock:
            os.replace(self.path_for(old), self.path_for(new))
            self.settings.enabled_configs = [new if n == old else n for n in self.settings.enabled_configs]
            self._cache_key = None
        self.save()
        return new

    def delete(self, name: str) -> None:
        with self._lock:
            self.path_for(name).unlink(missing_ok=True)
            self.settings.enabled_configs = [n for n in self.settings.enabled_configs if n != name]
            self._cache_key = None
        self.save()

    def import_file(self, source: Path) -> str:
        """Copy a (Fleasion or Interceptium) config into the library. Returns its name."""
        raw = Path(source).read_bytes()
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError("file is too large to be a config")
        data = decode_json_bytes(raw)
        if not isinstance(data, (dict, list)):
            raise ValueError("not a rule config")
        name = self._unique(Path(source).stem)
        self.save_config(name, normalize_config(data))
        return name

    def export(self, name: str, dest: Path) -> None:
        shutil.copyfile(self.path_for(name), dest)

    # -- local file paths ----------------------------------------------------
    def resolve_local(self, value: str) -> str:
        """Fleasion "/Folder/file.png" paths live inside the configs folder."""
        text = value.strip()
        if text.startswith("/") and not text.startswith("//") and "\\" not in text:
            parts = text[1:].split("/")
            if len(parts) >= 2 and not any(p in ("", ".", "..") for p in parts):
                candidate = self.folder.joinpath(*parts)
                if candidate.is_file() or not Path(text).is_file():
                    return str(candidate)
        return str(Path(text).expanduser())

    # -- what the proxy uses -----------------------------------------------
    def active_rules(self) -> RuleSet:
        """Compiled rules from all enabled configs (re-read when files change)."""
        with self._lock:
            key_parts = []
            for name in self.settings.enabled_configs:
                try:
                    st = self.path_for(name).stat()
                    key_parts.append((name, st.st_mtime_ns, st.st_size))
                except (OSError, ValueError):
                    key_parts.append((name, None, None))
            key = tuple(key_parts)
            if key != self._cache_key:
                combined: list[dict] = []
                for name in self.settings.enabled_configs:
                    combined.extend(iter_rules(self.load_config(name)["replacement_rules"]))
                self._cache = compile_rules(combined, self.resolve_local)
                self._cache_key = key
            return self._cache
