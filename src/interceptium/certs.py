"""Local certificate authority.

Safety properties:
  * The CA is created on this machine; its private key never leaves the
    per-user data folder (file mode 0600 on macOS/Linux).
  * The CA carries an X.509 *Name Constraints* extension that only
    permits the Roblox domains below. TLS libraries reject any certificate it
    signs for other sites (banks, e-mail, ...), so even a leaked key could not
    be used to impersonate them.
  * It is only added to Roblox's private ``ssl/cacert.pem`` bundle, never to the
    operating-system trust store.
"""

from __future__ import annotations

import datetime as _dt
import os
import ssl
import sys
import tempfile
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from . import paths

CA_COMMON_NAME = "Interceptium Local CA"
PERMITTED_DOMAINS = ("roblox.com", "rbxcdn.com")


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class CertAuthority:
    def __init__(self, directory: Path | None = None, permitted: tuple[str, ...] = PERMITTED_DOMAINS) -> None:
        self.directory = directory or paths.ca_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.permitted = permitted
        self.cert_path = self.directory / "interceptium-ca.pem"
        self.key_path = self.directory / "interceptium-ca.key"
        self._contexts: dict[str, ssl.SSLContext] = {}
        self._lock = threading.Lock()
        self._load_or_create()

    # -- CA ---------------------------------------------------------------
    def _load_or_create(self) -> None:
        if self.cert_path.exists() and self.key_path.exists():
            try:
                self.key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
                self.cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
                if self.cert.not_valid_after_utc > _now() + _dt.timedelta(days=30):
                    return
            except (ValueError, OSError):
                pass
        self._create()

    def _create(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Interceptium (this computer only)"),
        ])
        now = _now()
        constraints = x509.NameConstraints(
            permitted_subtrees=[x509.DNSName(d) for d in self.permitted],
            excluded_subtrees=None,
        )
        self.cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _dt.timedelta(days=1))
            .not_valid_after(now + _dt.timedelta(days=825))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            # Non-critical on purpose: OpenSSL/BoringSSL/SChannel enforce it either
            # way, while a TLS stack that doesn't know the extension won't reject the CA.
            .add_extension(constraints, critical=False)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=True,
                    crl_sign=True, encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(self.key.public_key()), critical=False)
            .sign(self.key, hashes.SHA256())
        )
        _write_private(
            self.key_path,
            self.key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        self.cert_path.write_bytes(self.cert.public_bytes(serialization.Encoding.PEM))

    def ca_pem(self) -> str:
        return self.cert.public_bytes(serialization.Encoding.PEM).decode("ascii")

    def regenerate(self) -> None:
        with self._lock:
            self._contexts.clear()
            self._create()

    # -- leaf certificates -----------------------------------------------
    def _issue(self, host: str) -> tuple[bytes, bytes]:
        key = ec.generate_private_key(ec.SECP256R1())
        now = _now()
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(self.cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _dt.timedelta(days=1))
            .not_valid_after(now + _dt.timedelta(days=397))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()), critical=False
            )
            .sign(self.key, hashes.SHA256())
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM) + self.cert.public_bytes(
            serialization.Encoding.PEM
        )
        key_pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        return cert_pem, key_pem

    def server_context(self, host: str) -> ssl.SSLContext:
        """TLS server context presenting a certificate for ``host`` (cached in memory)."""
        host = host.lower()
        with self._lock:
            ctx = self._contexts.get(host)
            if ctx is not None:
                return ctx
            cert_pem, key_pem = self._issue(host)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.set_alpn_protocols(["http/1.1"])
            # ssl can only load from files; use a private temp dir and delete at once.
            with tempfile.TemporaryDirectory(prefix="interceptium-") as tmp:
                if sys.platform != "win32":
                    os.chmod(tmp, 0o700)
                c = Path(tmp) / "c.pem"
                k = Path(tmp) / "k.pem"
                c.write_bytes(cert_pem)
                _write_private(k, key_pem)
                ctx.load_cert_chain(c, k)
            self._contexts[host] = ctx
            return ctx
