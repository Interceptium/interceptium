"""The proxy server.

* Listens on 127.0.0.1 only - nothing on your network can reach it.
* Speaks the standard HTTP proxy protocol (CONNECT), so Roblox is pointed at it
  with the HTTPS_PROXY environment variable; no hosts-file or system changes.
* Only the Roblox asset hosts in ``router.INTERCEPT_HOSTS`` are decrypted.
  Every other CONNECT is a blind byte tunnel - Interceptium can't read it.
* Upstream connections always use the system's trusted CAs with full
  certificate and hostname verification.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
import threading
from typing import Callable

from . import http1
from .certs import CertAuthority
from .config import ConfigStore
from .http1 import Message, ProtocolError
from .logbus import log
from .router import ASSET_HOST, BATCH_PATH, CDN_HOSTS, INTERCEPT_HOSTS, AssetRouter, read_local_file

LISTEN_HOST = "127.0.0.1"
CONNECT_TIMEOUT = 15.0
IDLE_TIMEOUT = 120.0


def default_upstream_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_alpn_protocols(["http/1.1"])
    return ctx


def _split_host_port(authority: str, default_port: int) -> tuple[str, int]:
    authority = authority.strip()
    if authority.startswith("["):
        host, _, rest = authority[1:].partition("]")
        port = rest.lstrip(":")
    elif authority.count(":") == 1:
        host, _, port = authority.partition(":")
    else:
        host, port = authority, ""
    if not host:
        raise ProtocolError("missing host")
    if port and not port.isdigit():
        raise ProtocolError("bad port")
    return host.lower(), int(port) if port else default_port


async def _close(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    with contextlib.suppress(Exception):
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), 2)


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.IncompleteReadError):
        pass
    finally:
        with contextlib.suppress(Exception):
            if writer.can_write_eof():
                writer.write_eof()


class ProxyServer:
    def __init__(
        self,
        config: ConfigStore,
        ca: CertAuthority,
        router: AssetRouter | None = None,
        port: int | None = None,
        upstream_context: ssl.SSLContext | None = None,
        resolve: Callable[[str, int], tuple[str, int]] | None = None,
    ) -> None:
        self.config = config
        self.ca = ca
        self.router = router or AssetRouter()
        self.requested_port = port if port is not None else config.settings.port
        self.port = 0
        self.upstream_context = upstream_context or default_upstream_context()
        self._resolve = resolve or (lambda h, p: (h, p))
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> int:
        last_error: OSError | None = None
        candidates = [self.requested_port] if self.requested_port == 0 else [
            self.requested_port + i for i in range(10)
        ]
        for port in candidates:
            try:
                self._server = await asyncio.start_server(self._handle, LISTEN_HOST, port, limit=2**20)
                break
            except OSError as exc:
                last_error = exc
        if self._server is None:
            raise OSError(f"could not listen on {LISTEN_HOST}:{self.requested_port}: {last_error}")
        self.port = self._server.sockets[0].getsockname()[1]
        log.emit("Proxy", f"listening on http://{LISTEN_HOST}:{self.port}")
        return self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 3)
            self._server = None
            log.emit("Proxy", "stopped")

    # ------------------------------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(http1.read_head(reader), IDLE_TIMEOUT)
            if head is None:
                return
            if head.method == "CONNECT":
                await self._handle_connect(head, reader, writer)
            elif head.target.lower().startswith("http://"):
                await self._handle_plain_http(head, reader, writer)
            else:
                # Not a proxy request (e.g. a web page probing localhost): refuse.
                writer.write(http1.simple_response(400, "Bad Request", b"Interceptium is a proxy.\n"))
                await writer.drain()
        except (ProtocolError, asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except Exception as exc:  # never let one connection kill the server
            log.emit("Error", f"{type(exc).__name__}: {exc}")
        finally:
            await _close(writer)

    async def _handle_connect(
        self, head: Message, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        host, port = _split_host_port(head.target, 443)
        if host in INTERCEPT_HOSTS and port == 443:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await writer.start_tls(self.ca.server_context(host))
            await self._mitm_loop(host, reader, writer)
            return
        # Blind tunnel: bytes are passed through untouched and unread.
        try:
            up_host, up_port = self._resolve(host, port)
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(up_host, up_port), CONNECT_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError):
            writer.write(http1.simple_response(502, "Bad Gateway", b"upstream unreachable\n"))
            await writer.drain()
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        try:
            await asyncio.gather(_pump(reader, up_writer), _pump(up_reader, writer))
        finally:
            await _close(up_writer)

    async def _handle_plain_http(
        self, head: Message, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        rest = head.target[len("http://"):]
        authority, slash, path = rest.partition("/")
        host, port = _split_host_port(authority, 80)
        head.target = slash + path if slash else "/"
        head.headers.set("Connection", "close")
        head.headers.remove("proxy-connection")
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(*self._resolve(host, port)), CONNECT_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError):
            writer.write(http1.simple_response(502, "Bad Gateway", b"upstream unreachable\n"))
            await writer.drain()
            return
        try:
            up_writer.write(head.head_bytes())
            await http1.relay_body(reader, up_writer, head)
            await up_writer.drain()
            await _pump(up_reader, writer)
        finally:
            await _close(up_writer)

    # ------------------------------------------------------------------
    async def _open_upstream(self, host: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        up_host, up_port = self._resolve(host, 443)
        return await asyncio.wait_for(
            asyncio.open_connection(
                up_host, up_port, ssl=self.upstream_context, server_hostname=host, limit=2**20
            ),
            CONNECT_TIMEOUT,
        )

    async def _send_local(self, writer: asyncio.StreamWriter, path: str) -> bool:
        data = read_local_file(path)
        if data is None:
            log.emit("File", f"missing local file {path}")
            writer.write(http1.simple_response(404, "Not Found", b"replacement file missing\n"))
        else:
            writer.write(http1.simple_response(200, "OK", data, "application/octet-stream"))
        await writer.drain()
        return True

    async def _mitm_loop(self, host: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        up: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        try:
            while True:
                req = await asyncio.wait_for(http1.read_head(reader), IDLE_TIMEOUT)
                if req is None:
                    return
                if "100-continue" in req.headers.get("expect").lower():
                    req.headers.remove("expect")
                    writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                    await writer.drain()
                body = await http1.read_body(reader, req)
                rules = self.config.active_rules()
                path = req.target

                if host in CDN_HOSTS:
                    local = self.router.cdn_file_for(host, path)
                    if local:
                        await self._send_local(writer, local)
                        if req.wants_close():
                            return
                        continue

                is_batch = False
                if host == ASSET_HOST:
                    action, new_target, local = self.router.rewrite_single(path, rules)
                    if action == "remove":
                        writer.write(http1.simple_response(404, "Not Found", b"removed by Interceptium\n"))
                        await writer.drain()
                        continue
                    if action == "file" and local:
                        await self._send_local(writer, local)
                        continue
                    req.target = new_target
                    is_batch = req.method == "POST" and path.split("?", 1)[0].rstrip("/") == BATCH_PATH
                    if is_batch and rules:
                        plain = http1.decode_content(body, req.headers.get("content-encoding"))
                        if plain is not None:
                            new_body = self.router.rewrite_batch_request(plain, rules)
                            if new_body is not None:
                                body = new_body
                                http1.set_plain_body(req, body)
                        # Ask for an encoding we can always read back.
                        req.headers.set("Accept-Encoding", "gzip")

                if not req.headers.has("content-length") and body:
                    http1.set_plain_body(req, body)
                elif "chunked" in req.headers.get("transfer-encoding").lower():
                    req.headers.remove("transfer-encoding")
                    req.headers.set("Content-Length", str(len(body)))

                resp: Message | None = None
                for attempt in (1, 2):  # retry once if a reused connection went stale
                    fresh = up is None
                    if up is None:
                        try:
                            up = await self._open_upstream(host)
                        except (OSError, asyncio.TimeoutError, ssl.SSLError) as exc:
                            log.emit("Upstream", f"{host}: {exc}")
                            writer.write(http1.simple_response(502, "Bad Gateway", b"upstream failed\n"))
                            await writer.drain()
                            return
                    try:
                        up[1].write(req.head_bytes() + body)
                        await up[1].drain()
                        resp = await asyncio.wait_for(http1.read_head(up[0]), IDLE_TIMEOUT)
                        while resp is not None and 100 <= resp.status < 200 and resp.status != 101:
                            resp = await http1.read_head(up[0])
                    except (OSError, ProtocolError, asyncio.IncompleteReadError):
                        resp = None
                    if resp is not None or fresh or attempt == 2:
                        break
                    await _close(up[1])
                    up = None
                if resp is None or up is None:
                    writer.write(http1.simple_response(502, "Bad Gateway", b"no upstream response\n"))
                    await writer.drain()
                    return

                reusable = True
                if is_batch and self.router.has_pending():
                    resp_body = await http1.read_body(up[0], resp, req.method)
                    plain = http1.decode_content(resp_body, resp.headers.get("content-encoding"))
                    if plain is not None:
                        self.router.learn_batch_response(plain)
                    if "chunked" in resp.headers.get("transfer-encoding").lower() or not resp.headers.has(
                        "content-length"
                    ):
                        resp.headers.remove("transfer-encoding")
                        resp.headers.set("Content-Length", str(len(resp_body)))
                    writer.write(resp.head_bytes() + resp_body)
                    await writer.drain()
                else:
                    writer.write(resp.head_bytes())
                    reusable = await http1.relay_body(up[0], writer, resp, req.method)
                    await writer.drain()

                if resp.wants_close() or not reusable:
                    await _close(up[1])
                    up = None
                    if not reusable:
                        return
                if req.wants_close():
                    return
        finally:
            if up is not None:
                await _close(up[1])


class ProxyThread:
    """Runs a ProxyServer on its own event loop thread (used by GUI and CLI)."""

    def __init__(self, server: ProxyServer) -> None:
        self.server = server
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.error: BaseException | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self.error is None

    def start(self, timeout: float = 10.0) -> int:
        self._ready.clear()
        self.error = None
        self._thread = threading.Thread(target=self._run, name="interceptium-proxy", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self.error is not None:
            raise self.error
        return self.server.port

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self.server.start())
        except BaseException as exc:  # report bind errors to the caller
            self.error = exc
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(self.server.stop())
            loop.close()

    def stop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(5)
        self._thread = None
        self._loop = None
