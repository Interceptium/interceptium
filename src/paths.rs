//! Where Interceptium keeps its own files (per user, never system-wide).

use std::path::PathBuf;

pub fn home() -> PathBuf {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

pub fn local_appdata() -> PathBuf {
    std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(|| home().join("AppData").join("Local"))
}

/// The per-user data folder (created if missing).
pub fn data_dir() -> PathBuf {
    let base = data_dir_path();
    let _ = std::fs::create_dir_all(&base);
    base
}

/// Where the per-user data folder is, without creating it.
pub fn data_dir_path() -> PathBuf {
    if let Some(o) = std::env::var_os("INTERCEPTIUM_HOME") {
        PathBuf::from(o)
    } else if cfg!(windows) {
        local_appdata().join(concat!(crate::brand_name!()))
    } else if cfg!(target_os = "macos") {
        home().join("Library").join("Application Support").join(concat!(crate::brand_name!()))
    } else {
        std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| home().join(".local").join("share"))
            .join(concat!(crate::brand_lower!()))
    }
}

pub fn ca_dir() -> PathBuf {
    let p = data_dir().join("ca");
    let _ = std::fs::create_dir_all(&p);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o700));
    }
    p
}
