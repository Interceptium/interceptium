"""Minimal, strict HTTP/1.1 message handling on asyncio streams."""

from __future__ import annotations

import asyncio
import gzip
import zlib

try:  # Python 3.14+ ships zstd in the standard library.
    from compression import zstd as _zstd  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - depends on interpreter version
    _zstd = None

MAX_HEAD = 64 * 1024
MAX_BUFFERED_BODY = 64 * 1024 * 1024
COPY_CHUNK = 64 * 1024


class ProtocolError(Exception):
    pass


class Headers:
    """Ordered, case-insensitive header list that preserves original spelling."""

    def __init__(self, items: list[tuple[str, str]] | None = None) -> None:
        self.items: list[tuple[str, str]] = list(items or [])

    def get(self, name: str, default: str = "") -> str:
        low = name.lower()
        for k, v in self.items:
            if k.lower() == low:
                return v
        return default

    def has(self, name: str) -> bool:
        low = name.lower()
        return any(k.lower() == low for k, _ in self.items)

    def remove(self, name: str) -> None:
        low = name.lower()
        self.items = [(k, v) for k, v in self.items if k.lower() != low]

    def set(self, name: str, value: str) -> None:
        self.remove(name)
        self.items.append((name, value))

    def encode(self) -> bytes:
        return b"".join(f"{k}: {v}\r\n".encode("latin-1") for k, v in self.items)


class Message:
    def __init__(self, start_line: str, headers: Headers) -> None:
        self.start_line = start_line
        self.headers = headers

    # request helpers
    @property
    def method(self) -> str:
        return self.start_line.split(" ", 1)[0].upper()

    @property
    def target(self) -> str:
        parts = self.start_line.split(" ")
        return parts[1] if len(parts) > 1 else "/"

    @target.setter
    def target(self, value: str) -> None:
        parts = self.start_line.split(" ")
        parts[1] = value
        self.start_line = " ".join(parts)

    # response helpers
    @property
    def status(self) -> int:
        parts = self.start_line.split(" ")
        try:
            return int(parts[1])
        except (IndexError, ValueError):
            return 0

    def head_bytes(self) -> bytes:
        return self.start_line.encode("latin-1") + b"\r\n" + self.headers.encode() + b"\r\n"

    def wants_close(self) -> bool:
        conn = self.headers.get("connection").lower()
        if "close" in conn:
            return True
        return self.start_line.upper().endswith("HTTP/1.0") and "keep-alive" not in conn


async def read_head(reader: asyncio.StreamReader) -> Message | None:
    """Read a request/response head. Returns None on a clean EOF."""
    try:
        raw = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError as exc:
        if not exc.partial.strip():
            return None
        raise ProtocolError("connection closed mid-header") from exc
    except asyncio.LimitOverrunError as exc:
        raise ProtocolError("header too large") from exc
    if len(raw) > MAX_HEAD:
        raise ProtocolError("header too large")
    lines = raw.decode("latin-1").split("\r\n")
    start = lines[0]
    items: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise ProtocolError(f"bad header line {line[:40]!r}")
        k, v = line.split(":", 1)
        items.append((k.strip(), v.strip()))
    return Message(start, Headers(items))


def _has_body(msg: Message, request_method: str | None) -> bool:
    if request_method is None:  # it's a request
        return msg.headers.has("content-length") or "chunked" in msg.headers.get("transfer-encoding").lower()
    if request_method == "HEAD" or msg.status in (204, 304) or 100 <= msg.status < 200:
        return False
    return True


def _content_length(msg: Message) -> int | None:
    value = msg.headers.get("content-length")
    if not value:
        return None
    if not value.isdigit():
        raise ProtocolError("bad content-length")
    return int(value)


async def read_body(reader: asyncio.StreamReader, msg: Message, request_method: str | None = None) -> bytes:
    """Read and fully decode the framing of a body into memory."""
    if not _has_body(msg, request_method):
        return b""
    if "chunked" in msg.headers.get("transfer-encoding").lower():
        out = bytearray()
        while True:
            size_line = await reader.readuntil(b"\r\n")
            size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
            if size == 0:
                while (await reader.readuntil(b"\r\n")) != b"\r\n":
                    pass  # discard trailers
                return bytes(out)
            if len(out) + size > MAX_BUFFERED_BODY:
                raise ProtocolError("body too large")
            out += await reader.readexactly(size)
            await reader.readexactly(2)
    length = _content_length(msg)
    if length is not None:
        if length > MAX_BUFFERED_BODY:
            raise ProtocolError("body too large")
        return await reader.readexactly(length)
    if request_method is None:
        return b""
    data = await reader.read(MAX_BUFFERED_BODY + 1)
    chunks = [data]
    while data:
        data = await reader.read(COPY_CHUNK)
        chunks.append(data)
        if sum(len(c) for c in chunks) > MAX_BUFFERED_BODY:
            raise ProtocolError("body too large")
    return b"".join(chunks)


async def relay_body(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    msg: Message,
    request_method: str | None = None,
) -> bool:
    """Copy a body unchanged. Returns False if the body ran until EOF (no reuse)."""
    if not _has_body(msg, request_method):
        return True
    if "chunked" in msg.headers.get("transfer-encoding").lower():
        while True:
            size_line = await reader.readuntil(b"\r\n")
            writer.write(size_line)
            size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
            if size == 0:
                while True:
                    trailer = await reader.readuntil(b"\r\n")
                    writer.write(trailer)
                    if trailer == b"\r\n":
                        await writer.drain()
                        return True
            remaining = size + 2
            while remaining:
                part = await reader.readexactly(min(remaining, COPY_CHUNK))
                writer.write(part)
                remaining -= len(part)
                await writer.drain()
    length = _content_length(msg)
    if length is not None:
        remaining = length
        while remaining:
            part = await reader.readexactly(min(remaining, COPY_CHUNK))
            writer.write(part)
            remaining -= len(part)
            await writer.drain()
        return True
    if request_method is None:
        return True
    while True:
        part = await reader.read(COPY_CHUNK)
        if not part:
            return False
        writer.write(part)
        await writer.drain()


def set_plain_body(msg: Message, body: bytes) -> None:
    """Make ``msg`` describe an uncompressed, fixed-length body."""
    msg.headers.remove("transfer-encoding")
    msg.headers.remove("content-encoding")
    msg.headers.set("Content-Length", str(len(body)))


def decode_content(body: bytes, encoding: str) -> bytes | None:
    """Undo Content-Encoding. Returns None if we can't (caller passes through)."""
    enc = encoding.strip().lower()
    try:
        if enc in ("", "identity"):
            return body
        if enc in ("gzip", "x-gzip"):
            return gzip.decompress(body)
        if enc == "deflate":
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
        if enc == "zstd" and _zstd is not None:
            return _zstd.decompress(body)
    except Exception:
        return None
    return None


def simple_response(status: int, reason: str, body: bytes = b"", content_type: str = "text/plain") -> bytes:
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\n"
        "\r\n"
    )
    return head.encode("latin-1") + body
