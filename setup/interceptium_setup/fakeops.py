"""Stand-in for Windows used by tests and when previewing the setup on other systems.

Everything happens inside a sandbox folder: "registry" values go to a JSON
file and "shortcuts" are small text files.
"""

from __future__ import annotations

import json
from pathlib import Path


class FakeOps:
    def __init__(self, sandbox: Path) -> None:
        self.sandbox = sandbox
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.registry_file = sandbox / "registry.json"
        self.calls: list[str] = []

    def stop_app(self) -> None:
        self.calls.append("stop_app")

    def remove_roblox_changes(self, app_exe: Path) -> None:
        self.calls.append(f"remove_roblox_changes:{app_exe.name}")

    def start_menu_dir(self) -> Path:
        return self.sandbox / "StartMenu"

    def desktop_dir(self) -> Path:
        return self.sandbox / "Desktop"

    def create_shortcut(self, link: Path, target: Path, icon: Path) -> None:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_text(f"{target}\n{icon}\n", encoding="utf-8")

    def write_uninstall_entry(self, values: dict[str, object]) -> None:
        self.registry_file.write_text(json.dumps(values), encoding="utf-8")

    def read_uninstall_entry(self) -> dict | None:
        try:
            return json.loads(self.registry_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def read_install_location(self) -> Path | None:
        entry = self.read_uninstall_entry()
        return Path(entry["InstallLocation"]) if entry and entry.get("InstallLocation") else None

    def delete_uninstall_entry(self) -> None:
        self.registry_file.unlink(missing_ok=True)

    def user_data_dir(self) -> Path:
        return self.sandbox / "LocalAppData" / "Interceptium"

    def launch(self, exe: Path) -> None:
        self.calls.append(f"launch:{exe.name}")
