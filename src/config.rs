//! Settings plus a library of rule configs (one JSON file per config).
//!
//! Inside the per-user data folder:
//!   settings.json        port, options and "enabled_configs"
//!   configs/<Name>.json  {"replacement_rules": [...]}  (Fleasion-compatible)
//!
//! Any number of configs can be enabled; their rules are combined in the order of
//! `enabled_configs`.

use crate::rules::{compile_rules, iter_rules, RuleSet};
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::SystemTime;

pub const DEFAULT_PORT: u16 = 58443;
pub const MAX_CONFIG_BYTES: u64 = 32 * 1024 * 1024;

/// Parse JSON that may be UTF-8 (with or without BOM), UTF-16 or Windows-1252.
pub fn decode_json_bytes(raw: &[u8]) -> Result<Value, String> {
    let mut candidates: Vec<String> = Vec::new();
    let body = raw.strip_prefix(b"\xEF\xBB\xBF").unwrap_or(raw);
    if let Ok(s) = std::str::from_utf8(body) {
        candidates.push(s.to_string());
    }
    let utf16 = |bytes: &[u8], le: bool| -> Option<String> {
        if bytes.len() % 2 != 0 {
            return None;
        }
        let units: Vec<u16> = bytes
            .chunks(2)
            .map(|c| if le { u16::from_le_bytes([c[0], c[1]]) } else { u16::from_be_bytes([c[0], c[1]]) })
            .collect();
        String::from_utf16(&units).ok()
    };
    if let Some(rest) = raw.strip_prefix(b"\xFF\xFE") {
        candidates.extend(utf16(rest, true));
    } else if let Some(rest) = raw.strip_prefix(b"\xFE\xFF") {
        candidates.extend(utf16(rest, false));
    } else if raw.len() >= 2 && raw[1] == 0 {
        candidates.extend(utf16(raw, true));
    } else if raw.len() >= 2 && raw[0] == 0 {
        candidates.extend(utf16(raw, false));
    }
    candidates.push(raw.iter().map(|&b| b as char).collect()); // latin-1 / cp1252 fallback
    for c in candidates {
        if let Ok(v) = serde_json::from_str::<Value>(c.trim_start_matches('\u{feff}')) {
            return Ok(v);
        }
    }
    Err("not a JSON file".into())
}

pub fn normalize_config(data: Value) -> Value {
    match data {
        Value::Object(mut m) => {
            if !m.get("replacement_rules").map(Value::is_array).unwrap_or(false) {
                m.insert("replacement_rules".into(), json!([]));
            }
            Value::Object(m)
        }
        Value::Array(a) => json!({ "replacement_rules": a }),
        _ => json!({ "replacement_rules": [] }),
    }
}

pub fn safe_config_name(name: &str) -> Result<String, String> {
    let cleaned: String = name
        .chars()
        .map(|c| if "<>:\"/\\|?*".contains(c) || (c as u32) < 0x20 { '_' } else { c })
        .collect();
    let cleaned = cleaned.trim().trim_matches('.').trim().to_string();
    if cleaned.is_empty() {
        return Err("Enter a name.".into());
    }
    Ok(cleaned.chars().take(80).collect())
}

pub fn atomic_write_json(path: &Path, data: &Value) -> std::io::Result<()> {
    let tmp = path.with_file_name(format!("{}.tmp", path.file_name().unwrap_or_default().to_string_lossy()));
    let text = serde_json::to_string_pretty(data).unwrap_or_else(|_| "{}".into());
    std::fs::write(&tmp, text)?;
    std::fs::rename(&tmp, path)
}

#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    pub port: u16,
    pub clear_cache_on_launch: bool,
    pub auto_capture_launches: bool,
    pub enabled_configs: Vec<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Settings { port: DEFAULT_PORT, clear_cache_on_launch: true, auto_capture_launches: true, enabled_configs: vec![] }
    }
}

type CacheKey = Vec<(String, Option<(SystemTime, u64)>)>;

struct Inner {
    settings: Settings,
    cache_key: Option<CacheKey>,
    cache: Arc<RuleSet>,
}

pub struct ConfigStore {
    pub root: PathBuf,
    pub folder: PathBuf,
    settings_path: PathBuf,
    inner: Mutex<Inner>,
}

impl ConfigStore {
    pub fn open(root: PathBuf) -> ConfigStore {
        let folder = root.join("configs");
        let _ = std::fs::create_dir_all(&folder);
        let settings_path = root.join("settings.json");
        let settings = load_settings(&settings_path);
        let store = ConfigStore {
            root,
            folder,
            settings_path,
            inner: Mutex::new(Inner { settings, cache_key: None, cache: Arc::new(RuleSet::default()) }),
        };
        if store.names().is_empty() {
            let _ = store.save_config("Default", &json!({"replacement_rules": []}));
            store.lock().settings.enabled_configs = vec!["Default".into()];
            store.save();
        }
        store
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn settings(&self) -> Settings {
        self.lock().settings.clone()
    }

    pub fn update_settings(&self, f: impl FnOnce(&mut Settings)) {
        {
            let mut inner = self.lock();
            f(&mut inner.settings);
            inner.cache_key = None;
        }
        self.save();
    }

    pub fn save(&self) {
        let s = self.settings();
        let _ = atomic_write_json(
            &self.settings_path,
            &json!({
                "port": s.port,
                "clear_cache_on_launch": s.clear_cache_on_launch,
                "auto_capture_launches": s.auto_capture_launches,
                "enabled_configs": s.enabled_configs,
            }),
        );
    }

    pub fn path_for(&self, name: &str) -> Result<PathBuf, String> {
        Ok(self.folder.join(format!("{}.json", safe_config_name(name)?)))
    }

    pub fn names(&self) -> Vec<String> {
        let mut out: Vec<String> = std::fs::read_dir(&self.folder)
            .map(|rd| {
                rd.filter_map(|e| e.ok())
                    .map(|e| e.path())
                    .filter(|p| p.is_file() && p.extension().map(|x| x == "json").unwrap_or(false))
                    .filter_map(|p| p.file_stem().map(|s| s.to_string_lossy().into_owned()))
                    .collect()
            })
            .unwrap_or_default();
        out.sort_by_key(|n| n.to_lowercase());
        out
    }

    pub fn load_config(&self, name: &str) -> Value {
        let Ok(path) = self.path_for(name) else { return json!({"replacement_rules": []}) };
        match std::fs::metadata(&path) {
            Ok(m) if m.len() <= MAX_CONFIG_BYTES => {}
            _ => return json!({"replacement_rules": []}),
        }
        std::fs::read(&path)
            .ok()
            .and_then(|raw| decode_json_bytes(&raw).ok())
            .map(normalize_config)
            .unwrap_or_else(|| json!({"replacement_rules": []}))
    }

    pub fn save_config(&self, name: &str, data: &Value) -> Result<(), String> {
        let path = self.path_for(name)?;
        atomic_write_json(&path, &normalize_config(data.clone())).map_err(|e| e.to_string())?;
        self.lock().cache_key = None;
        Ok(())
    }

    pub fn is_enabled(&self, name: &str) -> bool {
        self.lock().settings.enabled_configs.iter().any(|n| n == name)
    }

    pub fn set_enabled(&self, name: &str, enabled: bool) {
        self.update_settings(|s| {
            s.enabled_configs.retain(|n| n != name);
            if enabled {
                s.enabled_configs.push(name.to_string());
            }
        });
    }

    pub fn create(&self, name: &str) -> Result<String, String> {
        let name = safe_config_name(name)?;
        if self.path_for(&name)?.exists() {
            return Err(format!("A config named \"{name}\" already exists."));
        }
        self.save_config(&name, &json!({"replacement_rules": []}))?;
        Ok(name)
    }

    fn unique(&self, name: &str) -> Result<String, String> {
        let base = safe_config_name(name)?;
        let mut candidate = base.clone();
        let mut n = 2;
        while self.path_for(&candidate)?.exists() {
            candidate = format!("{base} ({n})");
            n += 1;
        }
        Ok(candidate)
    }

    pub fn rename(&self, old: &str, new: &str) -> Result<String, String> {
        let new = safe_config_name(new)?;
        if new == old {
            return Ok(new);
        }
        let to = self.path_for(&new)?;
        let from = self.path_for(old)?;
        // On Windows a case-only rename points at the same file; allow it.
        if to.exists() && new.to_lowercase() != old.to_lowercase() {
            return Err(format!("A config named \"{new}\" already exists."));
        }
        std::fs::rename(&from, &to).map_err(|e| e.to_string())?;
        self.update_settings(|s| {
            for n in s.enabled_configs.iter_mut() {
                if n == old {
                    *n = new.clone();
                }
            }
        });
        Ok(new)
    }

    pub fn delete(&self, name: &str) {
        if let Ok(p) = self.path_for(name) {
            let _ = std::fs::remove_file(p);
        }
        self.update_settings(|s| s.enabled_configs.retain(|n| n != name));
    }

    /// Copy a Fleasion or Interceptium config into the library. Returns its name.
    pub fn import_file(&self, source: &Path) -> Result<String, String> {
        let meta = std::fs::metadata(source).map_err(|e| e.to_string())?;
        if meta.len() > MAX_CONFIG_BYTES {
            return Err("The file is too large to be a config.".into());
        }
        let raw = std::fs::read(source).map_err(|e| e.to_string())?;
        let data = decode_json_bytes(&raw)?;
        if !(data.is_object() || data.is_array()) {
            return Err("This file is not a rule config.".into());
        }
        let stem = source.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_else(|| "Imported".into());
        let name = self.unique(&stem)?;
        self.save_config(&name, &normalize_config(data))?;
        Ok(name)
    }

    pub fn export(&self, name: &str, dest: &Path) -> Result<(), String> {
        std::fs::copy(self.path_for(name)?, dest).map(|_| ()).map_err(|e| e.to_string())
    }

    /// Fleasion "/Folder/file.png" paths live inside the configs folder.
    pub fn resolve_local(&self, value: &str) -> String {
        let text = value.trim();
        if text.starts_with('/') && !text.starts_with("//") && !text.contains('\\') {
            let parts: Vec<&str> = text[1..].split('/').collect();
            if parts.len() >= 2 && !parts.iter().any(|p| p.is_empty() || *p == "." || *p == "..") {
                let mut candidate = self.folder.clone();
                for p in &parts {
                    candidate.push(p);
                }
                if candidate.is_file() || !Path::new(text).is_file() {
                    return candidate.to_string_lossy().into_owned();
                }
            }
        }
        if let Some(rest) = text.strip_prefix("~/") {
            return crate::paths::home().join(rest).to_string_lossy().into_owned();
        }
        text.to_string()
    }

    /// Compiled rules from all enabled configs (re-read when the files change).
    pub fn active_rules(&self) -> Arc<RuleSet> {
        let enabled = self.lock().settings.enabled_configs.clone();
        let key: CacheKey = enabled
            .iter()
            .map(|n| {
                let stat = self
                    .path_for(n)
                    .ok()
                    .and_then(|p| std::fs::metadata(p).ok())
                    .and_then(|m| m.modified().ok().map(|t| (t, m.len())));
                (n.clone(), stat)
            })
            .collect();
        {
            let inner = self.lock();
            if inner.cache_key.as_ref() == Some(&key) {
                return inner.cache.clone();
            }
        }
        let configs: Vec<Value> = enabled.iter().map(|n| self.load_config(n)).collect();
        let mut all: Vec<&Map<String, Value>> = Vec::new();
        for c in &configs {
            if let Some(arr) = c.get("replacement_rules").and_then(Value::as_array) {
                all.extend(iter_rules(arr));
            }
        }
        let compiled = Arc::new(compile_rules(&all, &|p: &str| self.resolve_local(p)));
        let mut inner = self.lock();
        inner.cache = compiled.clone();
        inner.cache_key = Some(key);
        compiled
    }
}

fn load_settings(path: &Path) -> Settings {
    let Some(Value::Object(raw)) = std::fs::read(path).ok().and_then(|b| decode_json_bytes(&b).ok()) else {
        return Settings::default();
    };
    let port = raw
        .get("port")
        .and_then(Value::as_u64)
        .filter(|p| (1024..=65535).contains(p))
        .map(|p| p as u16)
        .unwrap_or(DEFAULT_PORT);
    let flag = |k: &str| raw.get(k).map(crate::rules::truthy).unwrap_or(true);
    Settings {
        port,
        clear_cache_on_launch: flag("clear_cache_on_launch"),
        auto_capture_launches: flag("auto_capture_launches"),
        enabled_configs: raw
            .get("enabled_configs")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
            .unwrap_or_default(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rules::Action;

    fn temp() -> PathBuf {
        let d = std::env::temp_dir().join(format!("icfg-{}-{}", std::process::id(), rand_suffix()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }
    fn rand_suffix() -> u128 {
        SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).unwrap().as_nanos()
    }

    #[test]
    fn library_round_trip() {
        let root = temp();
        let store = ConfigStore::open(root.clone());
        assert_eq!(store.names(), vec!["Default"]);
        assert!(store.is_enabled("Default"));
        store.create("Sky").unwrap();
        assert!(store.create("Sky").is_err());
        store
            .save_config("Sky", &json!({"replacement_rules": [{"replace_ids": ["1000"], "with_id": 2000}], "extra": 1}))
            .unwrap();
        assert!(store.active_rules().is_empty());
        store.set_enabled("Sky", true);
        assert_eq!(store.active_rules().by_id[&1000], Action::Replace(2000));
        assert_eq!(store.load_config("Sky")["extra"], json!(1));
        let new = store.rename("Sky", "Sky 2").unwrap();
        assert_eq!(new, "Sky 2");
        assert!(store.is_enabled("Sky 2"));
        store.delete("Sky 2");
        assert!(!store.is_enabled("Sky 2"));
        let again = ConfigStore::open(root.clone());
        assert_eq!(again.settings().enabled_configs, vec!["Default"]);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn imports_fleasion_files() {
        let root = temp();
        let store = ConfigStore::open(root.clone());
        let src = root.join("My Config.json");
        // UTF-16 LE with BOM, a bare list
        let text = "[{\"replace_ids\": [\"5000\"], \"mode\": \"remove\"}]";
        let mut bytes = vec![0xFF, 0xFE];
        for u in text.encode_utf16() {
            bytes.extend_from_slice(&u.to_le_bytes());
        }
        std::fs::write(&src, bytes).unwrap();
        assert_eq!(store.import_file(&src).unwrap(), "My Config");
        assert_eq!(store.import_file(&src).unwrap(), "My Config (2)");
        store.set_enabled("My Config", true);
        assert_eq!(store.active_rules().by_id[&5000], Action::Remove);
        std::fs::write(&src, "nope").unwrap();
        assert!(store.import_file(&src).is_err());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn portable_local_paths() {
        let root = temp();
        let store = ConfigStore::open(root.clone());
        std::fs::create_dir_all(store.folder.join("Pack")).unwrap();
        std::fs::write(store.folder.join("Pack").join("a.png"), b"x").unwrap();
        assert_eq!(store.resolve_local("/Pack/a.png"), store.folder.join("Pack").join("a.png").to_string_lossy());
        assert_eq!(store.resolve_local("/../a.png"), "/../a.png");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn names_are_cleaned() {
        assert_eq!(safe_config_name(" a/b:c ").unwrap(), "a_b_c");
        assert!(safe_config_name("..").is_err());
    }
}
