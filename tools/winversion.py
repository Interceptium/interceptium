"""Writes a PyInstaller version-info file (shown in Windows file Properties)."""

from __future__ import annotations

from pathlib import Path

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v}), prodvers=({v}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Interceptium'),
      StringStruct('FileDescription', {desc!r}),
      StringStruct('FileVersion', {ver!r}),
      StringStruct('InternalName', {name!r}),
      StringStruct('OriginalFilename', {name!r} + '.exe'),
      StringStruct('ProductName', 'Interceptium'),
      StringStruct('ProductVersion', {ver!r}),
      StringStruct('LegalCopyright', 'MIT License')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write(path: Path, version: str, name: str, description: str) -> Path:
    parts = [int(p) for p in (version.split(".") + ["0", "0", "0", "0"])[:4]]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(v=", ".join(map(str, parts)), ver=version, name=name, desc=description),
                    encoding="utf-8")
    return path
