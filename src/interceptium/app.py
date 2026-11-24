"""Glue between the proxy, the Roblox integration and the user interfaces."""

from __future__ import annotations

from . import roblox
from .certs import CertAuthority
from .config import ConfigStore
from .logbus import log
from .proxy import LISTEN_HOST, ProxyServer, ProxyThread
from .router import AssetRouter


class Controller:
    def __init__(self, config: ConfigStore | None = None, ca: CertAuthority | None = None) -> None:
        self.config = config or ConfigStore()
        self.ca = ca or CertAuthority()
        self.router = AssetRouter()
        self._thread: ProxyThread | None = None
        self.watcher = roblox.LaunchWatcher(
            proxy_url=lambda: self.proxy_url,
            ca_pem=self.ca.ca_pem,
            clear_first=lambda: self.config.settings.clear_cache_on_launch,
        )

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.running

    @property
    def proxy_url(self) -> str:
        port = self._thread.server.port if self._thread else self.config.settings.port
        return f"http://{LISTEN_HOST}:{port}"

    def start(self) -> int:
        if self.running:
            return self._thread.server.port  # type: ignore[union-attr]
        server = ProxyServer(self.config, self.ca, self.router)
        self._thread = ProxyThread(server)
        port = self._thread.start()
        roblox.install_ca(self.ca.ca_pem())
        if self.config.settings.auto_capture_launches and (roblox.IS_WINDOWS or roblox.IS_MACOS):
            self.watcher.start()
        return port

    def stop(self) -> None:
        self.watcher.stop()
        if self._thread is not None:
            self._thread.stop()
            self._thread = None

    def launch_roblox(self) -> None:
        if not self.running:
            self.start()
        roblox.install_ca(self.ca.ca_pem())
        if self.config.settings.clear_cache_on_launch:
            for proc in roblox.list_players():
                roblox.kill(proc.pid)
            roblox.clear_cache()
        self.watcher.mark_ours(roblox.launch(self.proxy_url))

    def remove_from_roblox(self) -> None:
        roblox.uninstall_ca()

    def shutdown(self) -> None:
        self.stop()
        log.emit("App", "bye")
