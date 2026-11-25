**Interceptium** 1.0.0: a local, loopback-only proxy that swaps Roblox assets on your own computer. Rewritten in Rust.

### What it does
- Replace an asset with another asset ID, remove it, or serve it from a local file
- Multiple configs: switch each one on or off, and they combine
- Imports Fleasion JSON configs (groups, asset-type names like `Image`, portable `/Folder/file` paths)
- Rounded dark interface with the Inter font, vector icons and smooth animations at your display's refresh rate
- Launch Roblox through the proxy, or automatically reroute Roblox started from the website
- Clear the Roblox asset cache, and remove the certificate from Roblox again

### Safety
- Written in Rust: one self-contained program, with widely used open-source crates only (rustls, ring, rcgen, tokio, serde, egui)
- Listens on 127.0.0.1 only. No accounts, cookies, tokens, telemetry, update checks or other servers.
- Decrypts only Roblox asset hosts. Everything else is passed through untouched.
- Its local certificate authority is name-constrained to `roblox.com` / `rbxcdn.com`, and it is never added to the system trust store.

### Windows setup
- The setup file is the app itself: nothing is unpacked and no other program is started
- Per-user, no admin prompt, Start menu and Desktop shortcuts
- Listed in Windows Settings > Apps and Control Panel, with Uninstall and Modify
- Repair, Reinstall and Uninstall, also from Interceptium's Settings > Installation

### Downloads
| File | |
|---|---|
| `Interceptium-Setup-1.0.0.exe` | Windows: the app, which installs itself |
| `Interceptium-1.0.0-source-windows.zip` | Source + Windows build script |
| `Interceptium-1.0.0-source-linux.zip` | Source + Linux build script |
| `Interceptium-1.0.0-source-macos.zip` | Source + macOS build script |
| `Interceptium-Setup-1.0.0-source.zip` | Source + setup build script |

Check your download against `SHA256SUMS.txt`.
