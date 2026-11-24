"""Entry script used by PyInstaller and for running from a source checkout."""

import os
import sys

_TRACE = os.environ.get("INTERCEPTIUM_TRACE")
if _TRACE:  # diagnostics for unattended runs: note the start and dump stacks if still running later
    import faulthandler
    import time

    _trace_file = open(_TRACE, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - kept open on purpose
    _trace_file.write(f"{time.strftime('%H:%M:%S')} [{os.getpid()}] python started: {sys.argv!r}\n")
    faulthandler.dump_traceback_later(float(os.environ.get("INTERCEPTIUM_TRACE_AFTER", "100")), exit=True,
                                      file=_trace_file)
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from interceptium.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
