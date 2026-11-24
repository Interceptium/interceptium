# Interceptium Setup

The Windows installer for Interceptium, written from scratch in Python (standard library + tkinter),
using the same UI toolkit as the app (`src/interceptium/ui`).

- Installs per user to `%LOCALAPPDATA%\Programs\Interceptium` (no administrator rights).
- Start menu and Desktop shortcuts.
- Registers in **Settings > Apps** and **Control Panel > Programs** (Uninstall and Modify work there).
- **Repair**, **Reinstall** and **Uninstall**, also reachable from Interceptium's Settings > Installation.
- Only removes files it created (listed in `install.json`). Configs are kept unless you choose to delete them.

## Command line

```
Interceptium Setup.exe                 install, or the maintenance menu if already installed
Interceptium Setup.exe --maintenance   Repair / Reinstall / Uninstall
Interceptium Setup.exe --repair | --reinstall | --uninstall
  --quiet          no window; exit code 0 on success
  --dir PATH       install folder
  --no-desktop     no Desktop shortcut
  --no-start-menu  no Start menu shortcut
  --remove-data    with --uninstall: also delete configs and settings
  --launch         open Interceptium after a quiet install
```

## How the Windows files are built

No PyInstaller and no third-party installer tools (`tools/build_windows.py`):

- **Runtime:** the official Python embeddable package from python.org. The build refuses to continue unless
  `python.exe`, `pythonw.exe` and the Python DLL carry a valid Python Software Foundation signature.
  tkinter and the pinned `cryptography` wheel are added.
- **`Interceptium.exe`** (`windows/launcher.c`, ~30 lines): starts the bundled `pythonw.exe` with Interceptium.
- **`Interceptium-Setup-<version>.exe`** (`windows/setup_stub.c`): unpacks the files listed after its own end
  into a fresh `%TEMP%` folder, runs this setup with the bundled Python, then deletes that folder.
  The list format is in `tools/pack_setup.py`.

Build on Windows with Python 3.14 and the Visual Studio Build Tools ("Desktop development with C++"):

```
scripts\build-setup.bat
```

Tests (run on any OS; Windows calls are faked): `python -m unittest discover -s setup/tests`
