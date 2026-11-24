"""User settings and replacement rules, stored as plain JSON on disk."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import paths

ACTIONS = ("replace", "remove", "file")
DEFAULT_PORT = 58443


@dataclass
class Rule:
    """One replacement rule.

    action == "replace": every asset in ``assets`` is fetched as asset ``value`` instead.
    action == "remove":  every asset in ``assets`` is dropped from the request.
    action == "file":    every asset in ``assets`` is served from the local file ``value``.
    """

    name: str = "New rule"
    assets: list[int] = field(default_factory=list)
    action: str = "replace"
    value: str = ""
    enabled: bool = True

    def validate(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"unknown action {self.action!r}")
        if not self.assets:
            raise ValueError("a rule needs at least one asset ID")
        for asset in self.assets:
            if not isinstance(asset, int) or asset <= 0:
                raise ValueError(f"invalid asset ID {asset!r}")
        if self.action == "replace":
            if not str(self.value).isdigit() or int(self.value) <= 0:
                raise ValueError("replace rules need a numeric replacement asset ID")
        elif self.action == "file":
            if not self.value:
                raise ValueError("file rules need a local file path")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Rule":
        assets = [int(a) for a in raw.get("assets", []) if str(a).strip().isdigit()]
        rule = cls(
            name=str(raw.get("name", "Rule")),
            assets=assets,
            action=str(raw.get("action", "replace")),
            value=str(raw.get("value", "")),
            enabled=bool(raw.get("enabled", True)),
        )
        rule.validate()
        return rule


def parse_asset_list(text: str) -> list[int]:
    """Parse "123, 456 789" (commas, spaces or newlines) into asset IDs."""
    out: list[int] = []
    for token in text.replace(",", " ").split():
        token = token.strip()
        if not token.isdigit():
            raise ValueError(f"{token!r} is not a numeric asset ID")
        out.append(int(token))
    return out


@dataclass
class Settings:
    port: int = DEFAULT_PORT
    clear_cache_on_launch: bool = True
    auto_capture_launches: bool = True
    rules: list[Rule] = field(default_factory=list)


class ConfigStore:
    """Loads/saves settings. Thread-safe; the proxy reads a snapshot per request."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.config_file()
        self._lock = threading.Lock()
        self.settings = self._load()

    def _load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Settings()
        rules = []
        for item in raw.get("rules", []):
            try:
                rules.append(Rule.from_dict(item))
            except (ValueError, TypeError):
                continue  # skip a broken rule instead of refusing to start
        port = raw.get("port", DEFAULT_PORT)
        return Settings(
            port=port if isinstance(port, int) and 1024 <= port <= 65535 else DEFAULT_PORT,
            clear_cache_on_launch=bool(raw.get("clear_cache_on_launch", True)),
            auto_capture_launches=bool(raw.get("auto_capture_launches", True)),
            rules=rules,
        )

    def save(self) -> None:
        with self._lock:
            payload = asdict(self.settings)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def rules_snapshot(self) -> list[Rule]:
        with self._lock:
            return [Rule(**asdict(r)) for r in self.settings.rules if r.enabled]

    def set_rules(self, rules: list[Rule]) -> None:
        for rule in rules:
            rule.validate()
        with self._lock:
            self.settings.rules = rules
        self.save()
