//! The real Windows operations behind the installer.

use super::core::{EntryValue, Ops};
use crate::winsys::{self, RegValue};
use std::path::{Path, PathBuf};

pub struct WindowsOps;

impl Ops for WindowsOps {
    fn stop_app(&self, exe: &Path) {
        if winsys::stop_running(exe) > 0 {
            std::thread::sleep(std::time::Duration::from_millis(500));
        }
    }
    fn remove_roblox_changes(&self) {
        crate::roblox::uninstall_ca();
    }
    fn start_menu_dir(&self) -> PathBuf {
        winsys::start_menu_dir()
    }
    fn desktop_dir(&self) -> PathBuf {
        winsys::desktop_dir()
    }
    fn create_shortcut(&self, link: &Path, target: &Path) -> Result<(), String> {
        winsys::create_shortcut(link, target, "Interceptium")
    }
    fn write_uninstall_entry(&self, values: Vec<(&'static str, EntryValue)>) -> Result<(), String> {
        let converted: Vec<(&str, RegValue)> = values
            .into_iter()
            .map(|(k, v)| (k, match v { EntryValue::Str(s) => RegValue::Str(s), EntryValue::Dword(d) => RegValue::Dword(d) }))
            .collect();
        winsys::write_uninstall_entry(&converted)
    }
    fn read_install_location(&self) -> Option<PathBuf> {
        winsys::read_uninstall_value("InstallLocation").filter(|s| !s.is_empty()).map(PathBuf::from)
    }
    fn delete_uninstall_entry(&self) {
        winsys::delete_uninstall_entry();
    }
    fn user_data_dir(&self) -> PathBuf {
        crate::paths::local_appdata().join("Interceptium")
    }
    fn temp_dir(&self) -> PathBuf {
        std::env::temp_dir()
    }
}
