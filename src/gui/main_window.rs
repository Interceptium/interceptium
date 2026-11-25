//! The Interceptium window: proxy switch, config library, rules and the settings page.

use super::anim::{self, tween, tween_bool, tween_from};
use super::icons::{self, Icon};
use super::popup::{self, Event, Shell};
use super::theme::{self as t, alpha, mix};
use super::widgets::{self, Kind, Status};
use crate::app::Controller;
use crate::rules::{self, is_group, iter_rules, mode_label, rule_mode};
use egui::{pos2, vec2, Align2, Color32, CornerRadius, Id, Rect, Sense, Stroke, StrokeKind, Ui, UiBuilder};
use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::{mpsc, Arc, Mutex};
use std::time::SystemTime;

type Path_ = Vec<usize>;

enum Job {
    Done { ok: Option<String>, err: Option<String> },
}

enum PromptFor {
    NewConfig,
    RenameConfig(String),
}

enum ConfirmFor {
    DeleteConfig(String),
    DeleteRule(Path_),
    RemoveFromRoblox,
    Maintenance(crate::setup::Action),
}

struct RuleEditor {
    path: Option<Path_>,
    name: String,
    mode: usize, // 0 replace, 1 remove, 2 local file
    assets: Vec<(u64, String)>,
    value: String,
    error: String,
    original: Option<Map<String, Value>>,
}

enum PopupKind {
    Prompt { title: String, ok: String, value: String, error: String, purpose: PromptFor },
    Confirm { title: String, message: String, ok: String, danger: bool, action: ConfirmFor },
    Rule(RuleEditor),
}

struct Popup {
    shell: Shell,
    kind: PopupKind,
}

enum Act {
    Select(String),
    ToggleConfig(String, bool),
    NewConfig,
    Import,
    Rename,
    Export,
    DeleteConfig,
    AddRule,
    EditRule(Path_),
    DeleteRule(Path_),
    ToggleRule(Path_, bool),
    OpenSettings,
    CloseSettings,
    ToggleProxy,
    Launch,
    ClearCache,
    ToggleLog,
    OpenFolder,
    RemoveFromRoblox,
    Maintenance(crate::setup::Action),
    ApplyPort,
}

struct Toast {
    text: String,
    error: bool,
    at: f64,
}

#[derive(Clone)]
struct ConfigInfo {
    stamp: Option<(SystemTime, u64)>,
    total: usize,
    on: usize,
}

pub struct MainApp {
    ctl: Arc<Controller>,
    logo: egui::TextureHandle,
    names: Vec<String>,
    info: HashMap<String, ConfigInfo>,
    selected: String,
    config: Value,
    fresh_rows: HashSet<String>,
    vanishing: Vec<(String, f64)>,
    search: String,
    popups: Vec<Popup>,
    next_id: u64,
    settings_open: bool,
    tab: usize,
    log_open: bool,
    log: Vec<String>,
    log_rx: mpsc::Receiver<String>,
    job_tx: mpsc::Sender<Job>,
    job_rx: mpsc::Receiver<Job>,
    busy: usize,
    toast: Option<Toast>,
    port_text: String,
    last_scan: f64,
    install_dir: Option<PathBuf>,
    #[cfg(feature = "shots")]
    pub shots: super::shots::Shots,
}

fn rule_counts(config: &Value) -> (usize, usize) {
    let arr = config.get("replacement_rules").and_then(Value::as_array).cloned().unwrap_or_default();
    let all = iter_rules(&arr);
    let on = all.iter().filter(|r| r.get("enabled").map(rules::truthy).unwrap_or(true)).count();
    (all.len(), on)
}

fn plural(n: usize, word: &str) -> String {
    format!("{n} {word}{}", if n == 1 { "" } else { "s" })
}

fn entries_at<'a>(config: &'a mut Value, parent: &[usize]) -> Option<&'a mut Vec<Value>> {
    let mut arr = config.get_mut("replacement_rules")?.as_array_mut()?;
    for &i in parent {
        arr = arr.get_mut(i)?.get_mut("children")?.as_array_mut()?;
    }
    Some(arr)
}

fn rule_at<'a>(config: &'a mut Value, path: &[usize]) -> Option<&'a mut Value> {
    let (last, parent) = path.split_last()?;
    entries_at(config, parent)?.get_mut(*last)
}

fn targets_text(rule: &Map<String, Value>) -> String {
    let ids: Vec<String> = rule
        .get("replace_ids")
        .and_then(Value::as_array)
        .map(|a| a.iter().map(|v| match v { Value::String(s) => s.clone(), o => o.to_string() }).collect())
        .unwrap_or_default();
    if ids.is_empty() {
        return "No assets".into();
    }
    let shown: Vec<&str> = ids.iter().take(3).map(String::as_str).collect();
    let mut s = shown.join(", ");
    if ids.len() > 3 {
        s.push_str(&format!("  +{}", ids.len() - 3));
    }
    s
}

fn mode_color(mode: &str) -> Color32 {
    match mode {
        "remove" => t::DANGER,
        "local" => t::BLUE,
        "cdn" => t::WARN,
        _ => t::ACCENT,
    }
}

pub fn open_path(path: &std::path::Path) {
    #[cfg(windows)]
    {
        crate::winsys::open_folder(path);
    }
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open").arg(path).spawn();
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = std::process::Command::new("xdg-open").arg(path).spawn();
    }
}

impl MainApp {
    pub fn new(cc: &eframe::CreationContext<'_>, ctl: Arc<Controller>) -> MainApp {
        let ctx = cc.egui_ctx.clone();
        super::setup_context(&ctx);
        let (log_tx, log_rx) = mpsc::channel::<String>();
        let log_tx = Mutex::new(log_tx);
        let repaint = ctx.clone();
        crate::logbus::subscribe(move |line| {
            let _ = log_tx.lock().unwrap_or_else(|e| e.into_inner()).send(line.to_string());
            repaint.request_repaint();
        });
        let (job_tx, job_rx) = mpsc::channel();
        let settings = ctl.config.settings();
        let names = ctl.config.names();
        let selected = settings.enabled_configs.first().cloned().filter(|n| names.contains(n)).or_else(|| names.first().cloned()).unwrap_or_default();
        let config = ctl.config.load_config(&selected);
        let mut app = MainApp {
            logo: super::logo_texture(&ctx),
            names,
            info: HashMap::new(),
            selected,
            config,
            fresh_rows: HashSet::new(),
            vanishing: Vec::new(),
            search: String::new(),
            popups: Vec::new(),
            next_id: 1,
            settings_open: false,
            tab: 0,
            log_open: false,
            log: crate::logbus::history(),
            log_rx,
            job_tx,
            job_rx,
            busy: 0,
            toast: None,
            port_text: settings.port.to_string(),
            last_scan: 0.0,
            install_dir: crate::setup::install_location(),
            ctl,
            #[cfg(feature = "shots")]
            shots: super::shots::Shots::from_env(),
        };
        app.scan_configs();
        app
    }

    // -- data ----------------------------------------------------------------------------
    fn scan_configs(&mut self) {
        let names = self.ctl.config.names();
        for n in &names {
            let stamp = self.ctl.config.path_for(n).ok().and_then(|p| std::fs::metadata(p).ok()).and_then(|m| m.modified().ok().map(|t| (t, m.len())));
            let stale = self.info.get(n).map(|i| i.stamp != stamp).unwrap_or(true);
            if stale {
                let (total, on) = rule_counts(&self.ctl.config.load_config(n));
                self.info.insert(n.clone(), ConfigInfo { stamp, total, on });
            }
        }
        self.info.retain(|k, _| names.contains(k));
        if !names.contains(&self.selected) {
            self.selected = names.first().cloned().unwrap_or_default();
            self.config = self.ctl.config.load_config(&self.selected);
        } else if self.info.get(&self.selected).is_some() {
            // the selected file changed on disk: reload it unless we just saved it ourselves
            let on_disk = self.ctl.config.load_config(&self.selected);
            if on_disk != self.config {
                self.config = on_disk;
            }
        }
        self.names = names;
    }

    fn save(&mut self) {
        if self.selected.is_empty() {
            return;
        }
        if let Err(e) = self.ctl.config.save_config(&self.selected, &self.config) {
            self.notify(&format!("Could not save: {e}"), true);
        }
        let (total, on) = rule_counts(&self.config);
        let stamp = self.ctl.config.path_for(&self.selected).ok().and_then(|p| std::fs::metadata(p).ok()).and_then(|m| m.modified().ok().map(|t| (t, m.len())));
        self.info.insert(self.selected.clone(), ConfigInfo { stamp, total, on });
    }

    fn notify(&mut self, text: &str, error: bool) {
        self.toast = Some(Toast { text: text.to_string(), error, at: f64::NAN });
    }

    fn run_job(&mut self, ctx: &egui::Context, work: impl FnOnce() -> Result<Option<String>, String> + Send + 'static) {
        self.busy += 1;
        let tx = self.job_tx.clone();
        let repaint = ctx.clone();
        std::thread::spawn(move || {
            let r = work();
            let _ = tx.send(match r {
                Ok(ok) => Job::Done { ok, err: None },
                Err(e) => Job::Done { ok: None, err: Some(e) },
            });
            repaint.request_repaint();
        });
    }

    fn push(&mut self, ctx: &egui::Context, kind: PopupKind) {
        let id = self.next_id;
        self.next_id += 1;
        self.popups.push(Popup { shell: Shell::new(ctx, id), kind });
    }

    fn confirm(&mut self, ctx: &egui::Context, title: &str, message: &str, ok: &str, danger: bool, action: ConfirmFor) {
        self.push(ctx, PopupKind::Confirm { title: title.into(), message: message.into(), ok: ok.into(), danger, action });
    }

    // -- actions ---------------------------------------------------------------------------
    fn apply(&mut self, ctx: &egui::Context, act: Act) {
        match act {
            Act::Select(name) => {
                if name != self.selected {
                    self.selected = name.clone();
                    self.config = self.ctl.config.load_config(&name);
                    self.search.clear();
                    anim::reset(ctx, Id::unique("rules-fade"));
                }
            }
            Act::ToggleConfig(name, on) => {
                self.ctl.config.set_enabled(&name, on);
            }
            Act::NewConfig => self.push(ctx, PopupKind::Prompt { title: "New config".into(), ok: "Create".into(), value: String::new(), error: String::new(), purpose: PromptFor::NewConfig }),
            Act::Rename => {
                if !self.selected.is_empty() {
                    let old = self.selected.clone();
                    self.push(ctx, PopupKind::Prompt { title: "Rename config".into(), ok: "Rename".into(), value: old.clone(), error: String::new(), purpose: PromptFor::RenameConfig(old) });
                }
            }
            Act::Import => {
                let files = rfd::FileDialog::new().set_title("Import configs").add_filter("JSON", &["json"]).add_filter("All files", &["*"]).pick_files();
                let mut last = None;
                let mut errors = Vec::new();
                for f in files.unwrap_or_default() {
                    match self.ctl.config.import_file(&f) {
                        Ok(name) => {
                            self.fresh_rows.insert(name.clone());
                            last = Some(name);
                        }
                        Err(e) => errors.push(format!("{}: {e}", f.file_name().unwrap_or_default().to_string_lossy())),
                    }
                }
                self.scan_configs();
                if let Some(n) = last {
                    self.apply(ctx, Act::Select(n));
                }
                if !errors.is_empty() {
                    self.notify(&errors.join("\n"), true);
                }
            }
            Act::Export => {
                if self.selected.is_empty() {
                    return;
                }
                let dest = rfd::FileDialog::new().set_title("Export config").set_file_name(format!("{}.json", self.selected)).add_filter("JSON", &["json"]).save_file();
                if let Some(d) = dest {
                    self.save();
                    match self.ctl.config.export(&self.selected, &d) {
                        Ok(()) => self.notify("Config exported", false),
                        Err(e) => self.notify(&e, true),
                    }
                }
            }
            Act::DeleteConfig => {
                if !self.selected.is_empty() {
                    let n = self.selected.clone();
                    self.confirm(ctx, "Delete config", &format!("\"{n}\" will be permanently deleted."), "Delete", true, ConfirmFor::DeleteConfig(n));
                }
            }
            Act::AddRule => {
                let ed = RuleEditor { path: None, name: String::new(), mode: 0, assets: vec![(0, String::new())], value: String::new(), error: String::new(), original: None };
                self.push(ctx, PopupKind::Rule(ed));
            }
            Act::EditRule(path) => {
                let Some(rule) = rule_at(&mut self.config, &path).and_then(|v| v.as_object().cloned()) else { return };
                let mode = match rule_mode(&rule) { "remove" => 1, "local" => 2, _ => 0 };
                let assets: Vec<(u64, String)> = rule
                    .get("replace_ids")
                    .and_then(Value::as_array)
                    .map(|a| a.iter().map(|v| match v { Value::String(s) => s.clone(), o => o.to_string() }).collect::<Vec<_>>())
                    .unwrap_or_default()
                    .into_iter()
                    .enumerate()
                    .map(|(i, s)| (i as u64, s))
                    .collect();
                let value = match mode {
                    0 => match rule.get("with_id") { Some(Value::Null) | None => String::new(), Some(Value::String(s)) => s.clone(), Some(v) => v.to_string() },
                    2 => rule.get("local_path").and_then(Value::as_str).unwrap_or("").to_string(),
                    _ => String::new(),
                };
                let ed = RuleEditor {
                    path: Some(path),
                    name: rule.get("name").and_then(Value::as_str).unwrap_or("").to_string(),
                    mode,
                    assets: if assets.is_empty() { vec![(0, String::new())] } else { assets },
                    value,
                    error: String::new(),
                    original: Some(rule),
                };
                self.push(ctx, PopupKind::Rule(ed));
            }
            Act::DeleteRule(path) => {
                let name = rule_at(&mut self.config, &path).and_then(|r| r.get("name")).and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("This rule").to_string();
                self.confirm(ctx, "Delete rule", &format!("\"{name}\" will be removed from this config."), "Delete", true, ConfirmFor::DeleteRule(path));
            }
            Act::ToggleRule(path, on) => {
                if let Some(Value::Object(r)) = rule_at(&mut self.config, &path) {
                    r.insert("enabled".into(), Value::from(on));
                    self.save();
                }
            }
            Act::OpenSettings => {
                self.settings_open = true;
                self.port_text = self.ctl.config.settings().port.to_string();
                self.install_dir = crate::setup::install_location();
            }
            Act::CloseSettings => self.settings_open = false,
            Act::ToggleProxy => {
                let ctl = self.ctl.clone();
                self.run_job(ctx, move || {
                    if ctl.running() {
                        ctl.stop();
                        Ok(None)
                    } else {
                        ctl.start().map(|_| None)
                    }
                });
            }
            Act::Launch => {
                let ctl = self.ctl.clone();
                self.run_job(ctx, move || ctl.launch_roblox().map(|_| None));
            }
            Act::ClearCache => {
                self.run_job(ctx, move || {
                    if crate::roblox::is_player_running() {
                        return Err("Close Roblox first.".into());
                    }
                    let n = crate::roblox::clear_cache();
                    Ok(Some(if n == 0 { "Roblox cache is already empty".into() } else { "Roblox cache cleared".into() }))
                });
            }
            Act::ToggleLog => self.log_open = !self.log_open,
            Act::OpenFolder => open_path(&self.ctl.config.root),
            Act::RemoveFromRoblox => self.confirm(
                ctx,
                "Remove from Roblox",
                "Interceptium's certificate and setting will be removed from every Roblox install. The proxy turns them back on when it starts.",
                "Remove",
                true,
                ConfirmFor::RemoveFromRoblox,
            ),
            Act::Maintenance(action) => {
                use crate::setup::Action as A;
                let (title, msg, ok, danger) = match action {
                    A::Repair => ("Repair", "Interceptium will close and its files will be restored.", "Repair", false),
                    A::Reinstall => ("Reinstall", "Interceptium will close, be removed and installed again. Configs are kept.", "Reinstall", false),
                    _ => ("Uninstall", "Interceptium will close and open its uninstaller.", "Uninstall", true),
                };
                self.confirm(ctx, title, msg, ok, danger, ConfirmFor::Maintenance(action));
            }
            Act::ApplyPort => match self.port_text.trim().parse::<u16>() {
                Ok(p) if p >= 1024 => {
                    self.ctl.config.update_settings(|s| s.port = p);
                    if self.ctl.running() {
                        let ctl = self.ctl.clone();
                        self.run_job(ctx, move || {
                            ctl.stop();
                            ctl.start().map(|port| Some(format!("Proxy restarted on port {port}")))
                        });
                    } else {
                        self.notify("Port saved", false);
                    }
                }
                _ => self.notify("Enter a port from 1024 to 65535.", true),
            },
        }
    }

    fn finish_popup(&mut self, ctx: &egui::Context, index: usize, ok: bool) {
        let mut close = true;
        let kind = std::mem::replace(&mut self.popups[index].kind, PopupKind::Prompt { title: String::new(), ok: String::new(), value: String::new(), error: String::new(), purpose: PromptFor::NewConfig });
        let kind = if !ok {
            kind
        } else {
            match kind {
                PopupKind::Prompt { title, ok: okl, value, purpose, .. } => {
                    let result = match &purpose {
                        PromptFor::NewConfig => self.ctl.config.create(&value),
                        PromptFor::RenameConfig(old) => self.ctl.config.rename(old, &value),
                    };
                    match result {
                        Ok(name) => {
                            if let PromptFor::NewConfig = purpose {
                                self.fresh_rows.insert(name.clone());
                            }
                            self.scan_configs();
                            self.selected.clear();
                            self.apply(ctx, Act::Select(name));
                            PopupKind::Prompt { title, ok: okl, value, error: String::new(), purpose }
                        }
                        Err(e) => {
                            close = false;
                            PopupKind::Prompt { title, ok: okl, value, error: e, purpose }
                        }
                    }
                }
                PopupKind::Confirm { title, message, ok: okl, danger, action } => {
                    match &action {
                        ConfirmFor::DeleteConfig(name) => {
                            self.vanishing.push((name.clone(), ctx.input(|i| i.time)));
                            self.ctl.config.delete(name);
                            self.scan_configs();
                        }
                        ConfirmFor::DeleteRule(path) => {
                            if let Some((last, parent)) = path.split_last() {
                                if let Some(arr) = entries_at(&mut self.config, parent) {
                                    if *last < arr.len() {
                                        arr.remove(*last);
                                    }
                                }
                            }
                            self.save();
                        }
                        ConfirmFor::RemoveFromRoblox => {
                            let ctl = self.ctl.clone();
                            self.run_job(ctx, move || {
                                ctl.remove_from_roblox();
                                Ok(Some("Removed from Roblox".into()))
                            });
                        }
                        ConfirmFor::Maintenance(a) => match crate::setup::start_maintenance(*a) {
                            Ok(()) => {
                                self.ctl.stop();
                                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                            }
                            Err(e) => self.notify(&e, true),
                        },
                    }
                    PopupKind::Confirm { title, message, ok: okl, danger, action }
                }
                PopupKind::Rule(mut ed) => {
                    match self.build_rule(&ed) {
                        Ok(rule) => {
                            match &ed.path {
                                Some(path) => {
                                    if let Some(slot) = rule_at(&mut self.config, path) {
                                        *slot = rule;
                                    }
                                }
                                None => {
                                    if let Some(arr) = entries_at(&mut self.config, &[]) {
                                        arr.push(rule);
                                    }
                                }
                            }
                            self.save();
                        }
                        Err(e) => {
                            ed.error = e;
                            close = false;
                        }
                    }
                    PopupKind::Rule(ed)
                }
            }
        };
        self.popups[index].kind = kind;
        if close {
            self.popups[index].shell.close(ctx);
        }
    }

    fn build_rule(&self, ed: &RuleEditor) -> Result<Value, String> {
        let mut targets = Vec::new();
        for (_, a) in &ed.assets {
            if a.trim().is_empty() {
                continue;
            }
            targets.push(rules::parse_target(a)?);
        }
        if targets.is_empty() {
            return Err("Enter at least one asset ID or type.".into());
        }
        let mode = ["id", "remove", "local"][ed.mode.min(2)];
        let value = ed.value.trim().to_string();
        if mode == "id" && !value.is_empty() {
            match value.parse::<i64>() {
                Ok(n) if n > rules::TYPE_ID_MAX => {}
                _ => return Err("Enter a numeric replacement asset ID.".into()),
            }
        }
        if mode == "local" {
            if value.is_empty() {
                return Err("Choose a file.".into());
            }
            let resolved = self.ctl.config.resolve_local(&value);
            if !std::path::Path::new(&resolved).is_file() {
                return Err("File not found.".into());
            }
        }
        if mode == "id" && value.is_empty() {
            return Err("Enter a numeric replacement asset ID.".into());
        }
        let name = if ed.name.trim().is_empty() { targets[0].clone() } else { ed.name.trim().to_string() };
        let enabled = ed.original.as_ref().and_then(|o| o.get("enabled")).map(rules::truthy).unwrap_or(true);
        let fresh = rules::new_rule(&name, &targets, mode, &value, enabled);
        // keep keys this editor doesn't know about
        let mut out = ed.original.clone().unwrap_or_default();
        for k in ["with_id", "local_path", "cdn_url", "remove"] {
            out.remove(k);
        }
        if let Value::Object(f) = fresh {
            for (k, v) in f {
                out.insert(k, v);
            }
        }
        Ok(Value::Object(out))
    }

    // -- drawing -----------------------------------------------------------------------------
    fn header(&mut self, ui: &mut Ui, rect: Rect, acts: &mut Vec<Act>) {
        let p = ui.painter();
        let logo_rect = Rect::from_center_size(pos2(rect.left() + 20.0, rect.center().y), vec2(40.0, 40.0));
        p.image(self.logo.id(), logo_rect, Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
        p.text(pos2(rect.left() + 52.0, rect.center().y), Align2::LEFT_CENTER, "Interceptium", t::semi(21.0), t::TEXT);
        let right = Rect::from_min_max(pos2(rect.right() - 320.0, rect.top()), rect.max);
        let mut child = ui.new_child(UiBuilder::new().max_rect(right).layout(egui::Layout::right_to_left(egui::Align::Center)));
        if widgets::icon_button(&mut child, "gear", Icon::Gear, 38.0, t::TEXT, "Settings").clicked() {
            acts.push(Act::OpenSettings);
        }
        child.add_space(6.0);
        let (status, text) = if self.busy > 0 {
            (Status::Busy, "Working")
        } else if self.ctl.running() {
            (Status::Running, "Running")
        } else {
            (Status::Stopped, "Stopped")
        };
        widgets::status_pill(&mut child, status, text);
    }

    fn sidebar(&mut self, ui: &mut Ui, rect: Rect, acts: &mut Vec<Act>) {
        let running = self.ctl.running();
        // proxy card
        let card_h = 356.0;
        let proxy = Rect::from_min_size(rect.min, vec2(rect.width(), card_h));
        ui.painter().rect_filled(proxy, CornerRadius::same(22), t::CARD);
        ui.painter().rect_stroke(proxy, CornerRadius::same(22), Stroke::new(1.0, alpha(t::LINE, 0.6)), StrokeKind::Inside);
        let mut c = ui.new_child(UiBuilder::new().max_rect(proxy.shrink(18.0)).layout(egui::Layout::top_down(egui::Align::Center)));
        c.add_space(6.0);
        if widgets::power_button(&mut c, running, self.busy > 0).clicked() {
            acts.push(Act::ToggleProxy);
        }
        c.add_space(6.0);
        let on = tween_bool(c.ctx(), Id::unique("run-text"), running, 0.3);
        widgets::label(&mut c, if running { "Proxy running" } else { "Proxy off" }, t::semi(15.0), mix(t::MUTED, t::TEXT, on));
        widgets::label(&mut c, &self.ctl.proxy_url().trim_start_matches("http://").to_string(), t::mono(12.5), t::FAINT);
        c.add_space(10.0);
        let w = c.available_width();
        if widgets::button(&mut c, "launch", "Launch Roblox", Some(Icon::Play), Kind::Primary, w, 42.0, self.busy == 0).clicked() {
            acts.push(Act::Launch);
        }
        if widgets::button(&mut c, "clear", "Clear cache", Some(Icon::Broom), Kind::Ghost, w, 40.0, self.busy == 0).clicked() {
            acts.push(Act::ClearCache);
        }

        // configs card
        let list = Rect::from_min_max(pos2(rect.left(), proxy.bottom() + 16.0), rect.max);
        ui.painter().rect_filled(list, CornerRadius::same(22), t::CARD);
        ui.painter().rect_stroke(list, CornerRadius::same(22), Stroke::new(1.0, alpha(t::LINE, 0.6)), StrokeKind::Inside);
        let head = Rect::from_min_size(list.min + vec2(18.0, 12.0), vec2(list.width() - 30.0, 36.0));
        ui.painter().text(pos2(head.left(), head.center().y), Align2::LEFT_CENTER, "Configs", t::semi(15.0), t::TEXT);
        let mut hc = ui.new_child(UiBuilder::new().max_rect(head).layout(egui::Layout::right_to_left(egui::Align::Center)));
        if widgets::icon_button(&mut hc, "new-config", Icon::Plus, 32.0, t::ACCENT, "New config").clicked() {
            acts.push(Act::NewConfig);
        }
        if widgets::icon_button(&mut hc, "import", Icon::Import, 32.0, t::TEXT, "Import").clicked() {
            acts.push(Act::Import);
        }
        let body = Rect::from_min_max(pos2(list.left() + 8.0, head.bottom() + 6.0), pos2(list.right() - 8.0, list.bottom() - 8.0));
        let mut bc = ui.new_child(UiBuilder::new().max_rect(body));
        egui::ScrollArea::vertical().id_salt("configs").auto_shrink([false, false]).show(&mut bc, |ui| {
            ui.spacing_mut().item_spacing.y = 4.0;
            let now = ui.input(|i| i.time);
            let mut rows: Vec<(String, bool)> = self.names.iter().map(|n| (n.clone(), false)).collect();
            self.vanishing.retain(|(_, at)| now - at < 0.26);
            for (name, _) in &self.vanishing {
                if !rows.iter().any(|(n, _)| n == name) {
                    rows.push((name.clone(), true));
                }
            }
            for (name, gone) in rows {
                self.config_row(ui, &name, gone, acts);
            }
        });
    }

    fn config_row(&mut self, ui: &mut Ui, name: &str, gone: bool, acts: &mut Vec<Act>) {
        let ctx = ui.ctx().clone();
        let id = Id::unique(("cfg-row", name));
        let grow = if gone {
            let at = self.vanishing.iter().find(|(n, _)| n == name).map(|v| v.1).unwrap_or(0.0);
            1.0 - anim::progress(&ctx, at, 0.24, anim::ease_in_out_cubic)
        } else if self.fresh_rows.contains(name) {
            tween_from(&ctx, id.with("grow"), 0.0, 1.0, 0.28, anim::ease_out_cubic)
        } else {
            1.0
        };
        let full_h = 58.0;
        let h = full_h * grow;
        let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), h), if gone { Sense::hover() } else { Sense::click() });
        if h < 1.0 || !ui.is_rect_visible(rect) {
            return;
        }
        let selected = !gone && name == self.selected;
        let sel = tween_bool(&ctx, id.with("sel"), selected, 0.22);
        let hover = tween_bool(&ctx, id.with("hover"), resp.hovered() && !gone, 0.14);
        let painter = ui.painter().with_clip_rect(rect.intersect(ui.clip_rect()));
        let body = Rect::from_min_size(rect.min, vec2(rect.width(), full_h)).translate(vec2(0.0, (h - full_h) / 2.0));
        let bg = mix(mix(alpha(t::CARD_HOVER, 0.0), t::CARD_HOVER, hover), t::CARD_SELECTED, sel);
        painter.rect_filled(body, CornerRadius::same(16), bg.gamma_multiply(grow));
        if sel > 0.01 {
            let bar = Rect::from_center_size(pos2(body.left() + 4.0, body.center().y), vec2(3.0, 24.0 * sel));
            painter.rect_filled(bar, CornerRadius::same(2), alpha(t::ACCENT, sel * grow));
        }
        let info = self.info.get(name).cloned();
        let fade = grow;
        let text_w = body.width() - 90.0;
        let title = widgets::elide(ui, name, &t::semi(14.0), text_w);
        painter.text(pos2(body.left() + 16.0, body.top() + 20.0), Align2::LEFT_CENTER, title, t::semi(14.0), t::TEXT.gamma_multiply(fade));
        let count = info.map(|i| plural(i.total, "rule")).unwrap_or_default();
        painter.text(pos2(body.left() + 16.0, body.top() + 39.0), Align2::LEFT_CENTER, count, t::regular(12.5), t::MUTED.gamma_multiply(fade));
        if !gone {
            let mut enabled = self.ctl.config.is_enabled(name);
            let toggle_rect = Rect::from_center_size(pos2(body.right() - 34.0, body.center().y), vec2(40.0, 22.0));
            let mut tc = ui.new_child(UiBuilder::new().max_rect(toggle_rect));
            if widgets::toggle(&mut tc, ("cfg-toggle", name), &mut enabled).changed() {
                acts.push(Act::ToggleConfig(name.to_string(), enabled));
            } else if resp.clicked() {
                acts.push(Act::Select(name.to_string()));
            }
            resp.on_hover_cursor(egui::CursorIcon::PointingHand);
        }
    }

    fn rules_panel(&mut self, ui: &mut Ui, rect: Rect, acts: &mut Vec<Act>) {
        ui.painter().rect_filled(rect, CornerRadius::same(22), t::CARD);
        ui.painter().rect_stroke(rect, CornerRadius::same(22), Stroke::new(1.0, alpha(t::LINE, 0.6)), StrokeKind::Inside);
        let inner = rect.shrink2(vec2(22.0, 18.0));
        let ctx = ui.ctx().clone();
        let fade = tween_from(&ctx, Id::unique("rules-fade"), 0.0, 1.0, 0.25, anim::ease_out_cubic);
        let (total, on) = self.info.get(&self.selected).map(|i| (i.total, i.on)).unwrap_or((0, 0));
        let head = Rect::from_min_size(inner.min, vec2(inner.width(), 52.0));
        let title = if self.selected.is_empty() { "No config".to_string() } else { widgets::elide(ui, &self.selected, &t::semi(22.0), head.width() - 330.0) };
        ui.painter().text(pos2(head.left(), head.top() + 15.0), Align2::LEFT_CENTER, title, t::semi(22.0), t::TEXT.gamma_multiply(fade));
        let sub = format!("{}, {} enabled", plural(total, "rule"), on);
        ui.painter().text(pos2(head.left(), head.top() + 40.0), Align2::LEFT_CENTER, sub, t::regular(13.0), t::MUTED.gamma_multiply(fade));
        let mut hc = ui.new_child(UiBuilder::new().max_rect(head).layout(egui::Layout::right_to_left(egui::Align::Center)));
        let has = !self.selected.is_empty();
        if widgets::button(&mut hc, "add-rule", "Add rule", Some(Icon::Plus), Kind::Primary, 0.0, 40.0, has).clicked() {
            acts.push(Act::AddRule);
        }
        hc.add_space(6.0);
        if has {
            if widgets::icon_button(&mut hc, "del-config", Icon::Trash, 36.0, t::DANGER, "Delete config").clicked() {
                acts.push(Act::DeleteConfig);
            }
            if widgets::icon_button(&mut hc, "export", Icon::Export, 36.0, t::TEXT, "Export").clicked() {
                acts.push(Act::Export);
            }
            if widgets::icon_button(&mut hc, "rename", Icon::Pencil, 36.0, t::TEXT, "Rename").clicked() {
                acts.push(Act::Rename);
            }
        }
        // search
        let search_rect = Rect::from_min_size(pos2(inner.left(), head.bottom() + 12.0), vec2(inner.width(), 40.0));
        let mut sc = ui.new_child(UiBuilder::new().max_rect(search_rect));
        widgets::text_field(&mut sc, "search", &mut self.search, "Search rules", search_rect.width());
        icons::draw(ui.painter(), Icon::Search, pos2(search_rect.right() - 22.0, search_rect.center().y), 16.0, t::FAINT);

        let list = Rect::from_min_max(pos2(inner.left() - 6.0, search_rect.bottom() + 12.0), pos2(inner.right() + 6.0, rect.bottom() - 10.0));
        let mut lc = ui.new_child(UiBuilder::new().max_rect(list));
        lc.multiply_opacity(fade);
        let config = std::mem::take(&mut self.config);
        let entries = config.get("replacement_rules").and_then(Value::as_array).cloned().unwrap_or_default();
        let query = self.search.trim().to_lowercase();
        egui::ScrollArea::vertical().id_salt(("rules", &self.selected)).auto_shrink([false, false]).show(&mut lc, |ui| {
            ui.spacing_mut().item_spacing.y = 8.0;
            ui.add_space(2.0);
            if !query.is_empty() {
                let mut hits = Vec::new();
                collect_matches(&entries, &mut Vec::new(), &query, &mut hits);
                if hits.is_empty() {
                    empty_state(ui, "No results", None, acts);
                }
                for (path, rule) in hits {
                    rule_card(ui, &path, &rule, 0, acts);
                }
            } else if entries.is_empty() {
                empty_state(ui, "No rules", if has { Some("Add rule") } else { None }, acts);
            } else {
                draw_entries(ui, &entries, &mut Vec::new(), 0, acts);
            }
            ui.add_space(6.0);
        });
        self.config = config;
    }

    fn log_panel(&mut self, ui: &mut Ui, rect: Rect, open: f32, acts: &mut Vec<Act>) {
        ui.painter().rect_filled(rect, CornerRadius::same(18), t::SURFACE);
        ui.painter().rect_stroke(rect, CornerRadius::same(18), Stroke::new(1.0, alpha(t::LINE, 0.6)), StrokeKind::Inside);
        let head = Rect::from_min_size(rect.min, vec2(rect.width(), 40.0));
        let resp = ui.interact(head, Id::unique("log-head"), Sense::click()).on_hover_cursor(egui::CursorIcon::PointingHand);
        if resp.clicked() {
            acts.push(Act::ToggleLog);
        }
        let hover = tween_bool(ui.ctx(), Id::unique("log-hover"), resp.hovered(), 0.14);
        ui.painter().text(pos2(head.left() + 18.0, head.center().y), Align2::LEFT_CENTER, "Activity", t::semi(13.5), mix(t::MUTED, t::TEXT, hover));
        let last = self.log.last().cloned().unwrap_or_default();
        if open < 0.5 && !last.is_empty() {
            let shown = widgets::elide(ui, &last, &t::mono(12.0), head.width() - 180.0);
            ui.painter().text(pos2(head.left() + 100.0, head.center().y), Align2::LEFT_CENTER, shown, t::mono(12.0), t::FAINT.gamma_multiply(1.0 - open * 2.0));
        }
        // chevron turns as the panel opens
        let c = pos2(head.right() - 24.0, head.center().y);
        let angle = std::f32::consts::PI * open;
        let pts = [vec2(-5.0, 2.5), vec2(0.0, -2.5), vec2(5.0, 2.5)];
        let rot = egui::emath::Rot2::from_angle(angle + std::f32::consts::PI);
        let pts: Vec<egui::Pos2> = pts.iter().map(|v| c + rot * *v).collect();
        ui.painter().add(egui::Shape::line(pts, Stroke::new(1.8, mix(t::MUTED, t::TEXT, hover))));
        if open > 0.01 {
            let body = Rect::from_min_max(pos2(rect.left() + 14.0, head.bottom()), pos2(rect.right() - 10.0, rect.bottom() - 10.0));
            let mut bc = ui.new_child(UiBuilder::new().max_rect(body));
            bc.multiply_opacity(open);
            egui::ScrollArea::vertical().id_salt("log").stick_to_bottom(true).auto_shrink([false, false]).show(&mut bc, |ui| {
                ui.spacing_mut().item_spacing.y = 2.0;
                let start = self.log.len().saturating_sub(400);
                for line in &self.log[start..] {
                    ui.add(egui::Label::new(egui::RichText::new(line).font(t::mono(12.0)).color(t::MUTED)).wrap());
                }
            });
        }
    }

    fn main_shell(&mut self, ui: &mut Ui, rect: Rect, acts: &mut Vec<Act>) {
        let pad = 22.0;
        let area = rect.shrink(pad);
        let header = Rect::from_min_size(area.min, vec2(area.width(), 46.0));
        self.header(ui, header, acts);
        let open = tween_from(ui.ctx(), Id::unique("log-open"), 0.0, if self.log_open { 1.0 } else { 0.0 }, 0.30, anim::ease_in_out_cubic);
        let log_h = 40.0 + 150.0 * open;
        let log_rect = Rect::from_min_max(pos2(area.left(), area.bottom() - log_h), area.max);
        let body = Rect::from_min_max(pos2(area.left(), header.bottom() + 18.0), pos2(area.right(), log_rect.top() - 16.0));
        let side_w = 300.0;
        let side = Rect::from_min_size(body.min, vec2(side_w, body.height()));
        let main = Rect::from_min_max(pos2(side.right() + 18.0, body.top()), body.max);
        self.sidebar(ui, side, acts);
        self.rules_panel(ui, main, acts);
        self.log_panel(ui, log_rect, open, acts);
    }

    fn settings_page(&mut self, ui: &mut Ui, rect: Rect, acts: &mut Vec<Act>) {
        let area = rect.shrink(22.0);
        let header = Rect::from_min_size(area.min, vec2(area.width(), 46.0));
        let mut hc = ui.new_child(UiBuilder::new().max_rect(header).layout(egui::Layout::left_to_right(egui::Align::Center)));
        if widgets::icon_button(&mut hc, "back", Icon::Back, 38.0, t::TEXT, "Back").clicked() {
            acts.push(Act::CloseSettings);
        }
        hc.add_space(6.0);
        widgets::label(&mut hc, "Settings", t::semi(21.0), t::TEXT);
        if ui.input(|i| i.key_pressed(egui::Key::Escape)) && self.popups.is_empty() {
            acts.push(Act::CloseSettings);
        }
        let col_w = area.width().min(720.0);
        let left = area.center().x - col_w / 2.0;
        let tabs_rect = Rect::from_min_size(pos2(left, header.bottom() + 20.0), vec2(col_w, 38.0));
        let mut tc = ui.new_child(UiBuilder::new().max_rect(tabs_rect));
        widgets::segmented(&mut tc, "settings-tabs", &["General", "Roblox", "Installation"], &mut self.tab, col_w);
        // tab content slides with the selection
        let pos = tween(ui.ctx(), Id::unique("tab-pos"), self.tab as f32, 0.30);
        let content = Rect::from_min_max(pos2(left, tabs_rect.bottom() + 20.0), pos2(left + col_w, area.bottom()));
        for i in 0..3 {
            let offset = i as f32 - pos;
            if offset.abs() >= 1.0 {
                continue;
            }
            let r = content.translate(vec2(offset * (col_w + 40.0), 0.0));
            let mut cc = ui.new_child(UiBuilder::new().max_rect(r).layout(egui::Layout::top_down(egui::Align::Min)));
            cc.set_clip_rect(content.expand2(vec2(6.0, 12.0)));
            cc.multiply_opacity(1.0 - offset.abs());
            if i != self.tab {
                cc.disable();
            }
            match i {
                0 => self.tab_general(&mut cc, col_w, acts),
                1 => self.tab_roblox(&mut cc, col_w, acts),
                _ => self.tab_install(&mut cc, col_w, acts),
            }
        }
    }

    fn setting_row(ui: &mut Ui, width: f32, title: &str, detail: &str, first: bool, control: impl FnOnce(&mut Ui)) {
        if !first {
            let (r, _) = ui.allocate_exact_size(vec2(width - 32.0, 1.0), Sense::hover());
            ui.painter().rect_filled(r, CornerRadius::ZERO, alpha(t::LINE, 0.7));
        }
        let (rect, _) = ui.allocate_exact_size(vec2(width - 32.0, 64.0), Sense::hover());
        let ty = if detail.is_empty() { rect.center().y } else { rect.center().y - 9.0 };
        ui.painter().text(pos2(rect.left(), ty), Align2::LEFT_CENTER, title, t::semi(14.5), t::TEXT);
        if !detail.is_empty() {
            let d = widgets::elide(ui, detail, &t::regular(12.5), rect.width() - 240.0);
            ui.painter().text(pos2(rect.left(), rect.center().y + 11.0), Align2::LEFT_CENTER, d, t::regular(12.5), t::MUTED);
        }
        let mut c = ui.new_child(UiBuilder::new().max_rect(rect).layout(egui::Layout::right_to_left(egui::Align::Center)));
        control(&mut c);
    }

    fn tab_general(&mut self, ui: &mut Ui, w: f32, acts: &mut Vec<Act>) {
        let s = self.ctl.config.settings();
        widgets::card().show(ui, |ui| {
            ui.set_width(w - 34.0);
            ui.spacing_mut().item_spacing.y = 0.0;
            let mut clear = s.clear_cache_on_launch;
            Self::setting_row(ui, w, "Clear cache on launch", "Roblox loads replaced assets fresh", true, |ui| {
                if widgets::toggle(ui, "set-clear", &mut clear).changed() {
                    self.ctl.config.update_settings(|s| s.clear_cache_on_launch = clear);
                }
            });
            let mut capture = s.auto_capture_launches;
            Self::setting_row(ui, w, "Reroute website launches", "Games started from the browser go through the proxy", false, |ui| {
                if widgets::toggle(ui, "set-capture", &mut capture).changed() {
                    self.ctl.config.update_settings(|s| s.auto_capture_launches = capture);
                }
            });
            let mut port_text = std::mem::take(&mut self.port_text);
            Self::setting_row(ui, w, "Port", "Local address the proxy listens on", false, |ui| {
                if widgets::button(ui, "apply-port", "Apply", None, Kind::Ghost, 0.0, 38.0, true).clicked() {
                    acts.push(Act::ApplyPort);
                }
                ui.add_space(4.0);
                widgets::text_field(ui, "port", &mut port_text, "58443", 110.0);
            });
            self.port_text = port_text;
        });
    }

    fn tab_roblox(&mut self, ui: &mut Ui, w: f32, acts: &mut Vec<Act>) {
        widgets::card().show(ui, |ui| {
            ui.set_width(w - 34.0);
            ui.spacing_mut().item_spacing.y = 0.0;
            Self::setting_row(ui, w, "Roblox cache", "Deletes downloaded assets so changes show up", true, |ui| {
                if widgets::button(ui, "s-clear", "Clear cache", Some(Icon::Broom), Kind::Ghost, 0.0, 38.0, self.busy == 0).clicked() {
                    acts.push(Act::ClearCache);
                }
            });
            let root = self.ctl.config.root.to_string_lossy().into_owned();
            Self::setting_row(ui, w, "Configs and settings", &root, false, |ui| {
                if widgets::button(ui, "s-open", "Open folder", Some(Icon::Folder), Kind::Ghost, 0.0, 38.0, true).clicked() {
                    acts.push(Act::OpenFolder);
                }
            });
            Self::setting_row(ui, w, "Certificate in Roblox", "Takes Interceptium's changes out of Roblox", false, |ui| {
                if widgets::button(ui, "s-remove", "Remove", Some(Icon::Shield), Kind::Danger, 0.0, 38.0, self.busy == 0).clicked() {
                    acts.push(Act::RemoveFromRoblox);
                }
            });
        });
    }

    fn tab_install(&mut self, ui: &mut Ui, w: f32, acts: &mut Vec<Act>) {
        let dir = self.install_dir.clone();
        widgets::card().show(ui, |ui| {
            ui.set_width(w - 34.0);
            ui.spacing_mut().item_spacing.y = 0.0;
            Self::setting_row(ui, w, "Version", "", true, |ui| {
                widgets::label(ui, crate::VERSION, t::semi(14.0), t::MUTED);
            });
            let loc = dir.as_ref().map(|d| d.to_string_lossy().into_owned()).unwrap_or_else(|| "Not installed with Interceptium Setup".into());
            Self::setting_row(ui, w, "Location", &loc, false, |ui| {
                if let Some(d) = &dir {
                    if widgets::icon_button(ui, "open-install", Icon::Folder, 36.0, t::TEXT, "Open folder").clicked() {
                        open_path(d);
                    }
                }
            });
        });
        if dir.is_some() {
            ui.add_space(16.0);
            ui.horizontal(|ui| {
                ui.spacing_mut().item_spacing.x = 10.0;
                let bw = (w - 20.0) / 3.0;
                use crate::setup::Action as A;
                if widgets::button(ui, "m-repair", "Repair", Some(Icon::Wrench), Kind::Ghost, bw, 44.0, true).clicked() {
                    acts.push(Act::Maintenance(A::Repair));
                }
                if widgets::button(ui, "m-reinstall", "Reinstall", Some(Icon::Refresh), Kind::Ghost, bw, 44.0, true).clicked() {
                    acts.push(Act::Maintenance(A::Reinstall));
                }
                if widgets::button(ui, "m-uninstall", "Uninstall", Some(Icon::Trash), Kind::Danger, bw, 44.0, true).clicked() {
                    acts.push(Act::Maintenance(A::Uninstall));
                }
            });
        }
    }

    fn popups(&mut self, ctx: &egui::Context) {
        let mut finished = Vec::new();
        let mut events = Vec::new();
        let top = self.popups.iter().rposition(|p| !p.shell.is_closing());
        for (i, p) in self.popups.iter_mut().enumerate() {
            let Some(vis) = p.shell.visibility(ctx) else {
                finished.push(i);
                continue;
            };
            let is_top = Some(i) == top;
            let ev = match &mut p.kind {
                PopupKind::Prompt { title, ok, value, error, .. } => {
                    let mut enter = false;
                    let ev = popup::show(ctx, &p.shell, vis, 420.0, title, ok, Kind::Primary, |ui| {
                        widgets::label(ui, "Name", t::semi(12.5), t::MUTED);
                        let r = widgets::text_field(ui, ("prompt", p.shell.id), value, "Config name", 420.0);
                        if !p.shell.is_closing() && anim::progress(ui.ctx(), p.shell.opened, 0.05, |x| x) < 1.0 {
                            r.request_focus();
                        }
                        if r.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)) {
                            enter = true;
                        }
                        if !error.is_empty() {
                            widgets::label(ui, error, t::regular(12.5), t::DANGER);
                        }
                    });
                    if enter { Event::Ok } else { ev }
                }
                PopupKind::Confirm { title, message, ok, danger, .. } => popup::show(ctx, &p.shell, vis, 420.0, title, ok, if *danger { Kind::Danger } else { Kind::Primary }, |ui| {
                    ui.add(egui::Label::new(egui::RichText::new(message.as_str()).font(t::regular(14.0)).color(t::MUTED)).wrap());
                }),
                PopupKind::Rule(ed) => {
                    let title = if ed.path.is_some() { "Edit rule" } else { "New rule" };
                    let shell_id = p.shell.id;
                    let ctl = self.ctl.clone();
                    popup::show(ctx, &p.shell, vis, 500.0, title, "Save", Kind::Primary, |ui| rule_editor(ui, shell_id, ed, &ctl))
                }
            };
            if is_top {
                events.push((i, ev));
            }
        }
        for (i, ev) in events {
            match ev {
                Event::Ok => self.finish_popup(ctx, i, true),
                Event::Cancel => self.popups[i].shell.close(ctx),
                Event::None => {}
            }
        }
        for i in finished.into_iter().rev() {
            self.popups.remove(i);
        }
    }

    fn toast(&mut self, ctx: &egui::Context) {
        let Some(toast) = &mut self.toast else { return };
        let now = ctx.input(|i| i.time);
        if toast.at.is_nan() {
            toast.at = now;
        }
        let age = (now - toast.at) as f32;
        let life = 3.4;
        if age > life {
            self.toast = None;
            return;
        }
        let vis = anim::ease_out_cubic((age / 0.25).min(1.0)) * (1.0 - anim::ease_in_out_cubic(((age - (life - 0.3)) / 0.3).clamp(0.0, 1.0)));
        ctx.request_repaint();
        let screen = ctx.content_rect();
        let text = toast.text.clone();
        let error = toast.error;
        egui::Area::new(Id::unique("toast")).order(egui::Order::Tooltip).anchor(Align2::CENTER_BOTTOM, vec2(0.0, -86.0 + (1.0 - vis) * 20.0)).interactable(false).show(ctx, |ui| {
            ui.multiply_opacity(vis);
            egui::Frame::new()
                .fill(if error { mix(t::SURFACE, t::DANGER, 0.16) } else { t::CARD_HOVER })
                .stroke(Stroke::new(1.0, if error { alpha(t::DANGER, 0.5) } else { t::LINE }))
                .corner_radius(CornerRadius::same(18))
                .shadow(egui::Shadow { offset: [0, 8], blur: 24, spread: 0, color: alpha(Color32::BLACK, 0.4) })
                .inner_margin(egui::Margin::symmetric(18, 11))
                .show(ui, |ui| {
                    ui.set_max_width(screen.width() * 0.6);
                    widgets::label(ui, &text, t::regular(13.5), if error { t::DANGER } else { t::TEXT });
                });
        });
    }
}

fn collect_matches(entries: &[Value], prefix: &mut Path_, query: &str, out: &mut Vec<(Path_, Map<String, Value>)>) {
    for (i, e) in entries.iter().enumerate() {
        prefix.push(i);
        if is_group(e) {
            if let Some(children) = e.get("children").and_then(Value::as_array) {
                collect_matches(children, prefix, query, out);
            }
        } else if let Some(r) = e.as_object() {
            let name = r.get("name").and_then(Value::as_str).unwrap_or("").to_lowercase();
            if name.contains(query) || targets_text(r).to_lowercase().contains(query) || r.get("replace_ids").map(|v| v.to_string().contains(query)).unwrap_or(false) {
                out.push((prefix.clone(), r.clone()));
            }
        }
        prefix.pop();
    }
}

fn draw_entries(ui: &mut Ui, entries: &[Value], prefix: &mut Path_, depth: usize, acts: &mut Vec<Act>) {
    for (i, e) in entries.iter().enumerate() {
        prefix.push(i);
        if is_group(e) {
            group(ui, e, prefix, depth, acts);
        } else if let Some(r) = e.as_object() {
            rule_card(ui, prefix, r, depth, acts);
        }
        prefix.pop();
    }
}

fn group(ui: &mut Ui, e: &Value, path: &mut Path_, depth: usize, acts: &mut Vec<Act>) {
    let name = e.get("name").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("Group").to_string();
    let children = e.get("children").and_then(Value::as_array).cloned().unwrap_or_default();
    let count = iter_rules(&children).len();
    let id = Id::unique(("group", path.clone()));
    let mut state = egui::collapsing_header::CollapsingState::load_with_default_open(ui.ctx(), id, true);
    let indent = depth as f32 * 18.0;
    let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 40.0), Sense::click());
    let hover = tween_bool(ui.ctx(), id.with("h"), resp.hovered(), 0.14);
    if resp.clicked() {
        state.toggle(ui);
    }
    let openness = state.openness(ui.ctx());
    let r = rect.shrink2(vec2(6.0, 0.0));
    ui.painter().rect_filled(r, CornerRadius::same(12), alpha(t::CARD_HOVER, hover));
    let c = pos2(r.left() + 18.0 + indent, r.center().y);
    let rot = egui::emath::Rot2::from_angle(std::f32::consts::FRAC_PI_2 * openness);
    let pts: Vec<egui::Pos2> = [vec2(-2.5, -5.0), vec2(2.5, 0.0), vec2(-2.5, 5.0)].iter().map(|v| c + rot * *v).collect();
    ui.painter().add(egui::Shape::line(pts, Stroke::new(1.8, t::MUTED)));
    let label = ui.painter().layout_no_wrap(name, t::semi(14.0), t::TEXT);
    let lw = label.size().x;
    ui.painter().galley(pos2(c.x + 16.0, r.center().y - label.size().y / 2.0), label, t::TEXT);
    let badge_c = pos2(c.x + 16.0 + lw + 20.0, r.center().y);
    let bt = ui.painter().layout_no_wrap(count.to_string(), t::semi(11.5), t::MUTED);
    let br = Rect::from_center_size(badge_c, vec2(bt.size().x + 14.0, 20.0));
    ui.painter().rect_filled(br, CornerRadius::same(10), t::CHIP);
    ui.painter().galley(br.center() - bt.size() / 2.0, bt, t::MUTED);
    resp.on_hover_cursor(egui::CursorIcon::PointingHand);
    state.store(ui.ctx());
    let mut state = egui::collapsing_header::CollapsingState::load_with_default_open(ui.ctx(), id, true);
    state.show_body_unindented(ui, |ui| {
        ui.spacing_mut().item_spacing.y = 8.0;
        draw_entries(ui, &children, path, depth + 1, acts);
    });
}

fn rule_card(ui: &mut Ui, path: &Path_, rule: &Map<String, Value>, depth: usize, acts: &mut Vec<Act>) {
    let width = ui.available_width();
    let (outer, resp) = ui.allocate_exact_size(vec2(width, 68.0), Sense::click());
    if !ui.is_rect_visible(outer) {
        return;
    }
    let rect = Rect::from_min_max(pos2(outer.left() + 6.0 + depth as f32 * 18.0, outer.top()), pos2(outer.right() - 6.0, outer.bottom()));
    let id = Id::unique(("rule", path.clone()));
    let hover = tween_bool(ui.ctx(), id.with("h"), ui.rect_contains_pointer(rect), 0.14);
    let enabled = rule.get("enabled").map(rules::truthy).unwrap_or(true);
    let on = tween_bool(ui.ctx(), id.with("on"), enabled, 0.2);
    ui.painter().rect_filled(rect, CornerRadius::same(16), mix(t::SURFACE, t::CARD_HOVER, hover));
    ui.painter().rect_stroke(rect, CornerRadius::same(16), Stroke::new(1.0, mix(alpha(t::LINE, 0.5), t::LINE, hover)), StrokeKind::Inside);
    let mut en = enabled;
    let toggle = Rect::from_center_size(pos2(rect.left() + 36.0, rect.center().y), vec2(40.0, 22.0));
    let mut tc = ui.new_child(UiBuilder::new().max_rect(toggle));
    if widgets::toggle(&mut tc, ("rule-toggle", path.clone()), &mut en).changed() {
        acts.push(Act::ToggleRule(path.clone(), en));
    }
    let mode = rule_mode(rule);
    let chip_text = mode_label(mode);
    let chip_galley = ui.painter().layout_no_wrap(chip_text.to_string(), t::semi(11.5), mode_color(mode));
    let icons_w = 80.0 * hover;
    let chip_rect = Rect::from_center_size(pos2(rect.right() - 18.0 - icons_w - (chip_galley.size().x + 20.0) / 2.0, rect.center().y), vec2(chip_galley.size().x + 20.0, 24.0));
    ui.painter().rect_filled(chip_rect, CornerRadius::same(12), alpha(mode_color(mode), 0.13 + 0.05 * on));
    ui.painter().galley(chip_rect.center() - chip_galley.size() / 2.0, chip_galley, mode_color(mode).gamma_multiply(0.55 + 0.45 * on));
    let detail = rules::describe(rule);
    let mut right_edge = chip_rect.left() - 12.0;
    if !detail.is_empty() {
        let d = widgets::elide(ui, &detail, &t::mono(12.0), 170.0);
        let g = ui.painter().layout_no_wrap(d, t::mono(12.0), t::MUTED);
        let w = g.size().x;
        ui.painter().galley(pos2(right_edge - w, rect.center().y - g.size().y / 2.0), g, t::MUTED.gamma_multiply(0.6 + 0.4 * on));
        right_edge -= w + 12.0;
    }
    let tx = rect.left() + 72.0;
    let name = rule.get("name").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("Rule");
    let name = widgets::elide(ui, name, &t::semi(14.5), (right_edge - tx).max(40.0));
    ui.painter().text(pos2(tx, rect.top() + 24.0), Align2::LEFT_CENTER, name, t::semi(14.5), mix(t::MUTED, t::TEXT, on));
    let targets = widgets::elide(ui, &targets_text(rule), &t::regular(12.5), (right_edge - tx).max(40.0));
    ui.painter().text(pos2(tx, rect.top() + 45.0), Align2::LEFT_CENTER, targets, t::regular(12.5), t::FAINT);
    if hover > 0.02 {
        let ir = Rect::from_min_max(pos2(rect.right() - 12.0 - icons_w, rect.top()), pos2(rect.right() - 10.0, rect.bottom()));
        let mut ic = ui.new_child(UiBuilder::new().max_rect(ir).layout(egui::Layout::right_to_left(egui::Align::Center)));
        ic.multiply_opacity(hover);
        if widgets::icon_button(&mut ic, ("rule-del", path.clone()), Icon::Trash, 34.0, t::DANGER, "Delete").clicked() {
            acts.push(Act::DeleteRule(path.clone()));
        }
        if widgets::icon_button(&mut ic, ("rule-edit", path.clone()), Icon::Pencil, 34.0, t::TEXT, "Edit").clicked() {
            acts.push(Act::EditRule(path.clone()));
        }
    }
    if resp.double_clicked() {
        acts.push(Act::EditRule(path.clone()));
    }
}

fn empty_state(ui: &mut Ui, title: &str, button: Option<&str>, acts: &mut Vec<Act>) {
    ui.add_space(70.0);
    ui.vertical_centered(|ui| {
        let (r, _) = ui.allocate_exact_size(vec2(64.0, 64.0), Sense::hover());
        ui.painter().circle_filled(r.center(), 32.0, t::SURFACE);
        icons::draw(ui.painter(), Icon::Box, r.center(), 28.0, t::FAINT);
        ui.add_space(6.0);
        widgets::label(ui, title, t::semi(15.0), t::MUTED);
        if let Some(b) = button {
            ui.add_space(8.0);
            if widgets::button(ui, "empty-add", b, Some(Icon::Plus), Kind::Primary, 0.0, 40.0, true).clicked() {
                acts.push(Act::AddRule);
            }
        }
    });
}

fn rule_editor(ui: &mut Ui, shell_id: u64, ed: &mut RuleEditor, ctl: &Arc<Controller>) {
    let w = 500.0;
    widgets::label(ui, "Name", t::semi(12.5), t::MUTED);
    widgets::text_field(ui, ("rule-name", shell_id), &mut ed.name, "Optional", w);
    ui.add_space(4.0);
    widgets::label(ui, "Action", t::semi(12.5), t::MUTED);
    if widgets::segmented(ui, ("rule-mode", shell_id), &["Replace", "Remove", "Local file"], &mut ed.mode, w) {
        ed.error.clear();
    }
    ui.add_space(4.0);
    widgets::label(ui, "Assets", t::semi(12.5), t::MUTED);
    let mut remove = None;
    let count = ed.assets.len();
    for (index, (aid, value)) in ed.assets.iter_mut().enumerate() {
        let id = Id::unique(("asset-row", shell_id, *aid));
        let grow = if *aid == 0 { 1.0 } else { tween_from(ui.ctx(), id.with("g"), 0.0, 1.0, 0.24, anim::ease_out_cubic) };
        let (row, _) = ui.allocate_exact_size(vec2(w, 40.0 * grow), Sense::hover());
        let mut rc = ui.new_child(UiBuilder::new().max_rect(Rect::from_min_size(row.min, vec2(w, 40.0))).layout(egui::Layout::left_to_right(egui::Align::Center)));
        rc.set_clip_rect(row.expand(2.0));
        rc.multiply_opacity(grow);
        let label = format!("Asset {}", index + 1);
        let lr = rc.allocate_exact_size(vec2(64.0, 40.0), Sense::hover()).0;
        rc.painter().text(pos2(lr.left(), lr.center().y), Align2::LEFT_CENTER, label, t::semi(12.5), t::FAINT);
        let field_w = if count > 1 { w - 64.0 - 46.0 } else { w - 64.0 };
        widgets::text_field(&mut rc, ("asset", shell_id, *aid), value, "Asset ID or type, like Image", field_w);
        if count > 1 {
            rc.add_space(6.0);
            if widgets::icon_button(&mut rc, ("asset-x", shell_id, *aid), Icon::Close, 34.0, t::DANGER, "").clicked() {
                remove = Some(index);
            }
        }
    }
    if let Some(i) = remove {
        ed.assets.remove(i);
    }
    let add_r = ui.allocate_exact_size(vec2(w, 36.0), Sense::hover()).0;
    let mut ac = ui.new_child(UiBuilder::new().max_rect(add_r).layout(egui::Layout::left_to_right(egui::Align::Center)));
    ac.add_space(64.0);
    if widgets::button(&mut ac, ("add-asset", shell_id), "Add asset", Some(Icon::Plus), Kind::Ghost, 0.0, 34.0, true).clicked() {
        let next = ed.assets.iter().map(|a| a.0).max().unwrap_or(0) + 1;
        ed.assets.push((next, String::new()));
    }
    // the value field appears for Replace and Local file
    let show = tween_bool(ui.ctx(), Id::unique(("value-show", shell_id)), ed.mode != 1, 0.24);
    if show > 0.01 {
        let (r, _) = ui.allocate_exact_size(vec2(w, 72.0 * show), Sense::hover());
        let mut vc = ui.new_child(UiBuilder::new().max_rect(Rect::from_min_size(r.min, vec2(w, 72.0))).layout(egui::Layout::top_down(egui::Align::Min)));
        vc.set_clip_rect(r.expand(2.0));
        vc.multiply_opacity(show);
        vc.spacing_mut().item_spacing.y = 8.0;
        let local = ed.mode == 2;
        widgets::label(&mut vc, if local { "File" } else { "Replacement asset ID" }, t::semi(12.5), t::MUTED);
        vc.horizontal(|ui| {
            if local {
                widgets::text_field(ui, ("rule-value", shell_id), &mut ed.value, "Path to a file", w - 110.0);
                if widgets::button(ui, ("browse", shell_id), "Browse", Some(Icon::File), Kind::Ghost, 102.0, 40.0, true).clicked() {
                    let mut dlg = rfd::FileDialog::new().set_title("Choose file");
                    if let Ok(dir) = std::fs::canonicalize(&ctl.config.folder) {
                        dlg = dlg.set_directory(dir);
                    }
                    if let Some(p) = dlg.pick_file() {
                        ed.value = p.to_string_lossy().into_owned();
                    }
                }
            } else {
                widgets::text_field(ui, ("rule-value", shell_id), &mut ed.value, "Asset ID", w);
            }
        });
    }
    if !ed.error.is_empty() {
        widgets::label(ui, &ed.error, t::regular(12.5), t::DANGER);
    }
}

impl eframe::App for MainApp {
    fn ui(&mut self, ui: &mut Ui, _frame: &mut eframe::Frame) {
        let ctx = ui.ctx().clone();
        while let Ok(line) = self.log_rx.try_recv() {
            self.log.push(line);
        }
        if self.log.len() > 2000 {
            self.log.drain(..self.log.len() - 2000);
        }
        while let Ok(Job::Done { ok, err }) = self.job_rx.try_recv() {
            self.busy = self.busy.saturating_sub(1);
            if let Some(e) = err {
                self.notify(&e, true);
            } else if let Some(m) = ok {
                self.notify(&m, false);
            }
        }
        let now = ctx.input(|i| i.time);
        if now - self.last_scan > 2.5 {
            self.last_scan = now;
            self.scan_configs();
        }
        let rect = ui.max_rect();
        ui.painter().rect_filled(rect, CornerRadius::ZERO, t::BG);
        let mut acts = Vec::new();
        let stage = tween_from(&ctx, Id::unique("stage"), 0.0, if self.settings_open { 1.0 } else { 0.0 }, 0.36, anim::ease_in_out_cubic);
        let modal = !self.popups.is_empty();
        if stage < 0.999 {
            let mut c = ui.new_child(UiBuilder::new().max_rect(rect.translate(vec2(-stage * rect.width() * 0.25, 0.0))));
            c.set_clip_rect(rect);
            c.multiply_opacity(1.0 - stage);
            if self.settings_open || modal {
                c.disable();
            }
            let r = c.max_rect();
            self.main_shell(&mut c, r, &mut acts);
        }
        if stage > 0.001 {
            let mut c = ui.new_child(UiBuilder::new().max_rect(rect.translate(vec2((1.0 - stage) * rect.width(), 0.0))));
            c.set_clip_rect(rect);
            c.multiply_opacity(stage);
            if !self.settings_open || modal {
                c.disable();
            }
            let r = c.max_rect();
            self.settings_page(&mut c, r, &mut acts);
        }
        for a in acts {
            self.apply(&ctx, a);
        }
        self.popups(&ctx);
        self.toast(&ctx);
        #[cfg(feature = "shots")]
        super::shots::drive(self, &ctx);
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        self.ctl.stop();
    }
}

#[cfg(feature = "shots")]
impl MainApp {
    pub fn shot_open_settings(&mut self, tab: usize) {
        self.settings_open = true;
        self.tab = tab;
    }
    pub fn shot_rule_editor(&mut self, ctx: &egui::Context) {
        self.apply(ctx, Act::AddRule);
        if let Some(PopupKind::Rule(ed)) = self.popups.last_mut().map(|p| &mut p.kind) {
            ed.name = "Sky".into();
            ed.assets = vec![(0, "1234567".into()), (1, "Image".into())];
            ed.value = "7654321".into();
        }
    }
    pub fn shot_log(&mut self) {
        self.log_open = true;
    }
    pub fn shot_start(&mut self, ctx: &egui::Context) {
        self.apply(ctx, Act::ToggleProxy);
    }
}
