"""Command-line entry point. Without arguments the window opens."""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from . import APP_NAME, __version__


def _headless(args: argparse.Namespace) -> int:
    from .app import Controller
    from .logbus import log

    log.subscribe(print)
    ctl = Controller()
    if args.port:
        ctl.config.settings.port = args.port
    ctl.start()
    if args.launch:
        ctl.launch_roblox()
    print(f"{APP_NAME} running at {ctl.proxy_url} - press Ctrl+C to stop.")
    done = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: done.set())
    with __import__("contextlib").suppress(AttributeError, ValueError):
        signal.signal(signal.SIGTERM, lambda *_: done.set())
    while not done.wait(0.5):
        pass
    ctl.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="interceptium", description=f"{APP_NAME} {__version__}")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--headless", action="store_true", help="run the proxy without a window")
    parser.add_argument("--port", type=int, default=0, help="listen port (default from settings, 58443)")
    parser.add_argument("--launch", action="store_true", help="with --headless: also launch Roblox")
    parser.add_argument("--clear-cache", action="store_true", help="delete the Roblox asset cache and exit")
    parser.add_argument("--uninstall", action="store_true", help="remove the Interceptium CA from Roblox and exit")
    args = parser.parse_args(argv)

    if args.clear_cache or args.uninstall:
        from . import roblox
        from .logbus import log

        log.subscribe(print)
        if args.clear_cache:
            roblox.clear_cache()
        if args.uninstall:
            roblox.uninstall_ca()
        return 0
    if args.headless:
        return _headless(args)
    try:
        from .gui import run
    except ImportError as exc:  # tkinter missing on some Linux installs
        print(f"GUI unavailable ({exc}); starting headless.", file=sys.stderr)
        return _headless(args)
    return run()


if __name__ == "__main__":
    sys.exit(main())
