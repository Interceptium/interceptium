"""Replacement rules in the Fleasion-compatible JSON shape.

A config file looks like::

    {"replacement_rules": [
        {"name": "Sky", "enabled": true, "replace_ids": ["123", "Image"],
         "mode": "id", "with_id": 456},
        {"type": "group", "name": "Folder", "children": [ ...rules... ]}
    ]}

``mode`` is one of:
  * ``id``     - fetch asset ``with_id`` instead (empty ``with_id`` = remove)
  * ``remove`` - drop the asset
  * ``local``  - serve the file at ``local_path`` (empty = remove)
  * ``cdn``    - fetch from ``cdn_url``. **Not supported on purpose**: it would
                 make Roblox talk to a third-party server, so such rules are
                 skipped and reported.
``replace_ids`` entries are asset IDs or asset-type names such as ``"Image"``.
Unknown keys are preserved when files are saved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

MODES = ("id", "remove", "local", "cdn")
MODE_LABELS = {
    "id": "Replace",
    "remove": "Remove",
    "local": "Local file",
    "cdn": "CDN",
}

ASSET_TYPES: dict[str, int] = {
    "image": 1, "tshirt": 2, "audio": 3, "mesh": 4, "lua": 5, "html": 6, "text": 7, "hat": 8,
    "place": 9, "model": 10, "shirt": 11, "pants": 12, "decal": 13, "avatar": 16, "head": 17,
    "face": 18, "gear": 19, "badge": 21, "groupemblem": 22, "animation": 24, "arms": 25,
    "legs": 26, "torso": 27, "rightarm": 28, "leftarm": 29, "leftleg": 30, "rightleg": 31,
    "package": 32, "youtubevideo": 33, "gamepass": 34, "app": 35, "code": 37, "plugin": 38,
    "solidmodel": 39, "meshpart": 40, "hairaccessory": 41, "faceaccessory": 42,
    "neckaccessory": 43, "shoulderaccessory": 44, "frontaccessory": 45, "backaccessory": 46,
    "waistaccessory": 47, "climbanimation": 48, "deathanimation": 49, "fallanimation": 50,
    "idleanimation": 51, "jumpanimation": 52, "runanimation": 53, "swimanimation": 54,
    "walkanimation": 55, "poseanimation": 56, "earaccessory": 57, "eyeaccessory": 58,
    "localizationtablemanifest": 59, "emoteanimation": 61, "video": 62, "texturepack": 63,
    "tshirtaccessory": 64, "shirtaccessory": 65, "pantsaccessory": 66, "jacketaccessory": 67,
    "sweateraccessory": 68, "shortsaccessory": 69, "leftshoeaccessory": 70,
    "rightshoeaccessory": 71, "dressskirtaccessory": 72, "fontfamily": 73, "fontface": 74,
    "meshhiddensurfaceremoval": 75, "eyebrowaccessory": 76, "eyelashaccessory": 77,
    "moodanimation": 78, "dynamichead": 79, "codesnippet": 80,
}
TYPE_ID_MAX = max(ASSET_TYPES.values())  # bare numbers <= this are type IDs, not assets
TYPE_NAMES = {v: k for k, v in ASSET_TYPES.items()}


def iter_rules(entries: Iterable[object]) -> Iterator[dict]:
    """Yield rule dicts depth-first, walking into groups."""
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "group":
            yield from iter_rules(entry.get("children") or [])
        else:
            yield entry


def rule_mode(rule: dict) -> str:
    if "mode" not in rule and "remove" in rule:
        return "remove" if rule.get("remove") else "id"
    mode = str(rule.get("mode") or "id").lower()
    return mode if mode in MODES else "id"


def parse_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_targets(values: Iterable[object]) -> tuple[set[int], set[int]]:
    """Split ``replace_ids`` into (asset IDs, asset type IDs). Unsupported keys are ignored."""
    ids: set[int] = set()
    types: set[int] = set()
    for value in values or []:
        number = parse_id(value)
        if number is not None:
            if number > TYPE_ID_MAX:
                ids.add(number)
            continue
        if isinstance(value, str):
            type_id = ASSET_TYPES.get(value.strip().lower().replace(" ", ""))
            if type_id is not None:
                types.add(type_id)
    return ids, types


def type_of_entry(entry: dict) -> int | None:
    type_id = parse_id(entry.get("assetTypeId"))
    if type_id is not None:
        return type_id
    name = entry.get("assetType")
    if isinstance(name, str):
        return ASSET_TYPES.get(name.strip().lower())
    return None


Action = tuple[str, object]  # ("replace", int) | ("remove", None) | ("file", str)


@dataclass
class RuleSet:
    by_id: dict[int, Action] = field(default_factory=dict)
    by_type: dict[int, Action] = field(default_factory=dict)
    skipped_cdn: int = 0

    def __bool__(self) -> bool:
        return bool(self.by_id or self.by_type)

    def lookup(self, asset_id: int | None, type_id: int | None = None) -> Action | None:
        if asset_id is not None and asset_id in self.by_id:
            return self.by_id[asset_id]
        if type_id is not None and type_id in self.by_type:
            return self.by_type[type_id]
        return None


def compile_rules(rules: Iterable[dict], resolve_local: Callable[[str], str] = str) -> RuleSet:
    """Turn enabled rule dicts into a fast lookup. Later rules win, like Fleasion."""
    out = RuleSet()
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        ids, types = parse_targets(rule.get("replace_ids") or [])
        if not ids and not types:
            continue
        mode = rule_mode(rule)
        action: Action | None
        if mode == "remove":
            action = ("remove", None)
        elif mode == "local":
            path = str(rule.get("local_path") or "").strip()
            action = ("file", resolve_local(path)) if path else ("remove", None)
        elif mode == "cdn":
            if str(rule.get("cdn_url") or "").strip():
                out.skipped_cdn += 1
                continue  # never send Roblox to third-party servers
            action = ("remove", None)
        else:
            raw = rule.get("with_id")
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                action = ("remove", None)
            else:
                target = parse_id(raw)
                if target is None or target <= TYPE_ID_MAX:
                    continue
                action = ("replace", target)
        for asset in ids:
            out.by_id[asset] = action
        for type_id in types:
            out.by_type[type_id] = action
    return out


def new_rule(name: str, targets: list[str], mode: str, value: str = "", enabled: bool = True) -> dict:
    rule: dict = {"name": name, "enabled": enabled, "replace_ids": targets, "mode": mode}
    if mode == "id":
        rule["with_id"] = int(value) if value.strip().isdigit() else None
    elif mode == "local":
        rule["local_path"] = value
    elif mode == "cdn":
        rule["cdn_url"] = value
    return rule


def parse_target_text(text: str) -> list[str]:
    """"123, 456 Image" -> ["123", "456", "Image"]; raises ValueError on junk."""
    out: list[str] = []
    for token in text.replace(",", " ").split():
        if token.isdigit():
            if int(token) <= TYPE_ID_MAX:
                raise ValueError(f"{token} is too small to be an asset ID")
            out.append(token)
        elif token.lower() in ASSET_TYPES:
            out.append(token)
        else:
            raise ValueError(f"{token!r} is not an asset ID or asset type name")
    return out


def describe(rule: dict) -> str:
    """Short detail shown next to the action badge."""
    mode = rule_mode(rule)
    if mode == "id":
        target = rule.get("with_id")
        return str(target) if target not in (None, "") else ""
    if mode == "local":
        return str(rule.get("local_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
    if mode == "cdn":
        return "Not supported"
    return ""
