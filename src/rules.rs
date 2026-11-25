//! Replacement rules in the Fleasion-compatible JSON shape.
//!
//! A config file looks like:
//!
//! ```json
//! {"replacement_rules": [
//!     {"name": "Sky", "enabled": true, "replace_ids": ["123", "Image"], "mode": "id", "with_id": 456},
//!     {"type": "group", "name": "Folder", "children": [ ... ]}
//! ]}
//! ```
//!
//! `mode` is `id` (fetch `with_id` instead; empty = remove), `remove`, `local` (serve
//! `local_path`; empty = remove) or `cdn`. CDN rules are skipped on purpose: they would make
//! Roblox talk to a third-party server. Rules stay as JSON values so unknown keys survive a save.

use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};

pub const MODES: [&str; 4] = ["id", "remove", "local", "cdn"];

pub const ASSET_TYPES: &[(&str, i64)] = &[
    ("image", 1), ("tshirt", 2), ("audio", 3), ("mesh", 4), ("lua", 5), ("html", 6), ("text", 7),
    ("hat", 8), ("place", 9), ("model", 10), ("shirt", 11), ("pants", 12), ("decal", 13),
    ("avatar", 16), ("head", 17), ("face", 18), ("gear", 19), ("badge", 21), ("groupemblem", 22),
    ("animation", 24), ("arms", 25), ("legs", 26), ("torso", 27), ("rightarm", 28), ("leftarm", 29),
    ("leftleg", 30), ("rightleg", 31), ("package", 32), ("youtubevideo", 33), ("gamepass", 34),
    ("app", 35), ("code", 37), ("plugin", 38), ("solidmodel", 39), ("meshpart", 40),
    ("hairaccessory", 41), ("faceaccessory", 42), ("neckaccessory", 43), ("shoulderaccessory", 44),
    ("frontaccessory", 45), ("backaccessory", 46), ("waistaccessory", 47), ("climbanimation", 48),
    ("deathanimation", 49), ("fallanimation", 50), ("idleanimation", 51), ("jumpanimation", 52),
    ("runanimation", 53), ("swimanimation", 54), ("walkanimation", 55), ("poseanimation", 56),
    ("earaccessory", 57), ("eyeaccessory", 58), ("localizationtablemanifest", 59),
    ("emoteanimation", 61), ("video", 62), ("texturepack", 63), ("tshirtaccessory", 64),
    ("shirtaccessory", 65), ("pantsaccessory", 66), ("jacketaccessory", 67),
    ("sweateraccessory", 68), ("shortsaccessory", 69), ("leftshoeaccessory", 70),
    ("rightshoeaccessory", 71), ("dressskirtaccessory", 72), ("fontfamily", 73), ("fontface", 74),
    ("meshhiddensurfaceremoval", 75), ("eyebrowaccessory", 76), ("eyelashaccessory", 77),
    ("moodanimation", 78), ("dynamichead", 79), ("codesnippet", 80),
];

/// Bare numbers up to this value are asset type IDs, not asset IDs.
pub const TYPE_ID_MAX: i64 = 80;

pub fn type_id(name: &str) -> Option<i64> {
    let key: String = name.trim().to_lowercase().chars().filter(|c| *c != ' ').collect();
    ASSET_TYPES.iter().find(|(n, _)| *n == key).map(|(_, v)| *v)
}

/// Python-style truthiness, so files written by other tools behave the same.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

pub fn is_group(entry: &Value) -> bool {
    entry.get("type").and_then(Value::as_str) == Some("group")
}

/// Rule objects depth-first, walking into groups.
pub fn iter_rules(entries: &[Value]) -> Vec<&Map<String, Value>> {
    let mut out = Vec::new();
    fn walk<'a>(entries: &'a [Value], out: &mut Vec<&'a Map<String, Value>>) {
        for entry in entries {
            let Some(obj) = entry.as_object() else { continue };
            if is_group(entry) {
                if let Some(children) = obj.get("children").and_then(Value::as_array) {
                    walk(children, out);
                }
            } else {
                out.push(obj);
            }
        }
    }
    walk(entries, &mut out);
    out
}

pub fn rule_mode(rule: &Map<String, Value>) -> &'static str {
    if !rule.contains_key("mode") {
        if let Some(remove) = rule.get("remove") {
            return if truthy(remove) { "remove" } else { "id" };
        }
    }
    let mode = match rule.get("mode") {
        Some(Value::String(s)) if !s.is_empty() => s.to_lowercase(),
        _ => "id".to_string(),
    };
    MODES.iter().copied().find(|m| *m == mode).unwrap_or("id")
}

pub fn parse_id(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n.as_i64(),
        Value::String(s) => {
            let t = s.trim();
            if !t.is_empty() && t.chars().all(|c| c.is_ascii_digit()) {
                t.parse().ok()
            } else {
                None
            }
        }
        _ => None,
    }
}

/// Split `replace_ids` into (asset IDs, asset type IDs). Anything else is ignored.
pub fn parse_targets(values: &[Value]) -> (HashSet<i64>, HashSet<i64>) {
    let mut ids = HashSet::new();
    let mut types = HashSet::new();
    for v in values {
        if let Some(n) = parse_id(v) {
            if n > TYPE_ID_MAX {
                ids.insert(n);
            }
            continue;
        }
        if let Some(s) = v.as_str() {
            if let Some(t) = type_id(s) {
                types.insert(t);
            }
        }
    }
    (ids, types)
}

/// The asset type named in a batch request entry, if any.
pub fn type_of_entry(entry: &Map<String, Value>) -> Option<i64> {
    if let Some(t) = entry.get("assetTypeId").and_then(parse_id) {
        return Some(t);
    }
    entry.get("assetType").and_then(Value::as_str).and_then(type_id)
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Action {
    Replace(i64),
    Remove,
    File(String),
}

#[derive(Clone, Debug, Default)]
pub struct RuleSet {
    pub by_id: HashMap<i64, Action>,
    pub by_type: HashMap<i64, Action>,
    pub skipped_cdn: usize,
}

impl RuleSet {
    pub fn is_empty(&self) -> bool {
        self.by_id.is_empty() && self.by_type.is_empty()
    }

    pub fn lookup(&self, asset: Option<i64>, type_id: Option<i64>) -> Option<&Action> {
        if let Some(a) = asset {
            if let Some(hit) = self.by_id.get(&a) {
                return Some(hit);
            }
        }
        type_id.and_then(|t| self.by_type.get(&t))
    }
}

fn text_of(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.trim().to_string(),
        Some(other) => other.to_string(),
    }
}

/// Turn enabled rules into a fast lookup. Later rules win, like Fleasion.
pub fn compile_rules(rules: &[&Map<String, Value>], resolve_local: &dyn Fn(&str) -> String) -> RuleSet {
    let mut out = RuleSet::default();
    for rule in rules {
        if !rule.get("enabled").map(truthy).unwrap_or(true) {
            continue;
        }
        let targets = rule.get("replace_ids").and_then(Value::as_array).cloned().unwrap_or_default();
        let (ids, types) = parse_targets(&targets);
        if ids.is_empty() && types.is_empty() {
            continue;
        }
        let action = match rule_mode(rule) {
            "remove" => Action::Remove,
            "local" => {
                let path = text_of(rule.get("local_path"));
                if path.is_empty() { Action::Remove } else { Action::File(resolve_local(&path)) }
            }
            "cdn" => {
                if !text_of(rule.get("cdn_url")).is_empty() {
                    out.skipped_cdn += 1;
                    continue; // never send Roblox to third-party servers
                }
                Action::Remove
            }
            _ => match rule.get("with_id") {
                None | Some(Value::Null) => Action::Remove,
                Some(Value::String(s)) if s.trim().is_empty() => Action::Remove,
                Some(raw) => match parse_id(raw) {
                    Some(t) if t > TYPE_ID_MAX => Action::Replace(t),
                    _ => continue,
                },
            },
        };
        for a in &ids {
            out.by_id.insert(*a, action.clone());
        }
        for t in &types {
            out.by_type.insert(*t, action.clone());
        }
    }
    out
}

pub fn new_rule(name: &str, targets: &[String], mode: &str, value: &str, enabled: bool) -> Value {
    let mut rule = Map::new();
    rule.insert("name".into(), Value::from(name));
    rule.insert("enabled".into(), Value::from(enabled));
    rule.insert("replace_ids".into(), Value::from(targets.to_vec()));
    rule.insert("mode".into(), Value::from(mode));
    match mode {
        "id" => {
            let v = value.trim();
            let id = if !v.is_empty() && v.chars().all(|c| c.is_ascii_digit()) {
                v.parse::<i64>().map(Value::from).unwrap_or(Value::Null)
            } else {
                Value::Null
            };
            rule.insert("with_id".into(), id);
        }
        "local" => {
            rule.insert("local_path".into(), Value::from(value));
        }
        "cdn" => {
            rule.insert("cdn_url".into(), Value::from(value));
        }
        _ => {}
    }
    Value::Object(rule)
}

/// One target field: an asset ID or an asset type name. Errors are shown to the user.
pub fn parse_target(token: &str) -> Result<String, String> {
    let t = token.trim();
    if !t.is_empty() && t.chars().all(|c| c.is_ascii_digit()) {
        let n: i64 = t.parse().map_err(|_| format!("{t} is not a valid asset ID"))?;
        if n <= TYPE_ID_MAX {
            return Err(format!("{t} is too small to be an asset ID"));
        }
        return Ok(t.to_string());
    }
    if type_id(t).is_some() && !t.contains(' ') {
        return Ok(t.to_string());
    }
    Err(format!("\"{t}\" is not an asset ID or asset type name"))
}

/// Short detail shown next to the action chip.
pub fn describe(rule: &Map<String, Value>) -> String {
    match rule_mode(rule) {
        "id" => match rule.get("with_id") {
            None | Some(Value::Null) => String::new(),
            Some(Value::String(s)) => s.clone(),
            Some(v) => v.to_string(),
        },
        "local" => {
            let p = text_of(rule.get("local_path")).replace('\\', "/");
            p.rsplit('/').next().unwrap_or("").to_string()
        }
        "cdn" => "Not supported".into(),
        _ => String::new(),
    }
}

pub fn mode_label(mode: &str) -> &'static str {
    match mode {
        "remove" => "Remove",
        "local" => "Local file",
        "cdn" => "CDN",
        _ => "Replace",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn compile(v: Value) -> RuleSet {
        let arr = v["replacement_rules"].as_array().unwrap().clone();
        let rules = iter_rules(&arr);
        compile_rules(&rules, &|p: &str| format!("R:{p}"))
    }

    #[test]
    fn modes_and_groups() {
        let rs = compile(json!({"replacement_rules": [
            {"name": "a", "replace_ids": ["1000", 2000], "mode": "id", "with_id": 5000},
            {"type": "group", "name": "g", "children": [
                {"replace_ids": ["3000"], "mode": "remove"},
                {"replace_ids": ["Image"], "mode": "local", "local_path": "/F/x.png"}
            ]},
            {"replace_ids": ["4000"], "mode": "cdn", "cdn_url": "https://example.com/x"},
            {"replace_ids": ["6000"], "enabled": false, "mode": "remove"},
            {"replace_ids": ["7000"], "remove": true},
            {"replace_ids": ["8000"], "with_id": ""},
            {"replace_ids": ["9000"], "with_id": 5}
        ]}));
        assert_eq!(rs.by_id[&1000], Action::Replace(5000));
        assert_eq!(rs.by_id[&2000], Action::Replace(5000));
        assert_eq!(rs.by_id[&3000], Action::Remove);
        assert_eq!(rs.by_type[&1], Action::File("R:/F/x.png".into()));
        assert!(!rs.by_id.contains_key(&4000));
        assert_eq!(rs.skipped_cdn, 1);
        assert!(!rs.by_id.contains_key(&6000));
        assert_eq!(rs.by_id[&7000], Action::Remove);
        assert_eq!(rs.by_id[&8000], Action::Remove);
        assert!(!rs.by_id.contains_key(&9000));
        assert_eq!(rs.lookup(Some(1), Some(1)), Some(&Action::File("R:/F/x.png".into())));
    }

    #[test]
    fn later_rules_win() {
        let rs = compile(json!({"replacement_rules": [
            {"replace_ids": ["1000"], "with_id": 2000},
            {"replace_ids": ["1000"], "with_id": 3000}
        ]}));
        assert_eq!(rs.by_id[&1000], Action::Replace(3000));
    }

    #[test]
    fn targets() {
        assert_eq!(parse_target("12345").unwrap(), "12345");
        assert!(parse_target("12").is_err());
        assert_eq!(parse_target("Image").unwrap(), "Image");
        assert!(parse_target("nope").is_err());
        assert_eq!(type_id("Mesh Part"), Some(40));
    }

    #[test]
    fn new_rule_shape() {
        let r = new_rule("x", &["1000".into()], "id", "2000", true);
        assert_eq!(r["with_id"], json!(2000));
        let r = new_rule("x", &["1000".into()], "local", "C:/a.png", true);
        assert_eq!(r["local_path"], json!("C:/a.png"));
        assert_eq!(describe(r.as_object().unwrap()), "a.png");
    }
}
