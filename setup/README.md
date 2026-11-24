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

## Build

On Windows, with `dist\Interceptium.exe` already built (see `scripts\build-windows.bat`):

```
scripts\build-setup.bat
```

Tests (run on any OS; Windows calls are faked): `python -m unittest discover -s setup/tests`
