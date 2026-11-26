<p align="center"><img src="assets/logo.png" width="128" alt="Interceptium logo"></p>

# Interceptium

Interceptium swaps Roblox game assets (textures, sounds, meshes, and so on) on **your own computer**.
It runs a small local proxy between the Roblox Player and Roblox's asset servers. When the game asks
for an asset, Interceptium can:

- **Replace** it with a different Roblox asset ID.
- **Remove** it, so it doesn't load.
- **Serve a local file** from your disk in its place.

Interceptium is an independent, clean-room project. It is written from scratch and shares no code with
other tools.

## Safety

Interceptium was designed to do one job and nothing else:

| | |
|---|---|
| **Local only** | The proxy listens on `127.0.0.1`. Other devices on your network can't reach it. |
| **No accounts, cookies or tokens** | It never reads your Roblox login, browser cookies or any credentials. |
| **No other servers** | No telemetry, no update checks, no downloads. The only traffic is Roblox's own traffic. |
| **Decrypts only Roblox asset hosts** | Only `assetdelivery.roblox.com`, `fts.rbxcdn.com` and `contentdelivery.roblox.com` are inspected. Every other connection is passed through as an encrypted tunnel it can't read. |
| **Name-constrained certificate** | The local certificate authority is created on your PC and carries an X.509 *Name Constraints* extension. It only works for `roblox.com` and `rbxcdn.com`. Even if someone stole it, it couldn't fake your bank, email or any other site. A test in `src/e2e_tests.rs` proves this. |
| **Not in your system trust store** | The certificate is only added to Roblox's own `ssl/cacert.pem`, never to Windows/macOS trust. |
| **No admin rights** | It doesn't edit the hosts file, system proxy settings or anything outside your user account. |
| **Verified upstream TLS** | Connections from Interceptium to Roblox are fully certificate-checked. |
| **Written in Rust** | Memory-safe, one self-contained program. It uses only widely used, open-source crates (rustls and ring for TLS, rcgen for certificates, tokio, serde, egui for the window), with exact versions locked in `Cargo.lock`. |
| **Undoable** | **Remove from Roblox** (or `--remove-from-roblox`) takes the certificate and setting back out. |

## Download

Get it from the [Releases](../../releases) page:

| File | What it is |
|---|---|
| `Interceptium-Setup-1.0.0.exe` | Windows: the app, which installs itself |
| `Interceptium-1.0.0-source-windows.zip` | Source with the Windows build script (`scripts\build-windows.bat`) |
| `Interceptium-1.0.0-source-linux.zip` | Source with `scripts/build-linux.sh` |
| `Interceptium-1.0.0-source-macos.zip` | Source with `scripts/build-macos.sh` |
| `Interceptium-Setup-1.0.0-source.zip` | Source with the setup build script (`scripts\build-setup.bat`); the setup is in `src/setup/` |
| `SHA256SUMS.txt` | Checksums of the files above |

Every file is built by GitHub Actions from this repository (`.github/workflows/release.yml`). Before a release is
published, the installer is tested on Windows with a real install, repair and uninstall.

### Windows installer

Interceptium installs itself (code in `src/setup/`, no third-party installer tools). The setup file *is* the
program: nothing is unpacked to a temporary folder and no other program is started.

- Installs for your user only (`%LOCALAPPDATA%\Programs\Interceptium`), so no administrator prompt.
- Start menu and Desktop shortcuts.
- Shows up in **Settings > Apps** and **Control Panel > Programs**; Uninstall and Modify work from there.
- **Repair**, **Reinstall** and **Uninstall** are in the installer and in Interceptium under **Settings > Installation**.
- Uninstall removes only what it installed. Your configs stay unless you choose to delete them.

Setup options (also for scripted installs):

```
Interceptium-Setup-1.0.0.exe --install --quiet [--dir FOLDER] [--no-desktop] [--no-start-menu] [--launch] [--log FILE]
Interceptium.exe --repair | --reinstall | --uninstall [--remove-data] [--quiet] [--log FILE]
```

## How to use

1. Open Interceptium.
2. In **Configs** (left), pick a config or press **+** to make one. Every config with its switch **on** runs, so you can mix packs.
3. Press **Add rule** (one field per asset; **Add asset** adds another):
   - **Assets to change:** asset IDs or asset types, for example `1234567 2345678` or `Image`.
   - **Action:** *Replace* (with another asset ID), *Remove*, or *Local file*.
4. Press the power button to start the proxy.
5. Press **Launch Roblox**, or join a game from the website. With *Reroute website launches* on (in Settings), Interceptium restarts that Roblox window through the proxy.
6. Roblox caches assets, so **clear the Roblox cache** after changing rules. *Clear cache when launching* does this for you.

## Configs and Fleasion compatibility

Interceptium uses the same JSON format as Fleasion, so you can import Fleasion configs directly with the import button
next to **Configs**, or by dropping them into the `configs` folder (Settings > Open data folder).

```json
{"replacement_rules": [
  {"name": "Purple sky", "enabled": true, "replace_ids": ["6444884337", "6444884785"], "mode": "id", "with_id": 1012890},
  {"name": "No textures", "replace_ids": ["Image"], "mode": "remove"},
  {"type": "group", "name": "Sounds", "children": [
    {"name": "Gunshot", "replace_ids": ["130113322"], "mode": "local", "local_path": "/Sounds/shot.ogg"}
  ]}
]}
```

| Field | Meaning |
|---|---|
| `replace_ids` | Asset IDs, or asset type names (`Image`, `Audio`, `Mesh`, `Animation`, …) |
| `mode` | `id` replace with `with_id` · `remove` · `local` serve `local_path` |
| `local_path` | A normal path, or `/Folder/file` relative to the configs folder (portable packs) |
| groups | `{"type": "group", "children": [...]}` show as sections |

Which configs run is stored as `enabled_configs` in `settings.json`. Unknown keys in config files are kept when Interceptium saves.

**Not supported on purpose:** `cdn` rules (`cdn_url`) would make Roblox download from third-party servers. They show up as
*CDN URL (disabled)* and are skipped. TexturePack slot keys (`123:1`) are ignored.

### What happens to Roblox when you start the proxy

For each Roblox Player install it finds, Interceptium:

- adds its certificate in a marked block to `<install>/ssl/cacert.pem`;
- sets `DFFlagHttpUseEmbeddedCaCerts` to `False` in `<install>/ClientSettings/ClientAppSettings.json`, so Roblox uses that file;
- starts Roblox with `HTTPS_PROXY=http://127.0.0.1:58443`.

**Remove from Roblox** removes the first two changes again.

## Command line

```
Interceptium --headless           run the proxy without a window
Interceptium --headless --launch  ...and launch Roblox through it
Interceptium --port 60000         use a different port
Interceptium --clear-cache        delete Roblox's asset cache and exit
Interceptium --remove-from-roblox remove Interceptium's certificate from Roblox and exit
```

Settings (`settings.json`) and configs (`configs/*.json`) are stored in `%LOCALAPPDATA%\Interceptium` (Windows),
`~/Library/Application Support/Interceptium` (macOS) or `~/.local/share/interceptium` (Linux).

## Build from source

Install Rust from [rustup.rs](https://rustup.rs) (on Windows, also the Visual Studio Build Tools with
"Desktop development with C++"), then:

```
cargo run --release            # run it
cargo test                     # unit and end-to-end tests
scripts\build-windows.bat      # Windows: dist\Interceptium-Setup-1.0.0.exe
scripts/build-linux.sh         # Linux:   dist/interceptium
scripts/build-macos.sh         # macOS:   dist/Interceptium
```

## Changing the logo

Replace `assets/logo.png` (256×256), `assets/logo_96.png` (window icon) and `assets/logo.ico` (Windows exe icon).

## Code signing

Release files are not code-signed yet. Every release file is built by GitHub Actions from this repository's
source code (`.github/workflows/release.yml`), and you can check your download against `SHA256SUMS.txt`.
The workflow is ready to sign releases through SignPath once a certificate is available (see [SIGNING.md](SIGNING.md)).

## Privacy policy

This program will not transfer any information to other networked systems unless specifically requested by the user
or the person installing or operating it. Interceptium is a local proxy: the only network traffic is Roblox's own
traffic passing through it on your computer. It has no telemetry, update checks or accounts.

## Notes

- Roblox can change how its client works at any time, and that could stop asset swapping from working.
- Using modified clients may be against Roblox's Terms of Use. Use it at your own risk.
- Not affiliated with Roblox Corporation.

## License

MIT. See [LICENSE](LICENSE).

On a Mac the window uses Apple's system font. Elsewhere it uses the bundled [Inter](https://rsms.me/inter/) font, and logs use
[Geist Mono](https://github.com/vercel/geist-font); both are licensed under the SIL Open Font License 1.1
(`assets/fonts/OFL-Inter.txt`, `assets/fonts/OFL-GeistMono.txt`). They are loaded for Interceptium only and are not installed on your system.
