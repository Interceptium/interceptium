//! The Windows setup. It is the same program as the app: run as "Interceptium-Setup-x.exe"
//! (or with a setup option) it shows the installer; the installed copy also serves as the
//! uninstaller, repairer and reinstaller for the Windows "Apps" page and the app's settings.

pub mod core;
pub mod ui;
#[cfg(windows)]
pub mod winops;

use self::core::{Installer, Options, APP_EXE};
use std::path::PathBuf;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Install,
    Maintenance,
    Repair,
    Reinstall,
    Uninstall,
}

#[derive(Clone, Debug, Default)]
pub struct SetupArgs {
    pub action: Option<Action>,
    pub quiet: bool,
    pub dir: Option<PathBuf>,
    pub no_desktop: bool,
    pub no_start_menu: bool,
    pub remove_data: bool,
    pub launch: bool,
    pub log: Option<PathBuf>,
}

impl SetupArgs {
    /// Pick the setup options out of the command line; returns None if none are present.
    pub fn parse(args: &[String]) -> Result<SetupArgs, String> {
        let mut out = SetupArgs::default();
        let mut it = args.iter();
        while let Some(a) = it.next() {
            match a.as_str() {
                "--install" => out.action = Some(Action::Install),
                "--maintenance" => out.action = Some(Action::Maintenance),
                "--repair" => out.action = Some(Action::Repair),
                "--reinstall" => out.action = Some(Action::Reinstall),
                "--uninstall" => out.action = Some(Action::Uninstall),
                "--quiet" => out.quiet = true,
                "--no-desktop" => out.no_desktop = true,
                "--no-start-menu" => out.no_start_menu = true,
                "--remove-data" => out.remove_data = true,
                "--launch" => out.launch = true,
                "--dir" => out.dir = Some(PathBuf::from(it.next().ok_or("--dir needs a folder")?)),
                "--log" => out.log = Some(PathBuf::from(it.next().ok_or("--log needs a file")?)),
                _ => {}
            }
        }
        Ok(out)
    }
}

/// The folder Interceptium is installed in (Windows), from its "Apps" entry.
pub fn install_location() -> Option<PathBuf> {
    #[cfg(windows)]
    {
        crate::winsys::read_uninstall_value("InstallLocation").filter(|s| !s.is_empty()).map(PathBuf::from)
    }
    #[cfg(not(windows))]
    {
        None
    }
}

/// True when this program file is the installed copy.
pub fn running_installed_copy() -> bool {
    let (Some(dir), Ok(me)) = (install_location(), std::env::current_exe()) else { return false };
    match (std::fs::canonicalize(dir.join(APP_EXE)), std::fs::canonicalize(me)) {
        (Ok(a), Ok(b)) => a == b,
        _ => false,
    }
}

/// Should this run show the setup instead of the app?
pub fn wants_setup(args: &SetupArgs) -> bool {
    if args.action.is_some() {
        return true;
    }
    if !cfg!(windows) {
        return false;
    }
    let name = std::env::current_exe()
        .ok()
        .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_lowercase()))
        .unwrap_or_default();
    name.contains("setup") && !running_installed_copy()
}

/// Start the installed copy with a maintenance option (used by the app's settings).
pub fn start_maintenance(action: Action) -> Result<(), String> {
    let dir = install_location().ok_or("Interceptium is not installed with its setup.")?;
    let exe = dir.join(APP_EXE);
    let flag = match action {
        Action::Repair => "--repair",
        Action::Reinstall => "--reinstall",
        Action::Uninstall => "--uninstall",
        Action::Maintenance => "--maintenance",
        Action::Install => "--install",
    };
    let mut cmd = std::process::Command::new(&exe);
    cmd.arg(flag).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0000_0008); // DETACHED_PROCESS
    }
    cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
}

pub fn make_installer() -> Installer {
    let source = std::env::current_exe().unwrap_or_default();
    #[cfg(windows)]
    let ops: Box<dyn self::core::Ops> = Box::new(winops::WindowsOps);
    #[cfg(not(windows))]
    let ops: Box<dyn self::core::Ops> = Box::new(demo::DemoOps::new());
    Installer::new(ops, source, crate::VERSION)
}

pub fn launch_app(directory: &std::path::Path) {
    let exe = directory.join(APP_EXE);
    let mut cmd = std::process::Command::new(&exe);
    cmd.current_dir(directory).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0000_0008);
    }
    let _ = cmd.spawn();
}

struct Log(Option<std::sync::Mutex<std::fs::File>>);

impl Log {
    fn open(path: &Option<PathBuf>) -> Log {
        Log(path.as_ref().and_then(|p| std::fs::File::create(p).ok()).map(std::sync::Mutex::new))
    }
    fn line(&self, text: &str) {
        use std::io::Write;
        if let Some(f) = &self.0 {
            let mut f = f.lock().unwrap_or_else(|e| e.into_inner());
            let _ = writeln!(f, "{} {text}", chrono::Local::now().format("%H:%M:%S"));
            let _ = f.flush();
        }
    }
}

/// Run an action without a window. Exit code 0 on success, 2 on failure.
pub fn run_quiet(args: &SetupArgs) -> i32 {
    let log = Log::open(&args.log);
    let installer = make_installer();
    let action = args.action.unwrap_or(Action::Install);
    log.line(&format!("{action:?} {} started", crate::VERSION));
    let last = std::sync::Mutex::new(String::new());
    let progress = |v: f32, t: &str| {
        let mut l = last.lock().unwrap_or_else(|e| e.into_inner());
        if *l != t {
            log.line(&format!("{:>3}% {t}", (v * 100.0) as u32));
            *l = t.to_string();
        }
    };
    let result: Result<(), String> = match action {
        Action::Install | Action::Maintenance => {
            let previous = installer.installed();
            let options = Options {
                directory: args.dir.clone().or_else(|| previous.as_ref().map(|m| PathBuf::from(&m.directory))).unwrap_or_else(self::core::default_directory),
                desktop_shortcut: !args.no_desktop,
                start_menu_shortcut: !args.no_start_menu,
            };
            installer.install(&options, &progress).map(|m| {
                if args.launch {
                    launch_app(std::path::Path::new(&m.directory));
                }
            })
        }
        Action::Repair => installer.repair(&progress).map(|_| ()),
        Action::Reinstall => installer.reinstall(&progress).map(|_| ()),
        Action::Uninstall => match installer.installed() {
            Some(m) => installer.uninstall(std::path::Path::new(&m.directory), args.remove_data, &progress),
            None => Err("Interceptium is not installed.".into()),
        },
    };
    match result {
        Ok(()) => {
            log.line(&format!("{action:?} finished"));
            0
        }
        Err(e) => {
            log.line(&format!("{action:?} failed: {e}"));
            2
        }
    }
}

pub fn run(args: SetupArgs) -> i32 {
    if args.quiet {
        return run_quiet(&args);
    }
    ui::run(args)
}

/// Stand-in system operations for trying the setup screens on macOS/Linux (writes to a temp folder).
#[cfg(not(windows))]
pub mod demo {
    use super::core::{EntryValue, Ops};
    use std::path::{Path, PathBuf};
    use std::sync::Mutex;

    pub struct DemoOps {
        root: PathBuf,
        location: Mutex<Option<PathBuf>>,
    }

    impl DemoOps {
        pub fn new() -> DemoOps {
            let root = std::env::temp_dir().join("interceptium-setup-demo");
            let _ = std::fs::create_dir_all(&root);
            DemoOps { root, location: Mutex::new(None) }
        }
    }

    impl Ops for DemoOps {
        fn stop_app(&self, _exe: &Path) {}
        fn remove_roblox_changes(&self) {}
        fn start_menu_dir(&self) -> PathBuf {
            self.root.join("start")
        }
        fn desktop_dir(&self) -> PathBuf {
            self.root.join("desktop")
        }
        fn create_shortcut(&self, link: &Path, target: &Path) -> Result<(), String> {
            let _ = std::fs::create_dir_all(link.parent().unwrap_or(Path::new(".")));
            std::fs::write(link, target.to_string_lossy().as_bytes()).map_err(|e| e.to_string())
        }
        fn write_uninstall_entry(&self, values: Vec<(&'static str, EntryValue)>) -> Result<(), String> {
            for (k, v) in values {
                if let ("InstallLocation", EntryValue::Str(s)) = (k, v) {
                    *self.location.lock().unwrap() = Some(PathBuf::from(s));
                }
            }
            Ok(())
        }
        fn read_install_location(&self) -> Option<PathBuf> {
            self.location.lock().unwrap().clone()
        }
        fn delete_uninstall_entry(&self) {
            *self.location.lock().unwrap() = None;
        }
        fn user_data_dir(&self) -> PathBuf {
            self.root.join("data").join("Interceptium")
        }
        fn temp_dir(&self) -> PathBuf {
            self.root.join("tmp")
        }
    }
}
