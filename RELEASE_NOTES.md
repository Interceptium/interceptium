First release of **Interceptium**, a local, loopback-only proxy that swaps Roblox assets on your own computer.

### What it does
- Replace an asset with another asset ID, remove it, or serve it from a local file
- Multiple configs: switch each one on or off, and they combine
- Imports Fleasion JSON configs (groups, asset-type names like `Image`, portable `/Folder/file` paths)
- Smooth, rounded dark interface with a round start button, sliding switches and rule search
- Launch Roblox through the proxy, or automatically reroute Roblox started from the website
- Clear the Roblox asset cache, and remove the certificate from Roblox again

### Safety
- Listens on 127.0.0.1 only. No accounts, cookies, tokens, telemetry, update checks or other servers.
- Decrypts only Roblox asset hosts. Everything else is passed through untouched.
- Its local certificate authority is name-constrained to `roblox.com` / `rbxcdn.com`, and it is never added to the system trust store.
- One third-party dependency: `cryptography` (PyCA).

### Downloads
| File | Platform |
|---|---|
| `Interceptium-windows-x64.exe` | Windows 10/11 |
| `Interceptium-macos.zip` | macOS (unsigned: right-click → Open the first time) |
| `Interceptium-linux-x64` | Linux (runs the proxy only) |

Check your download against `SHA256SUMS.txt`.
