//! Roblox client integration: find installs, trust our CA, launch through the proxy.
//!
//! Needs no administrator rights, reads no accounts or cookies and contacts no server.
//! Changes are limited to:
//!  * a marked block appended to each Roblox install's own `ssl/cacert.pem`
//!  * one FastFlag (`DFFlagHttpUseEmbeddedCaCerts=False`) in that install's
//!    `ClientSettings/ClientAppSettings.json`, so Roblox reads that bundle
//!  * deleting Roblox's asset cache when asked
//! "Remove from Roblox" undoes all of it.

use serde_json::{Map, Value};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub const BEGIN_MARK: &str = "# >>> Interceptium local CA (safe to delete) >>>";
pub const END_MARK: &str = "# <<< Interceptium local CA <<<";
pub const CA_FLAG: &str = "DFFlagHttpUseEmbeddedCaCerts";
pub const PLAYER_EXE: &str = if cfg!(windows) { "RobloxPlayerBeta.exe" } else { "RobloxPlayer" };

#[derive(Clone, Debug)]
pub struct Install {
    pub exe: PathBuf,
    pub resources: PathBuf,
    pub settings_dir: PathBuf,
}

pub fn find_installs() -> Vec<Install> {
    let mut found = Vec::new();
    if cfg!(windows) {
        let la = crate::paths::local_appdata();
        let pf86 = std::env::var_os("ProgramFiles(x86)").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\Program Files (x86)"));
        let roots = [
            la.join("Roblox").join("Versions"),
            la.join("Bloxstrap").join("Versions"),
            la.join("Fishstrap").join("Versions"),
            pf86.join("Roblox").join("Versions"),
        ];
        for root in roots {
            let Ok(rd) = std::fs::read_dir(&root) else { continue };
            for child in rd.filter_map(|e| e.ok()).map(|e| e.path()) {
                let exe = child.join(PLAYER_EXE);
                if exe.is_file() {
                    found.push(Install { exe, resources: child.clone(), settings_dir: child });
                }
            }
        }
    } else if cfg!(target_os = "macos") {
        for app in [PathBuf::from("/Applications/Roblox.app"), crate::paths::home().join("Applications").join("Roblox.app")] {
            let exe = app.join("Contents").join("MacOS").join(PLAYER_EXE);
            if exe.is_file() {
                found.push(Install { exe, resources: app.join("Contents").join("Resources"), settings_dir: app.join("Contents").join("MacOS") });
            }
        }
    }
    found.sort_by_key(|i| std::cmp::Reverse(std::fs::metadata(&i.exe).and_then(|m| m.modified()).ok()));
    found
}

pub fn cache_files() -> Vec<PathBuf> {
    let bases: Vec<PathBuf> = if cfg!(windows) {
        let la = crate::paths::local_appdata();
        vec![la.join("Roblox"), la.join("RobloxPCGDK")]
    } else if cfg!(target_os = "macos") {
        vec![crate::paths::home().join("Library").join("Roblox")]
    } else {
        vec![crate::paths::home().join(".var/app/org.vinegarhq.Sober/data/sober/appData")]
    };
    let mut out = Vec::new();
    for b in bases {
        for n in ["rbx-storage.db", "rbx-storage.db-wal", "rbx-storage.db-shm"] {
            out.push(b.join(n));
        }
    }
    out
}

pub fn strip_block(text: &str) -> String {
    let mut t = text.to_string();
    while let Some(start) = t.find(BEGIN_MARK) {
        match t[start..].find(END_MARK) {
            Some(rel) => {
                let end = start + rel + END_MARK.len();
                t.replace_range(start..end, "");
            }
            None => {
                t.truncate(start);
                break;
            }
        }
    }
    if t.trim().is_empty() {
        String::new()
    } else {
        format!("{}\n", t.trim_end())
    }
}

fn base64(data: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for c in data.chunks(3) {
        let b = [c[0], *c.get(1).unwrap_or(&0), *c.get(2).unwrap_or(&0)];
        let n = (b[0] as u32) << 16 | (b[1] as u32) << 8 | b[2] as u32;
        for i in 0..4 {
            if i <= c.len() {
                out.push(T[(n >> (18 - 6 * i) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

/// A PEM bundle of the system's trusted CAs (used only if Roblox ships none).
fn system_bundle() -> String {
    let mut out = String::new();
    for cert in rustls_native_certs::load_native_certs().certs {
        out.push_str("-----BEGIN CERTIFICATE-----\n");
        let b = base64(cert.as_ref());
        for line in b.as_bytes().chunks(64) {
            out.push_str(std::str::from_utf8(line).unwrap_or(""));
            out.push('\n');
        }
        out.push_str("-----END CERTIFICATE-----\n");
    }
    out
}

fn read_json_object(path: &Path) -> Map<String, Value> {
    std::fs::read(path)
        .ok()
        .and_then(|b| crate::config::decode_json_bytes(&b).ok())
        .and_then(|v| if let Value::Object(m) = v { Some(m) } else { None })
        .unwrap_or_default()
}

fn install_ca_one(inst: &Install, ca_pem: &str) -> std::io::Result<()> {
    let bundle = inst.resources.join("ssl").join("cacert.pem");
    std::fs::create_dir_all(bundle.parent().unwrap())?;
    let current = match std::fs::read(&bundle) {
        Ok(b) => String::from_utf8_lossy(&b).into_owned(),
        Err(_) => system_bundle(),
    };
    let base = strip_block(&current);
    std::fs::write(&bundle, format!("{base}\n{BEGIN_MARK}\n{}\n{END_MARK}\n", ca_pem.trim()))?;
    let settings = inst.settings_dir.join("ClientSettings").join("ClientAppSettings.json");
    std::fs::create_dir_all(settings.parent().unwrap())?;
    let mut data = read_json_object(&settings);
    data.insert(CA_FLAG.into(), Value::from("False"));
    std::fs::write(&settings, serde_json::to_string_pretty(&Value::Object(data)).unwrap_or_default())
}

pub fn install_ca(ca_pem: &str) -> usize {
    let mut count = 0;
    for inst in find_installs() {
        match install_ca_one(&inst, ca_pem) {
            Ok(()) => count += 1,
            Err(e) => crate::log!("Roblox", "could not prepare {}: {e}", inst.resources.display()),
        }
    }
    crate::log!("Roblox", "certificate set up in {count} Roblox install(s)");
    count
}

pub fn uninstall_ca() -> usize {
    let mut count = 0;
    for inst in find_installs() {
        let bundle = inst.resources.join("ssl").join("cacert.pem");
        if let Ok(b) = std::fs::read(&bundle) {
            let text = String::from_utf8_lossy(&b);
            if text.contains(BEGIN_MARK) && std::fs::write(&bundle, strip_block(&text)).is_ok() {
                count += 1;
            }
        }
        let settings = inst.settings_dir.join("ClientSettings").join("ClientAppSettings.json");
        let mut data = read_json_object(&settings);
        if data.remove(CA_FLAG).is_some() {
            let _ = std::fs::write(&settings, serde_json::to_string_pretty(&Value::Object(data)).unwrap_or_default());
        }
    }
    crate::log!("Roblox", "certificate removed from {count} Roblox install(s)");
    count
}

#[derive(Clone, Debug)]
pub struct Proc {
    pub pid: u32,
    pub ppid: u32,
    pub exe: String,
    pub args: Vec<String>,
}

/// Split a Windows command line into arguments, dropping argv[0].
#[cfg_attr(not(windows), allow(dead_code))]
pub fn split_args(cmdline: &str) -> Vec<String> {
    let rest = if let Some(r) = cmdline.strip_prefix('"') {
        r.split_once('"').map(|(_, rest)| rest).unwrap_or("")
    } else {
        cmdline.split_once(' ').map(|(_, rest)| rest).unwrap_or("")
    };
    rest.split_whitespace().map(String::from).collect()
}

#[cfg(windows)]
pub fn list_players() -> Vec<Proc> {
    crate::winsys::roblox_processes(PLAYER_EXE)
        .into_iter()
        .map(|(pid, ppid, exe, cmd)| Proc { pid, ppid, exe, args: split_args(&cmd) })
        .collect()
}

#[cfg(target_os = "macos")]
pub fn list_players() -> Vec<Proc> {
    let Ok(out) = std::process::Command::new("ps").args(["-axo", "pid=,ppid=,command="]).stdin(std::process::Stdio::null()).output() else {
        return vec![];
    };
    let needle = format!("/MacOS/{PLAYER_EXE}");
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter_map(|line| {
            let mut it = line.split_whitespace();
            let pid = it.next()?.parse().ok()?;
            let ppid = it.next()?.parse().ok()?;
            let command: Vec<&str> = it.collect();
            let command = command.join(" ");
            let i = command.find(&needle)?;
            let exe = command[..i + needle.len()].to_string();
            let args = command[i + needle.len()..].split_whitespace().map(String::from).collect();
            Some(Proc { pid, ppid, exe, args })
        })
        .collect()
}

#[cfg(not(any(windows, target_os = "macos")))]
pub fn list_players() -> Vec<Proc> {
    vec![]
}

pub fn is_player_running() -> bool {
    !list_players().is_empty()
}

pub fn kill(pid: u32) {
    #[cfg(windows)]
    crate::winsys::terminate(pid);
    #[cfg(unix)]
    {
        let _ = std::process::Command::new("kill").args(["-9", &pid.to_string()]).stdin(std::process::Stdio::null()).status();
    }
}

pub fn clear_cache() -> usize {
    if is_player_running() {
        crate::log!("Cache", "not cleared: Roblox is running");
        return 0;
    }
    let mut removed = 0;
    for p in cache_files() {
        match std::fs::remove_file(&p) {
            Ok(()) => removed += 1,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => crate::log!("Cache", "could not delete {}: {e}", p.file_name().unwrap_or_default().to_string_lossy()),
        }
    }
    crate::log!("Cache", "Roblox cache cleared ({removed} file(s))");
    removed
}

pub fn launch(proxy_url: &str, args: &[String], exe: Option<PathBuf>) -> Option<u32> {
    let exe = match exe {
        Some(e) => e,
        None => match find_installs().into_iter().next() {
            Some(i) => i.exe,
            None => {
                crate::log!("Roblox", "no Roblox Player install found");
                return None;
            }
        },
    };
    let mut cmd = std::process::Command::new(&exe);
    cmd.args(args)
        .current_dir(exe.parent().unwrap_or(Path::new(".")))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    for k in ["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"] {
        cmd.env(k, proxy_url);
    }
    cmd.env("NO_PROXY", "localhost,127.0.0.1,::1").env("no_proxy", "localhost,127.0.0.1,::1");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0000_0008 | 0x0000_0200); // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    match cmd.spawn() {
        Ok(child) => {
            crate::log!("Roblox", "launched");
            Some(child.id())
        }
        Err(e) => {
            crate::log!("Roblox", "launch failed: {e}");
            None
        }
    }
}

/// Restarts Roblox started from the website so it goes through the proxy.
///
/// When a Player process appears that Interceptium did not start, it is closed and started
/// again with the same arguments plus the proxy settings. The arguments (which include a
/// one-time join ticket) are never logged.
pub struct LaunchWatcher {
    ours: Mutex<std::collections::HashSet<u32>>,
    handled: Mutex<std::collections::HashSet<u32>>,
    stop: Arc<AtomicBool>,
    running: AtomicBool,
}

pub struct WatchContext {
    pub proxy_url: Box<dyn Fn() -> String + Send + Sync>,
    pub ca_pem: Box<dyn Fn() -> String + Send + Sync>,
    pub clear_first: Box<dyn Fn() -> bool + Send + Sync>,
}

impl Default for LaunchWatcher {
    fn default() -> Self {
        LaunchWatcher {
            ours: Mutex::new(Default::default()),
            handled: Mutex::new(Default::default()),
            stop: Arc::new(AtomicBool::new(false)),
            running: AtomicBool::new(false),
        }
    }
}

impl LaunchWatcher {
    pub fn mark_ours(&self, pid: Option<u32>) {
        if let Some(p) = pid {
            self.ours.lock().unwrap_or_else(|e| e.into_inner()).insert(p);
        }
    }

    pub fn start(self: &Arc<Self>, ctx: Arc<WatchContext>) {
        if self.running.swap(true, Ordering::SeqCst) {
            return;
        }
        self.stop.store(false, Ordering::SeqCst);
        let me = self.clone();
        std::thread::Builder::new()
            .name("interceptium-watch".into())
            .spawn(move || {
                while !me.stop.load(Ordering::SeqCst) {
                    for _ in 0..10 {
                        std::thread::sleep(Duration::from_millis(100));
                        if me.stop.load(Ordering::SeqCst) {
                            break;
                        }
                    }
                    if me.stop.load(Ordering::SeqCst) {
                        break;
                    }
                    me.tick(&ctx);
                }
                me.running.store(false, Ordering::SeqCst);
            })
            .ok();
    }

    pub fn stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }

    fn tick(&self, ctx: &WatchContext) {
        let procs = list_players();
        let alive: std::collections::HashSet<u32> = procs.iter().map(|p| p.pid).collect();
        let fresh: Vec<Proc> = {
            let mut ours = self.ours.lock().unwrap_or_else(|e| e.into_inner());
            ours.retain(|p| alive.contains(p));
            let mut changed = true;
            while changed {
                changed = false;
                for p in &procs {
                    if !ours.contains(&p.pid) && ours.contains(&p.ppid) {
                        ours.insert(p.pid); // anything a proxied player starts is proxied too
                        changed = true;
                    }
                }
            }
            let mut handled = self.handled.lock().unwrap_or_else(|e| e.into_inner());
            procs.into_iter().filter(|p| !ours.contains(&p.pid) && handled.insert(p.pid)).collect()
        };
        for p in fresh {
            crate::log!("Watcher", "restarting Roblox through the proxy");
            kill(p.pid);
            std::thread::sleep(Duration::from_secs(1));
            install_ca(&(ctx.ca_pem)());
            if (ctx.clear_first)() {
                clear_cache();
            }
            let exe = if p.exe.is_empty() { None } else { Some(PathBuf::from(&p.exe)) };
            self.mark_ours(launch(&(ctx.proxy_url)(), &p.args, exe));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn block_round_trip() {
        let original = "CERT A\n";
        let with = format!("{}\n{BEGIN_MARK}\nX\n{END_MARK}\n", strip_block(original));
        assert_eq!(strip_block(&with), "CERT A\n");
        assert_eq!(strip_block(&format!("A\n{BEGIN_MARK}\nbroken")), "A\n");
    }

    #[test]
    fn args() {
        assert_eq!(split_args(r#""C:\R\RobloxPlayerBeta.exe" roblox-player:1+x --app"#), vec!["roblox-player:1+x", "--app"]);
        assert_eq!(split_args("a.exe b"), vec!["b"]);
        assert!(split_args("").is_empty());
    }

    #[test]
    fn base64_matches_known_values() {
        assert_eq!(base64(b"hello"), "aGVsbG8=");
        assert_eq!(base64(b"hi!"), "aGkh");
        assert_eq!(base64(b"a"), "YQ==");
    }
}
