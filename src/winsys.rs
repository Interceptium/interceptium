//! Windows-only system calls: registry, shortcuts, known folders and processes.
//! Only documented, everyday APIs are used: no shells, scripts or helper programs.
#![cfg(windows)]

use std::path::{Path, PathBuf};
use windows::core::{Interface, BSTR, HSTRING, PCWSTR, PWSTR};
use windows::Win32::Foundation::{CloseHandle, ERROR_SUCCESS};
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoInitializeSecurity, CoSetProxyBlanket, CoTaskMemFree, IPersistFile,
    CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, EOAC_NONE, RPC_C_AUTHN_LEVEL_CALL, RPC_C_AUTHN_LEVEL_DEFAULT,
    RPC_C_IMP_LEVEL_IMPERSONATE,
};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows::Win32::System::Registry::{
    RegCloseKey, RegCreateKeyExW, RegDeleteTreeW, RegOpenKeyExW, RegQueryValueExW, RegSetValueExW, HKEY,
    HKEY_CURRENT_USER, KEY_READ, KEY_WRITE, REG_DWORD, REG_OPTION_NON_VOLATILE, REG_SZ, REG_VALUE_TYPE,
};
use windows::Win32::System::Threading::{
    OpenProcess, QueryFullProcessImageNameW, TerminateProcess, WaitForSingleObject, PROCESS_NAME_WIN32,
    PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE, PROCESS_TERMINATE,
};
use windows::Win32::System::Wmi::{
    IWbemClassObject, IWbemLocator, WbemLocator, WBEM_FLAG_FORWARD_ONLY, WBEM_FLAG_RETURN_IMMEDIATELY, WBEM_INFINITE,
};
use windows::Win32::UI::Shell::{IShellLinkW, SHGetKnownFolderPath, ShellLink, FOLDERID_Desktop, FOLDERID_Programs, KF_FLAG_DEFAULT};

pub const UNINSTALL_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Interceptium";

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

// -- registry -----------------------------------------------------------------------------
pub enum RegValue {
    Str(String),
    Dword(u32),
}

pub fn write_uninstall_entry(values: &[(&str, RegValue)]) -> Result<(), String> {
    unsafe {
        let mut key = HKEY::default();
        let path = wide(UNINSTALL_KEY);
        let r = RegCreateKeyExW(HKEY_CURRENT_USER, PCWSTR(path.as_ptr()), None, PCWSTR::null(), REG_OPTION_NON_VOLATILE, KEY_WRITE, None, &mut key, None);
        if r != ERROR_SUCCESS {
            return Err(format!("Can't register with Windows (error {}).", r.0));
        }
        for (name, value) in values {
            let n = wide(name);
            let r = match value {
                RegValue::Str(s) => {
                    let w = wide(s);
                    let bytes = std::slice::from_raw_parts(w.as_ptr() as *const u8, w.len() * 2);
                    RegSetValueExW(key, PCWSTR(n.as_ptr()), None, REG_SZ, Some(bytes))
                }
                RegValue::Dword(d) => RegSetValueExW(key, PCWSTR(n.as_ptr()), None, REG_DWORD, Some(&d.to_le_bytes())),
            };
            if r != ERROR_SUCCESS {
                let _ = RegCloseKey(key);
                return Err(format!("Can't register with Windows (error {}).", r.0));
            }
        }
        let _ = RegCloseKey(key);
    }
    Ok(())
}

pub fn read_uninstall_value(name: &str) -> Option<String> {
    unsafe {
        let mut key = HKEY::default();
        let path = wide(UNINSTALL_KEY);
        if RegOpenKeyExW(HKEY_CURRENT_USER, PCWSTR(path.as_ptr()), None, KEY_READ, &mut key) != ERROR_SUCCESS {
            return None;
        }
        let n = wide(name);
        let mut kind = REG_VALUE_TYPE::default();
        let mut buf = vec![0u16; 1024];
        let mut size = (buf.len() * 2) as u32;
        let r = RegQueryValueExW(key, PCWSTR(n.as_ptr()), None, Some(&mut kind), Some(buf.as_mut_ptr() as *mut u8), Some(&mut size));
        let _ = RegCloseKey(key);
        if r != ERROR_SUCCESS || kind != REG_SZ {
            return None;
        }
        let len = (size as usize / 2).min(buf.len());
        let s = String::from_utf16_lossy(&buf[..len]);
        Some(s.trim_end_matches('\0').to_string())
    }
}

pub fn delete_uninstall_entry() {
    unsafe {
        let path = wide(UNINSTALL_KEY);
        let _ = RegDeleteTreeW(HKEY_CURRENT_USER, PCWSTR(path.as_ptr()));
    }
}

// -- folders and shortcuts ------------------------------------------------------------------
fn known_folder(id: &windows::core::GUID) -> Option<PathBuf> {
    unsafe {
        let p: PWSTR = SHGetKnownFolderPath(id, KF_FLAG_DEFAULT, None).ok()?;
        let s = p.to_string().ok();
        CoTaskMemFree(Some(p.0 as *const _));
        s.map(PathBuf::from)
    }
}

pub fn start_menu_dir() -> PathBuf {
    known_folder(&FOLDERID_Programs).unwrap_or_else(|| {
        std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_default()
            .join(r"Microsoft\Windows\Start Menu\Programs")
    })
}

pub fn desktop_dir() -> PathBuf {
    known_folder(&FOLDERID_Desktop).unwrap_or_else(|| crate::paths::home().join("Desktop"))
}

fn com_init() {
    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
    }
}

pub fn create_shortcut(link: &Path, target: &Path, description: &str) -> Result<(), String> {
    com_init();
    unsafe {
        let shell: IShellLinkW = CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER).map_err(|e| e.to_string())?;
        let t = HSTRING::from(target.as_os_str());
        shell.SetPath(&t).map_err(|e| e.to_string())?;
        if let Some(dir) = target.parent() {
            shell.SetWorkingDirectory(&HSTRING::from(dir.as_os_str())).map_err(|e| e.to_string())?;
        }
        shell.SetIconLocation(&t, 0).map_err(|e| e.to_string())?;
        shell.SetDescription(&HSTRING::from(description)).map_err(|e| e.to_string())?;
        let file: IPersistFile = shell.cast().map_err(|e| e.to_string())?;
        if let Some(parent) = link.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        file.Save(&HSTRING::from(link.as_os_str()), true).map_err(|e| e.to_string())?;
    }
    Ok(())
}

// -- processes ------------------------------------------------------------------------------
fn image_path(pid: u32) -> Option<String> {
    unsafe {
        let h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;
        let mut buf = vec![0u16; 32768];
        let mut len = buf.len() as u32;
        let ok = QueryFullProcessImageNameW(h, PROCESS_NAME_WIN32, PWSTR(buf.as_mut_ptr()), &mut len).is_ok();
        let _ = CloseHandle(h);
        ok.then(|| String::from_utf16_lossy(&buf[..len as usize]))
    }
}

/// (pid, parent pid, executable name) of every process.
pub fn processes() -> Vec<(u32, u32, String)> {
    let mut out = Vec::new();
    unsafe {
        let Ok(snap) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else { return out };
        let mut entry = PROCESSENTRY32W { dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32, ..Default::default() };
        if Process32FirstW(snap, &mut entry).is_ok() {
            loop {
                let len = entry.szExeFile.iter().position(|&c| c == 0).unwrap_or(entry.szExeFile.len());
                out.push((entry.th32ProcessID, entry.th32ParentProcessID, String::from_utf16_lossy(&entry.szExeFile[..len])));
                if Process32NextW(snap, &mut entry).is_err() {
                    break;
                }
            }
        }
        let _ = CloseHandle(snap);
    }
    out
}

pub fn terminate(pid: u32) {
    unsafe {
        if let Ok(h) = OpenProcess(PROCESS_TERMINATE | PROCESS_SYNCHRONIZE, false, pid) {
            let _ = TerminateProcess(h, 1);
            let _ = WaitForSingleObject(h, 5000);
            let _ = CloseHandle(h);
        }
    }
}

/// Stop other running copies of `exe` (compared by full path). Returns how many were stopped.
pub fn stop_running(exe: &Path) -> usize {
    let want = exe.to_string_lossy().to_lowercase();
    let name = exe.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
    let me = std::process::id();
    let mut stopped = 0;
    for (pid, _, image) in processes() {
        if pid == me || image.to_lowercase() != name {
            continue;
        }
        if image_path(pid).map(|p| p.to_lowercase() == want).unwrap_or(false) {
            terminate(pid);
            stopped += 1;
        }
    }
    stopped
}

/// (pid, parent pid, executable path, command line) of every process called `name`, from WMI.
pub fn roblox_processes(name: &str) -> Vec<(u32, u32, String, String)> {
    // Cheap check first so WMI is only asked when a player is actually running.
    let candidates: Vec<(u32, u32)> =
        processes().into_iter().filter(|(_, _, n)| n.eq_ignore_ascii_case(name)).map(|(p, pp, _)| (p, pp)).collect();
    if candidates.is_empty() {
        return vec![];
    }
    let from_wmi = wmi_processes(name).unwrap_or_default();
    candidates
        .into_iter()
        .map(|(pid, ppid)| match from_wmi.iter().find(|w| w.0 == pid) {
            Some(w) => w.clone(),
            None => (pid, ppid, image_path(pid).unwrap_or_default(), String::new()),
        })
        .collect()
}

fn wmi_processes(name: &str) -> windows::core::Result<Vec<(u32, u32, String, String)>> {
    com_init();
    unsafe {
        let _ = CoInitializeSecurity(None, -1, None, None, RPC_C_AUTHN_LEVEL_DEFAULT, RPC_C_IMP_LEVEL_IMPERSONATE, None, EOAC_NONE, None);
        let locator: IWbemLocator = CoCreateInstance(&WbemLocator, None, CLSCTX_INPROC_SERVER)?;
        let services = locator.ConnectServer(&BSTR::from(r"ROOT\CIMV2"), &BSTR::new(), &BSTR::new(), &BSTR::new(), 0, &BSTR::new(), None)?;
        CoSetProxyBlanket(&services, 10, 0, None, RPC_C_AUTHN_LEVEL_CALL, RPC_C_IMP_LEVEL_IMPERSONATE, None, EOAC_NONE)?;
        let safe: String = name.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '.' || *c == '_' || *c == '-').collect();
        let query = format!("SELECT ProcessId, ParentProcessId, ExecutablePath, CommandLine FROM Win32_Process WHERE Name = '{safe}'");
        let rows = services.ExecQuery(&BSTR::from("WQL"), &BSTR::from(query), WBEM_FLAG_FORWARD_ONLY | WBEM_FLAG_RETURN_IMMEDIATELY, None)?;
        let mut out = Vec::new();
        loop {
            let mut row: [Option<IWbemClassObject>; 1] = [None];
            let mut returned = 0u32;
            let hr = rows.Next(WBEM_INFINITE, &mut row, &mut returned);
            if hr.is_err() || returned == 0 {
                break;
            }
            let Some(obj) = row[0].take() else { break };
            let get = |field: &str| -> windows::Win32::System::Variant::VARIANT {
                let mut v = windows::Win32::System::Variant::VARIANT::default();
                let _ = obj.Get(&HSTRING::from(field), 0, &mut v, None, None);
                v
            };
            let pid = u32::try_from(&get("ProcessId")).unwrap_or(0);
            let ppid = u32::try_from(&get("ParentProcessId")).unwrap_or(0);
            let exe = BSTR::try_from(&get("ExecutablePath")).map(|b| b.to_string()).unwrap_or_default();
            let cmd = BSTR::try_from(&get("CommandLine")).map(|b| b.to_string()).unwrap_or_default();
            out.push((pid, ppid, exe, cmd));
        }
        Ok(out)
    }
}

/// Show a console for command-line use when started from one (the app is a windowed program).
pub fn attach_parent_console() {
    unsafe {
        let _ = windows::Win32::System::Console::AttachConsole(windows::Win32::System::Console::ATTACH_PARENT_PROCESS);
    }
}

/// Open a folder in File Explorer.
pub fn open_folder(path: &Path) {
    use windows::Win32::UI::Shell::ShellExecuteW;
    use windows::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
    unsafe {
        let _ = ShellExecuteW(None, &HSTRING::from("open"), &HSTRING::from(path.as_os_str()), PCWSTR::null(), PCWSTR::null(), SW_SHOWNORMAL);
    }
}
