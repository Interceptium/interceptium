**Interceptium 1.0.1**

### New
- **Own title bar** with rounded window buttons (minimize, maximize, close). Drag it to move the window, double-click to maximize, resize from any edge. Rounded window corners on Windows 11 and macOS.
- **CDN mode** (Settings > Roblox, off by default). CDN rules from Fleasion configs can download replacement files from web addresses. Interceptium asks before turning it on and makes the downloads itself: HTTPS with certificate checks only, never to this PC or your local network, no cookies, limited time and size. Roblox never connects to those addresses.
- **CDN** action in the rule editor.
- **Import** is now a labelled button, and `.json` configs can be dragged onto the window.

### Fixed
- Saving or importing a config could fail with "file exists" on some Windows setups.

### Also in 1.0
- Replace, remove or serve local files for Roblox assets, several configs at once, Fleasion config import
- Dark, light or system appearance, smooth animations, Activity panel
- Windows setup that installs, repairs, reinstalls and uninstalls itself; signed by Canopy Interactive (self-signed)
- Local only: listens on 127.0.0.1, decrypts only Roblox asset hosts, name-constrained local certificate

### Downloads
| File | |
|---|---|
| `Interceptium-Setup-1.0.1.exe` | Windows: the app, which installs itself |
| `Interceptium-1.0.1-source-windows.zip` | Source + Windows build script |
| `Interceptium-1.0.1-source-linux.zip` | Source + Linux build script |
| `Interceptium-1.0.1-source-macos.zip` | Source + macOS build script |
| `Interceptium-Setup-1.0.1-source.zip` | Source + setup build script |

Check your download against `SHA256SUMS.txt`.
