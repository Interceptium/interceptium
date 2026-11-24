"""Appends a folder to the setup stub, producing the single-file Windows installer.

Format (read by windows/setup_stub.c), stored after the stub:
    list:      repeated  u32 name length | UTF-8 name (forward slashes) | u64 size | bytes
    trailer:   u64 block offset | u64 block size | b"INTCPTM1"  (list stored as is)
                                                | b"INTCPTM2"  (list compressed with LZMS through
                                                               Windows' built-in Cabinet API)
"""

from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path

MAGIC = b"INTCPTM1"
MAGIC_LZMS = b"INTCPTM2"
LZMS = 5  # COMPRESS_ALGORITHM_LZMS


def _cabinet():
    import ctypes

    cab = ctypes.WinDLL("cabinet")
    handle = ctypes.c_void_p
    size = ctypes.c_size_t
    for name in ("CreateCompressor", "CreateDecompressor"):
        getattr(cab, name).argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(handle)]
    cab.Compress.argtypes = [handle, ctypes.c_void_p, size, ctypes.c_void_p, size, ctypes.POINTER(size)]
    cab.Decompress.argtypes = [handle, ctypes.c_void_p, size, ctypes.c_void_p, size, ctypes.POINTER(size)]
    cab.CloseCompressor.argtypes = [handle]
    cab.CloseDecompressor.argtypes = [handle]
    return ctypes, cab


def _lzms(data: bytes, compress: bool) -> bytes:
    """Compress or expand with Windows' own LZMS implementation (Windows only)."""
    ctypes, cab = _cabinet()
    h = ctypes.c_void_p()
    create, run, close = ((cab.CreateCompressor, cab.Compress, cab.CloseCompressor) if compress
                          else (cab.CreateDecompressor, cab.Decompress, cab.CloseDecompressor))
    if not create(LZMS, None, ctypes.byref(h)):
        raise OSError("Cabinet API unavailable")
    try:
        src = ctypes.create_string_buffer(data, len(data))
        needed = ctypes.c_size_t(0)
        run(h, src, len(data), None, 0, ctypes.byref(needed))
        out = ctypes.create_string_buffer(needed.value)
        if not run(h, src, len(data), out, needed.value, ctypes.byref(needed)):
            raise OSError("LZMS failed")
        return out.raw[:needed.value]
    finally:
        close(h)


def files_in(folder: Path) -> list[tuple[str, Path]]:
    out = []
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            name = path.relative_to(folder).as_posix()
            if ".." in name.split("/") or ":" in name or "\\" in name:
                raise ValueError(f"unsafe name {name!r}")
            out.append((name, path))
    return out


def file_list(folder: Path) -> bytes:
    parts = []
    for name, path in files_in(folder):
        raw = name.encode("utf-8")
        parts.append(struct.pack("<I", len(raw)) + raw + struct.pack("<Q", path.stat().st_size))
        parts.append(path.read_bytes())
    return b"".join(parts)


def pack(stub: Path, folder: Path, output: Path, compress: bool = False) -> Path:
    """Write stub + file list + trailer. `compress` uses Windows' LZMS (build on Windows)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(stub, output)
    block = file_list(folder)
    magic = MAGIC
    if compress:
        block, magic = _lzms(block, compress=True), MAGIC_LZMS
    with open(output, "ab") as fh:
        offset = fh.tell()
        fh.write(block)
        fh.write(struct.pack("<QQ", offset, len(block)) + magic)
    return output


def unpack(setup: Path, target: Path) -> list[str]:
    """Pure-Python reader (used by tests to check what a setup file contains)."""
    data = setup.read_bytes()
    if data[-8:] not in (MAGIC, MAGIC_LZMS):
        raise ValueError("not an Interceptium setup file")
    offset, size = struct.unpack("<QQ", data[-24:-8])
    block = data[offset:offset + size]
    if data[-8:] == MAGIC_LZMS:
        block = _lzms(block, compress=False)
    data, pos, end, names = block, 0, len(block), []
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
