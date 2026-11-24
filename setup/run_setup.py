"""Entry script for PyInstaller and for running the setup from source."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from interceptium_setup.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
