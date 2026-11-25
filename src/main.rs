#![cfg_attr(all(windows, not(test)), windows_subsystem = "windows")]
//! Interceptium: a local proxy that swaps Roblox assets using Fleasion-compatible configs.
//! The same program is the app and, on Windows, its own setup and uninstaller.

mod app;
mod certs;
mod config;
mod gui;
mod http1;
#[macro_use]
mod logbus;
mod paths;
mod proxy;
mod roblox;
mod router;
mod rules;
mod setup;
#[cfg(windows)]
mod winsys;
mod e2e_tests;

pub const APP_NAME: &str = "Interceptium";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

const HELP: &str = "Interceptium

Usage: Interceptium [options]

  (no options)      open the window
  --headless        run the proxy without a window (Ctrl+C to stop)
  --port N          listen port (default from settings, 58443)
  --launch          with --headless: also launch Roblox
  --clear-cache     delete the Roblox asset cache and exit
  --remove-from-roblox
                    take Interceptium's certificate out of Roblox and exit
  --version         print the version

Windows setup options:
  --install, --maintenance, --repair, --reinstall, --uninstall
  --quiet           no window; with --log FILE to record progress
  --dir FOLDER      install folder (default %LOCALAPPDATA%\\Programs\\Interceptium)
  --no-desktop, --no-start-menu, --remove-data, --launch
";

fn headless(port: Option<u16>, launch: bool) -> i32 {
    crate::logbus::subscribe(|line| println!("{line}"));
    let ctl = match app::Controller::new() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("{e}");
            return 1;
        }
    };
    if let Some(p) = port {
        ctl.config.update_settings(|s| s.port = p);
    }
    if let Err(e) = ctl.start() {
        eprintln!("{e}");
        return 1;
    }
    if launch {
        if let Err(e) = ctl.launch_roblox() {
            eprintln!("{e}");
        }
    }
    println!("{APP_NAME} running at {} - press Ctrl+C to stop.", ctl.proxy_url());
    let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().expect("runtime");
    let _ = rt.block_on(tokio::signal::ctrl_c());
    ctl.stop();
    0
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let has = |f: &str| args.iter().any(|a| a == f);
    #[cfg(windows)]
    if !args.is_empty() {
        winsys::attach_parent_console();
    }
    if has("--version") {
        println!("{APP_NAME} {VERSION}");
        return;
    }
    if has("--help") || has("-h") {
        print!("{HELP}");
        return;
    }
    let setup_args = match setup::SetupArgs::parse(&args) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(64);
        }
    };
    if setup::wants_setup(&setup_args) {
        std::process::exit(setup::run(setup_args));
    }
    if has("--clear-cache") || has("--remove-from-roblox") {
        crate::logbus::subscribe(|line| println!("{line}"));
        if has("--clear-cache") {
            roblox::clear_cache();
        }
        if has("--remove-from-roblox") {
            roblox::uninstall_ca();
        }
        return;
    }
    let port = args.iter().position(|a| a == "--port").and_then(|i| args.get(i + 1)).and_then(|p| p.parse().ok());
    if has("--headless") {
        std::process::exit(headless(port, has("--launch")));
    }
    if let Some(p) = port {
        if let Ok(store) = std::panic::catch_unwind(|| config::ConfigStore::open(paths::data_dir())) {
            store.update_settings(|s| s.port = p);
        }
    }
    std::process::exit(gui::run());
}
