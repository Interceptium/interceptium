"""End-to-end tests: client -> Interceptium -> fake Roblox servers, all over real TLS."""

from __future__ import annotations

import asyncio
import gzip
import json
import ssl
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from interceptium import http1  # noqa: E402
from interceptium.certs import CertAuthority  # noqa: E402
from interceptium.config import ConfigStore, Rule  # noqa: E402
from interceptium.proxy import ProxyServer  # noqa: E402

CDN_BYTES = b"ORIGINAL-ASSET-BYTES"


class FakeRoblox:
    """Plays assetdelivery.roblox.com and fts.rbxcdn.com on one TLS port."""

    def __init__(self, ca: CertAuthority) -> None:
        self.ca = ca
        self.seen_batches: list[list[dict]] = []
        self.seen_single: list[str] = []

    def _sni(self, sslobj: ssl.SSLObject, name: str | None, _ctx: ssl.SSLContext) -> None:
        sslobj.context = self.ca.server_context(name or "assetdelivery.roblox.com")

    async def start(self) -> int:
        ctx = self.ca.server_context("assetdelivery.roblox.com")
        ctx.sni_callback = self._sni  # pick the right certificate per hostname
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=ctx)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                req = await http1.read_head(reader)
                if req is None:
                    break
                body = await http1.read_body(reader, req)
                host = req.headers.get("host")
                if req.target.startswith("/v1/assets/batch"):
                    items = json.loads(body)
                    self.seen_batches.append(items)
                    out = [
                        {"requestId": i["requestId"], "location": f"https://fts.rbxcdn.com/asset/{i['assetId']}?sig=x"}
                        for i in items
                    ]
                    payload = gzip.compress(json.dumps(out).encode())
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Encoding: gzip\r\n"
                        + f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload
                    )
                elif req.target.startswith("/v1/asset"):
                    self.seen_single.append(req.target)
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nsingle")
                elif host == "fts.rbxcdn.com":
                    # chunked on purpose, to exercise relaying
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                        + f"{len(CDN_BYTES):x}\r\n".encode() + CDN_BYTES + b"\r\n0\r\n\r\n"
                    )
                else:
                    writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
            pass
        finally:
            writer.close()


class Client:
    def __init__(self, proxy_port: int, trust_pem: str) -> None:
        self.port = proxy_port
        self.ctx = ssl.create_default_context(cadata=trust_pem)

    async def connect(self, host: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
        await writer.drain()
        head = await http1.read_head(reader)
        assert head is not None and head.status == 200, head and head.start_line
        await writer.start_tls(self.ctx, server_hostname=host)
        return reader, writer

    @staticmethod
    async def request(reader, writer, method: str, host: str, target: str, body: bytes = b"", extra: str = ""):
        writer.write(
            f"{method} {target} HTTP/1.1\r\nHost: {host}\r\nContent-Length: {len(body)}\r\n{extra}\r\n".encode() + body
        )
        await writer.drain()
        resp = await http1.read_head(reader)
        data = await http1.read_body(reader, resp, method)
        return resp, data


class EndToEnd(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.upstream_ca = CertAuthority(tmp / "upstream-ca")
        self.fake = FakeRoblox(self.upstream_ca)
        up_port = await self.fake.start()

        self.local_file = tmp / "replacement.bin"
        self.local_file.write_bytes(b"MY-LOCAL-FILE")
        self.config = ConfigStore(tmp / "config.json")
        self.config.set_rules([
            Rule(name="swap", assets=[111], action="replace", value="999"),
            Rule(name="drop", assets=[222], action="remove"),
            Rule(name="file", assets=[333], action="file", value=str(self.local_file)),
        ])
        self.ca = CertAuthority(tmp / "ca")
        upstream_ctx = ssl.create_default_context(cadata=self.upstream_ca.ca_pem())
        self.proxy = ProxyServer(
            self.config, self.ca, port=0, upstream_context=upstream_ctx,
            resolve=lambda h, p: ("127.0.0.1", up_port),
        )
        port = await self.proxy.start()
        self.client = Client(port, self.ca.ca_pem())

    async def asyncTearDown(self) -> None:
        await self.proxy.stop()
        self.fake.server.close()
        self.tmp.cleanup()

    async def test_batch_replace_remove_and_local_file(self) -> None:
        r, w = await self.client.connect("assetdelivery.roblox.com")
        batch = [
            {"requestId": "a", "assetId": 111},
            {"requestId": "b", "assetId": 222},
            {"requestId": "c", "assetId": 333},
            {"requestId": "d", "assetId": 444},
        ]
        resp, data = await Client.request(
            r, w, "POST", "assetdelivery.roblox.com", "/v1/assets/batch", json.dumps(batch).encode(),
            "Content-Type: application/json\r\nAccept-Encoding: gzip, zstd\r\n",
        )
        self.assertEqual(resp.status, 200)
        sent = self.fake.seen_batches[-1]
        self.assertEqual([i["assetId"] for i in sent], [999, 333, 444])  # 111 swapped, 222 dropped
        answer = json.loads(http1.decode_content(data, resp.headers.get("content-encoding")))
        locations = {i["requestId"]: i["location"] for i in answer}

        # keep-alive: second request on the same connection works
        resp2, data2 = await Client.request(r, w, "GET", "assetdelivery.roblox.com", "/v1/asset/?id=5")
        self.assertEqual((resp2.status, data2), (200, b"single"))
        w.close()

        cr, cw = await self.client.connect("fts.rbxcdn.com")
        path_c = locations["c"].split("fts.rbxcdn.com", 1)[1]
        resp, data = await Client.request(cr, cw, "GET", "fts.rbxcdn.com", path_c)
        self.assertEqual(data, b"MY-LOCAL-FILE")
        path_d = locations["d"].split("fts.rbxcdn.com", 1)[1]
        resp, data = await Client.request(cr, cw, "GET", "fts.rbxcdn.com", path_d)
        self.assertEqual(data, CDN_BYTES)
        cw.close()

    async def test_single_asset_endpoint(self) -> None:
        r, w = await self.client.connect("assetdelivery.roblox.com")
        resp, _ = await Client.request(r, w, "GET", "assetdelivery.roblox.com", "/v1/asset/?id=111&x=1")
        self.assertEqual(resp.status, 200)
        self.assertIn("id=999", self.fake.seen_single[-1])
        resp, _ = await Client.request(r, w, "GET", "assetdelivery.roblox.com", "/v1/asset?id=222")
        self.assertEqual(resp.status, 404)
        resp, data = await Client.request(r, w, "GET", "assetdelivery.roblox.com", "/v1/asset?id=333")
        self.assertEqual(data, b"MY-LOCAL-FILE")
        w.close()

    async def test_other_hosts_are_blind_tunnels(self) -> None:
        """Non-Roblox CONNECTs are passed through byte-for-byte, never decrypted."""
        async def echo(reader, writer):
            writer.write(await reader.read(100))
            await writer.drain()
            writer.close()

        echo_server = await asyncio.start_server(echo, "127.0.0.1", 0)
        echo_port = echo_server.sockets[0].getsockname()[1]
        self.proxy._resolve = lambda h, p: ("127.0.0.1", echo_port)
        reader, writer = await asyncio.open_connection("127.0.0.1", self.proxy.port)
        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        await writer.drain()
        head = await http1.read_head(reader)
        self.assertEqual(head.status, 200)
        writer.write(b"\x16\x03\x01raw-bytes")
        await writer.drain()
        self.assertEqual(await reader.read(100), b"\x16\x03\x01raw-bytes")
        writer.close()
        echo_server.close()

    async def test_refuses_non_proxy_requests(self) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.proxy.port)
        writer.write(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        await writer.drain()
        head = await http1.read_head(reader)
        self.assertEqual(head.status, 400)
        writer.close()


class NameConstraints(unittest.TestCase):
    def test_ca_cannot_sign_for_other_sites(self) -> None:
        """A cert our CA issues for a non-Roblox host must fail verification."""
        with tempfile.TemporaryDirectory() as tmp:
            ca = CertAuthority(Path(tmp))
            self.assertTrue(self._handshake(ca, "assetdelivery.roblox.com"))
            self.assertFalse(self._handshake(ca, "accounts.google.com"))

    @staticmethod
    def _handshake(ca: CertAuthority, host: str) -> bool:
        server_ctx = ca.server_context(host)
        client_ctx = ssl.create_default_context(cadata=ca.ca_pem())
        c_in, c_out, s_in, s_out = ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO()
        client = client_ctx.wrap_bio(c_in, c_out, server_hostname=host)
        server = server_ctx.wrap_bio(s_in, s_out, server_side=True)
        for _ in range(10):
            for obj in (client, server):
                try:
                    obj.do_handshake()
                except ssl.SSLWantReadError:
                    pass
                except ssl.SSLCertVerificationError:
                    return False
            s_in.write(c_out.read())
            c_in.write(s_out.read())
        try:
            client.do_handshake()
            return True
        except ssl.SSLError:
            return False


if __name__ == "__main__":
    unittest.main()
