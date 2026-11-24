"""Appends a folder to the setup stub, producing the single-file Windows installer.

Format (read by windows/setup_stub.c), stored uncompressed after the stub:
    repeated:  u32 name length | UTF-8 name (forward slashes) | u64 size | bytes
    trailer:   u64 list offset | u64 list size | b"INTCPTM1"
"""

from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path

MAGIC = b"INTCPTM1"


def files_in(folder: Path) -> list[tuple[str, Path]]:
    out = []
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            name = path.relative_to(folder).as_posix()
            if ".." in name.split("/") or ":" in name or "\\" in name:
                raise ValueError(f"unsafe name {name!r}")
            out.append((name, path))
    return out


def pack(stub: Path, folder: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(stub, output)
    with open(output, "r+b") as fh:
        fh.seek(0, 2)
        offset = fh.tell()
        for name, path in files_in(folder):
            raw = name.encode("utf-8")
            fh.write(struct.pack("<I", len(raw)) + raw + struct.pack("<Q", path.stat().st_size))
            with open(path, "rb") as src:
                shutil.copyfileobj(src, fh, 1024 * 1024)
        size = fh.tell() - offset
        fh.write(struct.pack("<QQ", offset, size) + MAGIC)
    return output


def unpack(setup: Path, target: Path) -> list[str]:
    """Pure-Python reader (used by tests to check what a setup file contains)."""
    data = setup.read_bytes()
    if data[-8:] != MAGIC:
        raise ValueError("not an Interceptium setup file")
    offset, size = struct.unpack("<QQ", data[-24:-8])
    pos, end, names = offset, offset + size, []
    while pos < end:
        (n,) = struct.unpack_from("<I", data, pos)
        name = data[pos + 4:pos + 4 + n].decode("utf-8")
        (length,) = struct.unpack_from("<Q", data, pos + 4 + n)
        start = pos + 12 + n
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data[start:start + length])
        names.append(name)
        pos = start + length
    return names


if __name__ == "__main__":
    pack(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
