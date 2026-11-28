**Interceptium 1.0.2**

### New in 1.0.2
- **.obj models**: point an asset ID straight at an `.obj` file with a Local file rule. Roblox can't read `.obj`, so Interceptium converts it to a Roblox mesh when the game asks for it (polygons are split into triangles; missing normals are computed).

### From 1.0.1

### New
- **Own title bar** with rounded window buttons (minimize, maximize, close). Drag it to move the window, double-click to maximize, resize from any edge. Rounded window corners on Windows 11 and macOS.
- **CDN mode** (Settings > Roblox, off by default). CDN rules from Fleasion configs can download replacement files from web addresses. Interceptium asks before turning it on and makes the downloads itself: HTTPS with certificate checks only, never to this PC or your local network, no cookies, limited time and size. Roblox never connects to those addresses.
- **CDN** action in the rule editor.
- **Import** is now a labelled button, and `.json` configs can be dragged onto the window.
- **Update screen:** with an older version installed, the setup opens as *Interceptium Update* (for example 1.0.0 → 1.0.1) and keeps your folder, shortcuts and configs.

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
| `Interceptium-Setup-1.0.2.exe` | Windows: the app, which installs itself |
| `Interceptium-1.0.2-source-windows.zip` | Source + Windows build script |
| `Interceptium-1.0.2-source-linux.zip` | Source + Linux build script |
| `Interceptium-1.0.2-source-macos.zip` | Source + macOS build script |
| `Interceptium-Setup-1.0.2-source.zip` | Source + setup build script |

Check your download against `SHA256SUMS.txt`.
