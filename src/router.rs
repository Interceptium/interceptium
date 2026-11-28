//! Decides what happens to each Roblox asset request.
//!
//! How Roblox fetches assets:
//!  1. The client POSTs a JSON list to `assetdelivery.roblox.com/v1/assets/batch`; each entry
//!     has a `requestId` and an `assetId` (plus type hints).
//!  2. The answer lists, per `requestId`, a `location` URL on a CDN host.
//!  3. The client downloads the bytes from that URL.
//!  Older code paths use `assetdelivery.roblox.com/v1/asset/?id=<id>` directly.
//!
//! replace -> rewrite `assetId` in step 1 (or `id=` in the legacy URL);
//! remove  -> drop the entry in step 1 (legacy URL: 404);
//! file    -> remember the requestId in step 1, learn its CDN URL in step 2 and answer the
//!            download in step 3 with the local file.

use crate::rules::{type_of_entry, Action, RuleSet};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const ASSET_HOST: &str = "assetdelivery.roblox.com";
pub const CDN_HOSTS: [&str; 2] = ["fts.rbxcdn.com", "contentdelivery.roblox.com"];
pub const BATCH_PATH: &str = "/v1/assets/batch";
const ROUTE_TTL: Duration = Duration::from_secs(600);

pub fn is_intercepted(host: &str) -> bool {
    host == ASSET_HOST || CDN_HOSTS.contains(&host)
}

fn as_id(v: Option<&Value>) -> Option<i64> {
    match v? {
        Value::Number(n) => n.as_i64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

fn id_text(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(o) => o.to_string(),
    }
}

/// "https://Host/path?q" -> "https://host/path"
pub fn strip_query(url: &str) -> String {
    let no_frag = url.split('#').next().unwrap_or("");
    let no_query = no_frag.split('?').next().unwrap_or("");
    match no_query.find("://") {
        Some(i) => {
            let (scheme, rest) = (&no_query[..i], &no_query[i + 3..]);
            let (host, path) = match rest.find('/') {
                Some(j) => (&rest[..j], &rest[j..]),
                None => (rest, ""),
            };
            format!("{}://{}{}", scheme.to_lowercase(), host.to_lowercase(), path)
        }
        None => no_query.to_string(),
    }
}

pub fn file_name(path: &str) -> String {
    path.replace('\\', "/").rsplit('/').next().unwrap_or("").to_string()
}

/// Where a replacement's bytes come from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Source {
    File(String),
    /// Downloaded by Interceptium (CDN mode); Roblox never contacts this address.
    Url(String),
}

impl Source {
    fn from_action(a: &Action) -> Option<Source> {
        match a {
            Action::File(p) => Some(Source::File(p.clone())),
            Action::Cdn(u) => Some(Source::Url(u.clone())),
            _ => None,
        }
    }
    pub fn label(&self) -> String {
        match self {
            Source::File(p) => file_name(p),
            Source::Url(u) => crate::rules::cdn_host(u),
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Single {
    Pass,
    Remove,
    Serve(Source),
    Replace(String),
}

#[derive(Default)]
struct State {
    pending: HashMap<String, (Source, Instant)>,
    routes: HashMap<String, (Source, Instant)>,
}

#[derive(Default)]
pub struct AssetRouter {
    state: Mutex<State>,
}

impl AssetRouter {
    fn lock(&self) -> std::sync::MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Step 1: a new request body, or None if nothing changes.
    pub fn rewrite_batch_request(&self, body: &[u8], rules: &RuleSet) -> Option<Vec<u8>> {
        if rules.is_empty() {
            return None;
        }
        let Ok(Value::Array(entries)) = serde_json::from_slice::<Value>(body) else { return None };
        let mut changed = false;
        let mut kept = Vec::with_capacity(entries.len());
        let now = Instant::now();
        for mut entry in entries {
            let Some(obj) = entry.as_object_mut() else {
                kept.push(entry);
                continue;
            };
            let asset = as_id(obj.get("assetId"));
            let Some(hit) = rules.lookup(asset, type_of_entry(obj)).cloned() else {
                kept.push(entry);
                continue;
            };
            let shown = asset.map(|a| a.to_string()).unwrap_or_else(|| "?".into());
            match hit {
                Action::Remove => {
                    crate::log!("Remove", "asset {shown} removed");
                    changed = true;
                    continue;
                }
                Action::File(_) | Action::Cdn(_) => {
                    let source = Source::from_action(&hit).expect("file or cdn");
                    let req = id_text(obj.get("requestId"));
                    if !req.is_empty() {
                        let topic = if matches!(source, Source::Url(_)) { "CDN" } else { "File" };
                        crate::log!(topic, "asset {shown} will be served from {}", source.label());
                        self.lock().pending.insert(req, (source, now));
                    }
                }
                Action::Replace(to) => {
                    if Some(to) != asset {
                        obj.insert("assetId".into(), Value::from(to));
                        crate::log!("Replace", "asset {shown} -> {to}");
                        changed = true;
                    }
                }
            }
            kept.push(entry);
        }
        if !changed {
            return None;
        }
        serde_json::to_vec(&kept).ok()
    }

    pub fn has_pending(&self) -> bool {
        !self.lock().pending.is_empty()
    }

    /// Step 2: learn which CDN URLs belong to pending local-file requests.
    pub fn learn_batch_response(&self, body: &[u8]) {
        if !self.has_pending() {
            return;
        }
        let Ok(Value::Array(entries)) = serde_json::from_slice::<Value>(body) else { return };
        let now = Instant::now();
        let mut st = self.lock();
        for entry in &entries {
            let Some(obj) = entry.as_object() else { continue };
            let Some((source, _)) = st.pending.remove(&id_text(obj.get("requestId"))) else { continue };
            let mut urls: Vec<&str> = Vec::new();
            if let Some(u) = obj.get("location").and_then(Value::as_str) {
                urls.push(u);
            }
            if let Some(list) = obj.get("locations").and_then(Value::as_array) {
                urls.extend(list.iter().filter_map(|l| l.get("location").and_then(Value::as_str)));
            }
            for u in urls {
                st.routes.insert(strip_query(u), (source.clone(), now));
            }
        }
        st.pending.retain(|_, v| now.duration_since(v.1) < ROUTE_TTL);
        st.routes.retain(|_, v| now.duration_since(v.1) < ROUTE_TTL);
    }

    /// Step 3: the replacement for a download from Roblox's CDN, if one was routed.
    pub fn cdn_file_for(&self, host: &str, path: &str) -> Option<Source> {
        let key = strip_query(&format!("https://{host}{path}"));
        self.lock().routes.get(&key).map(|v| v.0.clone())
    }

    /// The legacy single-asset endpoint `/v1/asset?id=`.
    pub fn rewrite_single(target: &str, rules: &RuleSet) -> Single {
        let (path, query) = match target.split_once('?') {
            Some((p, q)) => (p, q),
            None => (target, ""),
        };
        if rules.is_empty() || !path.trim_end_matches('/').to_lowercase().ends_with("/v1/asset") {
            return Single::Pass;
        }
        let (query, fragment) = match query.split_once('#') {
            Some((q, f)) => (q, Some(f)),
            None => (query, None),
        };
        let mut pairs: Vec<String> = query.split('&').map(String::from).collect();
        for i in 0..pairs.len() {
            let (key, value) = match pairs[i].split_once('=') {
                Some((k, v)) => (k.to_string(), v.to_string()),
                None => continue,
            };
            if !matches!(key.to_lowercase().as_str(), "id" | "assetid") {
                continue;
            }
            let Ok(asset) = value.trim().parse::<i64>() else { continue };
            let Some(hit) = rules.lookup(Some(asset), None) else { continue };
            match hit {
                Action::Remove => {
                    crate::log!("Remove", "asset {asset} removed");
                    return Single::Remove;
                }
                Action::File(_) | Action::Cdn(_) => {
                    let source = Source::from_action(hit).expect("file or cdn");
                    let topic = if matches!(source, Source::Url(_)) { "CDN" } else { "File" };
                    crate::log!(topic, "asset {asset} served from {}", source.label());
                    return Single::Serve(source);
                }
                Action::Replace(to) => {
                    crate::log!("Replace", "asset {asset} -> {to}");
                    pairs[i] = format!("{key}={to}");
                    let mut out = format!("{path}?{}", pairs.join("&"));
                    if let Some(f) = fragment {
                        out.push('#');
                        out.push_str(f);
                    }
                    return Single::Replace(out);
                }
            }
        }
        Single::Pass
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn rules() -> RuleSet {
        let mut r = RuleSet::default();
        r.by_id.insert(1000, Action::Replace(2000));
        r.by_id.insert(3000, Action::Remove);
        r.by_id.insert(4000, Action::File("/tmp/x.png".into()));
        r.by_type.insert(3, Action::Remove);
        r
    }

    #[test]
    fn batch_flow() {
        let router = AssetRouter::default();
        let body = json!([
            {"requestId": "a", "assetId": 1000},
            {"requestId": "b", "assetId": "3000"},
            {"requestId": "c", "assetId": 4000},
            {"requestId": "d", "assetId": 9999, "assetType": "Audio"},
            {"requestId": "e", "assetId": 5555}
        ]);
        let out = router.rewrite_batch_request(body.to_string().as_bytes(), &rules()).unwrap();
        let out: Value = serde_json::from_slice(&out).unwrap();
        assert_eq!(out, json!([
            {"requestId": "a", "assetId": 2000},
            {"requestId": "c", "assetId": 4000},
            {"requestId": "e", "assetId": 5555}
        ]));
        assert!(router.has_pending());
        router.learn_batch_response(
            json!([{"requestId": "c", "location": "https://FTS.rbxcdn.com/abc?sig=1"}]).to_string().as_bytes(),
        );
        assert_eq!(router.cdn_file_for("fts.rbxcdn.com", "/abc?other"), Some(Source::File("/tmp/x.png".into())));
        assert_eq!(router.cdn_file_for("fts.rbxcdn.com", "/zzz"), None);
    }

    #[test]
    fn unchanged_batch_is_none() {
        let router = AssetRouter::default();
        assert!(router.rewrite_batch_request(br#"[{"assetId": 5}]"#, &rules()).is_none());
        assert!(router.rewrite_batch_request(b"not json", &rules()).is_none());
    }

    #[test]
    fn legacy_endpoint() {
        let r = rules();
        assert_eq!(AssetRouter::rewrite_single("/v1/asset/?id=1000&x=1", &r), Single::Replace("/v1/asset/?id=2000&x=1".into()));
        assert_eq!(AssetRouter::rewrite_single("/v1/asset?ID=3000", &r), Single::Remove);
        assert_eq!(AssetRouter::rewrite_single("/v1/asset?id=4000", &r), Single::Serve(Source::File("/tmp/x.png".into())));
        assert_eq!(AssetRouter::rewrite_single("/v1/asset?id=7", &r), Single::Pass);
        assert_eq!(AssetRouter::rewrite_single("/v2/other?id=1000", &r), Single::Pass);
    }
}
