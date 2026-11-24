"""Draws the placeholder Interceptium logo with the standard library only.

Writes assets/logo.png (256px) and assets/logo.ico. To use your own logo,
replace assets/logo.png and assets/logo.ico (or run with --from-png FILE to
wrap an existing 256x256 PNG into an .ico).
"""

from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def png_bytes(width: int, height: int, rgba: bytearray) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + bytes(rgba[y * width * 4:(y + 1) * width * 4]) for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def ico_bytes(png: bytes, size: int = 256) -> bytes:
    dim = 0 if size >= 256 else size
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), 6 + 16)
    return header + entry + png


def icns_bytes(pngs: dict[str, bytes]) -> bytes:
    """Apple icon container holding PNG images (types ic07=128px, ic08=256px)."""
    body = b"".join(kind.encode("ascii") + struct.pack(">I", len(data) + 8) + data for kind, data in pngs.items())
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def draw(size: int = 256, ss: int = 3) -> bytearray:
    bg = (18, 20, 30)
    ring = (47, 211, 199)
    accent = (255, 255, 255)
    out = bytearray(size * size * 4)
    c = size / 2
    radius_box = size * 0.22
    for y in range(size):
        for x in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    px = x + (sx + 0.5) / ss
                    py = y + (sy + 0.5) / ss
                    # rounded square mask
                    dx = max(abs(px - c) - (c - radius_box), 0)
                    dy = max(abs(py - c) - (c - radius_box), 0)
                    if dx * dx + dy * dy > radius_box * radius_box:
                        continue
                    color = bg
                    r = math.hypot(px - c, py - c)
                    angle = math.degrees(math.atan2(py - c, px - c))
                    in_gap = abs(angle) < 28 or abs(abs(angle) - 180) < 28
                    if size * 0.27 < r < size * 0.36 and not in_gap:
                        color = ring
                    if abs(py - c) < size * 0.035 and size * 0.12 < px < size * 0.88:
                        color = ring
                    if r < size * 0.09:
                        color = accent
                    acc[0] += color[0]
                    acc[1] += color[1]
                    acc[2] += color[2]
                    acc[3] += 255
            n = ss * ss
            i = (y * size + x) * 4
            a = acc[3] / n
            if a:
                out[i:i + 4] = bytes((int(acc[0] / (acc[3] / 255)), int(acc[1] / (acc[3] / 255)),
                                      int(acc[2] / (acc[3] / 255)), int(a)))
    return out


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    if len(sys.argv) == 3 and sys.argv[1] == "--from-png":
        png = Path(sys.argv[2]).read_bytes()
    else:
        png = png_bytes(256, 256, draw())
        (ASSETS / "logo.png").write_bytes(png)
        for size in (48, 96):
            (ASSETS / f"logo_{size}.png").write_bytes(png_bytes(size, size, draw(size, 4)))
    (ASSETS / "logo.ico").write_bytes(ico_bytes(png))
    small = png_bytes(128, 128, draw(128, 4))
    (ASSETS / "logo.icns").write_bytes(icns_bytes({"ic07": small, "ic08": png}))
    print("wrote", ASSETS)


if __name__ == "__main__":
    main()
