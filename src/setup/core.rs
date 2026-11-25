//! Install, uninstall, repair and reinstall.
//!
//! Safety rules:
//!  * Per-user install (default %LOCALAPPDATA%\Programs\Interceptium); no administrator rights.
//!  * Only files this installer wrote (listed in `install.json`) are ever deleted, and a folder
//!    is removed only if it is empty afterwards, so an unusual install folder can't lose files.
//!  * The Windows "Apps" entry lives under HKEY_CURRENT_USER.
//!  * The setup is the app itself: nothing is unpacked and no other program is started.
//!
//! Everything that touches the operating system goes through `Ops`, so the logic is tested
//! with a fake.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

pub const APP_NAME: &str = "Interceptium";
pub const PUBLISHER: &str = "Interceptium";
pub const APP_EXE: &str = "Interceptium.exe";
pub const MANIFEST: &str = "install.json";
pub const SHORTCUT: &str = "Interceptium.lnk";
pub const MOVED_PREFIX: &str = "Interceptium-removed-";

#[cfg_attr(not(windows), allow(dead_code))]
pub enum EntryValue {
    Str(String),
    Dword(u32),
}

pub trait Ops: Send + Sync {
    /// Stop running copies of `exe` other than this process.
    fn stop_app(&self, exe: &Path);
    fn remove_roblox_changes(&self);
    fn start_menu_dir(&self) -> PathBuf;
    fn desktop_dir(&self) -> PathBuf;
    fn create_shortcut(&self, link: &Path, target: &Path) -> Result<(), String>;
    fn write_uninstall_entry(&self, values: Vec<(&'static str, EntryValue)>) -> Result<(), String>;
    fn read_install_location(&self) -> Option<PathBuf>;
    fn delete_uninstall_entry(&self);
    fn user_data_dir(&self) -> PathBuf;
    fn temp_dir(&self) -> PathBuf;
}

#[derive(Clone, Debug)]
pub struct Options {
    pub directory: PathBuf,
    pub desktop_shortcut: bool,
    pub start_menu_shortcut: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Manifest {
    pub version: String,
    #[serde(default)]
    pub directory: String,
    #[serde(default)]
    pub files: Vec<String>,
    #[serde(default)]
    pub shortcuts: Vec<String>,
    #[serde(default = "yes")]
    pub desktop_shortcut: bool,
    #[serde(default = "yes")]
    pub start_menu_shortcut: bool,
}

fn yes() -> bool {
    true
}

impl Manifest {
    pub fn options(&self) -> Options {
        Options { directory: PathBuf::from(&self.directory), desktop_shortcut: self.desktop_shortcut, start_menu_shortcut: self.start_menu_shortcut }
    }

    pub fn load(directory: &Path) -> Option<Manifest> {
        let raw = std::fs::read(directory.join(MANIFEST)).ok()?;
        let mut m: Manifest = serde_json::from_slice(&raw).ok()?;
        m.directory = directory.to_string_lossy().into_owned();
        m.files.retain(|f| safe_relative(f));
        m.shortcuts.retain(|s| s.to_lowercase().ends_with(".lnk"));
        Some(m)
    }

    pub fn save(&self) -> std::io::Result<()> {
        let path = Path::new(&self.directory).join(MANIFEST);
        let tmp = Path::new(&self.directory).join(format!("{MANIFEST}.tmp"));
        std::fs::write(&tmp, serde_json::to_vec_pretty(self).unwrap_or_default())?;
        std::fs::rename(tmp, path)
    }
}

/// Manifest entries must be relative paths that stay inside the install folder.
pub fn safe_relative(name: &str) -> bool {
    if name.is_empty() || name.contains('\\') || name.contains(':') || name.starts_with('/') {
        return false;
    }
    name.split('/').all(|p| !p.is_empty() && p != "." && p != "..")
}

fn join(dir: &Path, name: &str) -> PathBuf {
    let mut p = dir.to_path_buf();
    for part in name.split('/') {
        p.push(part);
    }
    p
}

/// Windows keeps files locked briefly after a process exits; try again for a while.
fn retry(mut action: impl FnMut() -> std::io::Result<()>) -> std::io::Result<()> {
    for attempt in 0..40 {
        match action() {
            Ok(()) => return Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(e) if attempt < 39 && e.kind() == std::io::ErrorKind::PermissionDenied => std::thread::sleep(Duration::from_millis(250)),
            Err(e) => return Err(e),
        }
    }
    Ok(())
}

fn remove_empty_dirs(directory: &Path, names: &[String]) {
    let mut folders: Vec<PathBuf> = Vec::new();
    for name in names {
        let parts: Vec<&str> = name.split('/').collect();
        for i in (1..parts.len()).rev() {
            let f = join(directory, &parts[..i].join("/"));
            if !folders.contains(&f) {
                folders.push(f);
            }
        }
    }
    folders.sort_by_key(|f| std::cmp::Reverse(f.components().count()));
    for f in folders {
        let _ = std::fs::remove_dir(f); // only succeeds when empty
    }
}

fn same_file(a: &Path, b: &Path) -> bool {
    match (std::fs::canonicalize(a), std::fs::canonicalize(b)) {
        (Ok(x), Ok(y)) => x == y,
        _ => false,
    }
}

pub fn default_directory() -> PathBuf {
    crate::paths::local_appdata().join("Programs").join(APP_NAME)
}

pub type Progress<'a> = &'a dyn Fn(f32, &str);

pub struct Installer {
    pub ops: Box<dyn Ops>,
    /// The program file being installed: normally this running exe.
    source: Mutex<PathBuf>,
    pub version: String,
}

impl Installer {
    pub fn new(ops: Box<dyn Ops>, source: PathBuf, version: &str) -> Installer {
        Installer { ops, source: Mutex::new(source), version: version.to_string() }
    }

    fn source(&self) -> PathBuf {
        self.source.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn installed(&self) -> Option<Manifest> {
        let location = self.ops.read_install_location()?;
        match Manifest::load(&location) {
            Some(m) => Some(m),
            None if location.join(APP_EXE).exists() => Some(Manifest {
                version: self.version.clone(),
                directory: location.to_string_lossy().into_owned(),
                files: vec![APP_EXE.into()],
                shortcuts: vec![],
                desktop_shortcut: true,
                start_menu_shortcut: true,
            }),
            None => None,
        }
    }

    /// Delete program copies an earlier uninstall moved out of the way.
    pub fn clean_moved_copies(&self) {
        let mut folders = vec![self.ops.temp_dir(), default_directory().parent().map(Path::to_path_buf).unwrap_or_default()];
        if let Some(parent) = self.ops.read_install_location().and_then(|d| d.parent().map(Path::to_path_buf)) {
            folders.push(parent);
        }
        let me = std::env::current_exe().ok();
        for p in folders.iter().filter_map(|f| std::fs::read_dir(f).ok()).flat_map(|rd| rd.filter_map(|e| e.ok()).map(|e| e.path())) {
            let name = p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            if name.starts_with(MOVED_PREFIX) && name.ends_with(".tmp") && me.as_deref().map(|m| !same_file(m, &p)).unwrap_or(true) {
                let _ = std::fs::remove_file(p);
            }
        }
    }

    pub fn install(&self, options: &Options, progress: Progress) -> Result<Manifest, String> {
        let source = self.source();
        if !source.is_file() {
            return Err("This setup file can't be read. Download it again.".into());
        }
        let directory = options.directory.clone();
        if !directory.is_absolute() {
            return Err("Choose a full folder path.".into());
        }
        let app = directory.join(APP_EXE);
        progress(0.04, "Closing Interceptium");
        self.ops.stop_app(&app);
        progress(0.08, "Creating folder");
        let probe = directory.join(".write-test");
        std::fs::create_dir_all(&directory)
            .and_then(|_| std::fs::write(&probe, b""))
            .and_then(|_| std::fs::remove_file(&probe))
            .map_err(|_| format!("Can't write to {}. Choose another folder.", directory.display()))?;
        self.clean_moved_copies();
        let previous = Manifest::load(&directory);

        if !same_file(&source, &app) {
            progress(0.12, "Copying Interceptium");
            copy_with_progress(&source, &app, &|f| progress(0.12 + 0.66 * f, "Copying Interceptium"))
                .map_err(|e| format!("Can't copy Interceptium: {e}"))?;
        }
        let files = vec![APP_EXE.to_string()];

        progress(0.82, "Creating shortcuts");
        let mut shortcuts = Vec::new();
        let mut links = Vec::new();
        if options.start_menu_shortcut {
            links.push(self.ops.start_menu_dir().join(SHORTCUT));
        }
        if options.desktop_shortcut {
            links.push(self.ops.desktop_dir().join(SHORTCUT));
        }
        for link in links {
            self.ops.create_shortcut(&link, &app).map_err(|e| format!("Can't create a shortcut: {e}"))?;
            shortcuts.push(link.to_string_lossy().into_owned());
        }
        if !options.start_menu_shortcut {
            let _ = retry(|| std::fs::remove_file(self.ops.start_menu_dir().join(SHORTCUT)));
        }
        if !options.desktop_shortcut {
            let _ = retry(|| std::fs::remove_file(self.ops.desktop_dir().join(SHORTCUT)));
        }

        if let Some(prev) = previous {
            // Files an older version installed that this one no longer has.
            let stale: Vec<String> = prev.files.into_iter().filter(|n| !files.contains(n) && n != MANIFEST).collect();
            for name in &stale {
                let _ = retry(|| std::fs::remove_file(join(&directory, name)));
            }
            remove_empty_dirs(&directory, &stale);
        }

        progress(0.92, "Registering with Windows");
        let mut all = files.clone();
        all.push(MANIFEST.into());
        let manifest = Manifest {
            version: self.version.clone(),
            directory: directory.to_string_lossy().into_owned(),
            files: all,
            shortcuts,
            desktop_shortcut: options.desktop_shortcut,
            start_menu_shortcut: options.start_menu_shortcut,
        };
        manifest.save().map_err(|e| format!("Can't write {MANIFEST}: {e}"))?;
        self.ops.write_uninstall_entry(self.entry(&directory, &app))?;
        progress(1.0, "Done");
        Ok(manifest)
    }

    fn entry(&self, directory: &Path, app: &Path) -> Vec<(&'static str, EntryValue)> {
        let size_kb = std::fs::metadata(app).map(|m| m.len() / 1024).unwrap_or(0) as u32;
        let exe = app.to_string_lossy();
        vec![
            ("DisplayName", EntryValue::Str(APP_NAME.into())),
            ("DisplayVersion", EntryValue::Str(self.version.clone())),
            ("Publisher", EntryValue::Str(PUBLISHER.into())),
            ("DisplayIcon", EntryValue::Str(format!("{exe},0"))),
            ("InstallLocation", EntryValue::Str(directory.to_string_lossy().into_owned())),
            ("InstallDate", EntryValue::Str(chrono::Local::now().format("%Y%m%d").to_string())),
            ("UninstallString", EntryValue::Str(format!("\"{exe}\" --uninstall"))),
            ("QuietUninstallString", EntryValue::Str(format!("\"{exe}\" --uninstall --quiet"))),
            ("ModifyPath", EntryValue::Str(format!("\"{exe}\" --maintenance"))),
            ("EstimatedSize", EntryValue::Dword(size_kb)),
            ("NoRepair", EntryValue::Dword(1)),
        ]
    }

    pub fn uninstall(&self, directory: &Path, remove_data: bool, progress: Progress) -> Result<(), String> {
        let manifest = Manifest::load(directory);
        let app = directory.join(APP_EXE);
        progress(0.05, "Closing Interceptium");
        self.ops.stop_app(&app);
        progress(0.15, "Removing changes made to Roblox");
        self.ops.remove_roblox_changes();
        progress(0.35, "Removing shortcuts");
        // Only the two shortcuts this installer makes, never paths read from a file.
        for link in [self.ops.start_menu_dir().join(SHORTCUT), self.ops.desktop_dir().join(SHORTCUT)] {
            let _ = retry(|| std::fs::remove_file(&link));
        }
        progress(0.5, "Removing files");
        let files = manifest.map(|m| m.files).unwrap_or_else(|| vec![APP_EXE.into(), MANIFEST.into()]);
        let me = std::env::current_exe().ok();
        for (i, name) in files.iter().enumerate() {
            let path = join(directory, name);
            if me.as_deref().map(|m| same_file(m, &path)).unwrap_or(false) {
                // A running program can't delete itself, but it can be moved out of the way;
                // the moved copy is deleted the next time Interceptium is installed.
                let source_is_this = same_file(&self.source(), &path);
                // A file can only be renamed within one drive, so if the temp folder is on another
                // drive, the copy goes next to the install folder instead.
                let name = format!("{MOVED_PREFIX}{}.tmp", std::process::id());
                let mut places = vec![self.ops.temp_dir().join(&name)];
                if let Some(parent) = directory.parent() {
                    places.push(parent.join(&name));
                }
                for aside in places {
                    let _ = std::fs::remove_file(&aside);
                    if std::fs::rename(&path, &aside).is_ok() {
                        if source_is_this {
                            *self.source.lock().unwrap_or_else(|e| e.into_inner()) = aside;
                        }
                        break;
                    }
                }
                continue; // if every move failed, the file (and its folder) stay
            }
            retry(|| std::fs::remove_file(&path)).map_err(|e| format!("Can't remove {name}: {e}"))?;
            progress(0.5 + 0.3 * (i + 1) as f32 / files.len().max(1) as f32, "Removing files");
        }
        remove_empty_dirs(directory, &files);
        let _ = retry(|| std::fs::remove_file(directory.join(format!("{APP_EXE}.partial"))));
        let _ = std::fs::remove_dir(directory); // only succeeds when nothing else is left
        progress(0.88, "Removing from Windows");
        self.ops.delete_uninstall_entry();
        if remove_data {
            progress(0.94, "Removing configs and settings");
            let data = self.ops.user_data_dir();
            let name = data.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
            if name == APP_NAME.to_lowercase() && data.is_dir() {
                let _ = std::fs::remove_dir_all(&data);
            }
        }
        progress(1.0, "Done");
        Ok(())
    }

    pub fn repair(&self, progress: Progress) -> Result<Manifest, String> {
        let m = self.installed().ok_or("Interceptium is not installed.")?;
        self.install(&m.options(), progress)
    }

    pub fn reinstall(&self, progress: Progress) -> Result<Manifest, String> {
        let m = self.installed().ok_or("Interceptium is not installed.")?;
        let options = m.options();
        // Keep the program bytes: when this is the installed copy, uninstall moves it aside.
        self.uninstall(&options.directory, false, &|v, t| progress(v * 0.45, t))?;
        self.install(&options, &|v, t| progress(0.45 + v * 0.55, t))
    }
}

pub fn copy_with_progress(src: &Path, dst: &Path, report: &dyn Fn(f32)) -> std::io::Result<()> {
    use std::io::{Read, Write};
    let total = std::fs::metadata(src)?.len().max(1);
    let tmp = dst.with_file_name(format!("{}.partial", dst.file_name().unwrap_or_default().to_string_lossy()));
    {
        let mut fin = std::fs::File::open(src)?;
        let mut fout = std::fs::File::create(&tmp)?;
        let mut buf = vec![0u8; 512 * 1024];
        let mut done = 0u64;
        loop {
            let n = fin.read(&mut buf)?;
            if n == 0 {
                break;
            }
            fout.write_all(&buf[..n])?;
            done += n as u64;
            report(done as f32 / total as f32);
        }
        fout.sync_all()?;
    }
    retry(|| std::fs::rename(&tmp, dst))
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use std::sync::{Arc, Mutex as M};

    #[derive(Default)]
    pub struct FakeState {
        pub entry: Option<Vec<(String, String)>>,
        pub stopped: usize,
        pub roblox_cleaned: usize,
    }

    pub struct FakeOps {
        pub root: PathBuf,
        pub state: Arc<M<FakeState>>,
    }

    impl Ops for FakeOps {
        fn stop_app(&self, _exe: &Path) {
            self.state.lock().unwrap().stopped += 1;
        }
        fn remove_roblox_changes(&self) {
            self.state.lock().unwrap().roblox_cleaned += 1;
        }
        fn start_menu_dir(&self) -> PathBuf {
            self.root.join("start")
        }
        fn desktop_dir(&self) -> PathBuf {
            self.root.join("desktop")
        }
        fn create_shortcut(&self, link: &Path, target: &Path) -> Result<(), String> {
            std::fs::create_dir_all(link.parent().unwrap()).unwrap();
            std::fs::write(link, target.to_string_lossy().as_bytes()).map_err(|e| e.to_string())
        }
        fn write_uninstall_entry(&self, values: Vec<(&'static str, EntryValue)>) -> Result<(), String> {
            let v = values
                .into_iter()
                .map(|(k, v)| (k.to_string(), match v { EntryValue::Str(s) => s, EntryValue::Dword(d) => d.to_string() }))
                .collect();
            self.state.lock().unwrap().entry = Some(v);
            Ok(())
        }
        fn read_install_location(&self) -> Option<PathBuf> {
            let st = self.state.lock().unwrap();
            st.entry.as_ref()?.iter().find(|(k, _)| k == "InstallLocation").map(|(_, v)| PathBuf::from(v))
        }
        fn delete_uninstall_entry(&self) {
            self.state.lock().unwrap().entry = None;
        }
        fn user_data_dir(&self) -> PathBuf {
            self.root.join("data").join("Interceptium")
        }
        fn temp_dir(&self) -> PathBuf {
            self.root.join("tmp")
        }
    }

    pub fn setup(name: &str) -> (PathBuf, Installer, Arc<M<FakeState>>) {
        let root = std::env::temp_dir().join(format!("isetup-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("tmp")).unwrap();
        let src = root.join("Interceptium-Setup-1.0.0.exe");
        std::fs::write(&src, vec![7u8; 300_000]).unwrap();
        let state = Arc::new(M::new(FakeState::default()));
        let ops = FakeOps { root: root.clone(), state: state.clone() };
        (root.clone(), Installer::new(Box::new(ops), src, "1.0.0"), state)
    }

    fn opts(root: &Path) -> Options {
        Options { directory: root.join("Programs").join("Interceptium"), desktop_shortcut: true, start_menu_shortcut: true }
    }

    #[test]
    fn install_and_uninstall() {
        let (root, inst, state) = setup("iu");
        let o = opts(&root);
        let calls = M::new(Vec::new());
        let m = inst.install(&o, &|v, t| calls.lock().unwrap().push((v, t.to_string()))).unwrap();
        let steps = calls.lock().unwrap();
        assert!(steps.windows(2).all(|w| w[0].0 <= w[1].0), "progress only moves forward");
        assert_eq!(steps.last().unwrap().0, 1.0);
        drop(steps);
        assert_eq!(m.files, vec![APP_EXE, MANIFEST]);
        assert_eq!(std::fs::read(o.directory.join(APP_EXE)).unwrap().len(), 300_000);
        assert!(root.join("start").join(SHORTCUT).exists() && root.join("desktop").join(SHORTCUT).exists());
        let entry = state.lock().unwrap().entry.clone().unwrap();
        let get = |k: &str| entry.iter().find(|(a, _)| a == k).unwrap().1.clone();
        assert!(get("UninstallString").ends_with("\" --uninstall"));
        assert!(get("QuietUninstallString").ends_with("--uninstall --quiet"));
        assert_eq!(get("NoRepair"), "1");
        assert_eq!(inst.installed().unwrap().version, "1.0.0");

        std::fs::write(o.directory.join("user-file.txt"), b"keep me").unwrap();
        let data = root.join("data").join("Interceptium");
        std::fs::create_dir_all(&data).unwrap();
        inst.uninstall(&o.directory, false, &|_, _| {}).unwrap();
        assert!(!o.directory.join(APP_EXE).exists());
        assert!(o.directory.join("user-file.txt").exists(), "files we didn't write are kept");
        assert!(!root.join("start").join(SHORTCUT).exists());
        assert!(state.lock().unwrap().entry.is_none());
        assert_eq!(state.lock().unwrap().roblox_cleaned, 1);
        assert!(data.exists(), "configs are kept unless asked");
        inst.install(&o, &|_, _| {}).unwrap();
        inst.uninstall(&o.directory, true, &|_, _| {}).unwrap();
        assert!(!data.exists());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn empty_folder_is_removed() {
        let (root, inst, _) = setup("empty");
        let o = opts(&root);
        inst.install(&o, &|_, _| {}).unwrap();
        inst.uninstall(&o.directory, false, &|_, _| {}).unwrap();
        assert!(!o.directory.exists());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn repair_restores_and_reinstall_keeps_options() {
        let (root, inst, _) = setup("repair");
        let mut o = opts(&root);
        o.desktop_shortcut = false;
        inst.install(&o, &|_, _| {}).unwrap();
        assert!(!root.join("desktop").join(SHORTCUT).exists());
        std::fs::remove_file(o.directory.join(APP_EXE)).unwrap();
        inst.repair(&|_, _| {}).unwrap();
        assert!(o.directory.join(APP_EXE).exists());
        let m = inst.reinstall(&|_, _| {}).unwrap();
        assert!(!m.desktop_shortcut);
        assert!(o.directory.join(APP_EXE).exists());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn upgrade_from_old_layout_removes_listed_files_only() {
        let (root, inst, _) = setup("upgrade");
        let o = opts(&root);
        let dir = &o.directory;
        std::fs::create_dir_all(dir.join("runtime").join("Lib")).unwrap();
        for f in ["runtime/python.exe", "runtime/Lib/os.py", "Interceptium Setup.exe", "Interceptium.exe"] {
            std::fs::write(join(dir, f), b"old").unwrap();
        }
        std::fs::write(dir.join("notes.txt"), b"mine").unwrap();
        let old = serde_json::json!({"version": "0.9", "files": ["runtime/python.exe", "runtime/Lib/os.py", "Interceptium Setup.exe", "Interceptium.exe", "../outside.txt", "C:/x", "install.json"]});
        std::fs::write(dir.join(MANIFEST), old.to_string()).unwrap();
        std::fs::write(root.join("outside.txt"), b"x").unwrap();
        inst.install(&o, &|_, _| {}).unwrap();
        assert!(!dir.join("runtime").exists());
        assert!(!dir.join("Interceptium Setup.exe").exists());
        assert!(dir.join("notes.txt").exists());
        assert!(root.join("outside.txt").exists(), "unsafe manifest entries are ignored");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn tampered_manifest_cannot_escape() {
        let (root, inst, _) = setup("tamper");
        let o = opts(&root);
        inst.install(&o, &|_, _| {}).unwrap();
        std::fs::write(root.join("victim.txt"), b"x").unwrap();
        let bad = serde_json::json!({"version": "1", "files": ["../../victim.txt", "/etc/passwd", "a\\..\\b", "Interceptium.exe", "install.json"]});
        std::fs::write(o.directory.join(MANIFEST), bad.to_string()).unwrap();
        inst.uninstall(&o.directory, false, &|_, _| {}).unwrap();
        assert!(root.join("victim.txt").exists());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn relative_paths() {
        assert!(safe_relative("a/b.txt"));
        for bad in ["", "/a", "a/../b", "./a", "a\\b", "C:/a", "a//b"] {
            assert!(!safe_relative(bad), "{bad}");
        }
    }

    #[test]
    fn rejects_relative_folder() {
        let (root, inst, _) = setup("rel");
        let o = Options { directory: PathBuf::from("relative/dir"), desktop_shortcut: false, start_menu_shortcut: false };
        assert!(inst.install(&o, &|_, _| {}).is_err());
        let _ = std::fs::remove_dir_all(root);
    }
}
