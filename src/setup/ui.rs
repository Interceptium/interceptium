//! The setup window.

use super::core::{default_directory, Installer, Manifest, Options};
use super::{Action, SetupArgs};
use crate::gui::anim::{self, tween_bool};
use crate::gui::icons::{self, Icon};
use crate::gui::theme::{self as t, alpha, mix};
use crate::gui::widgets::{self, Kind};
use egui::{pos2, vec2, Align2, Color32, CornerRadius, Id, Rect, Sense, Stroke, StrokeKind, Ui, UiBuilder, Vec2};
use std::path::PathBuf;
use std::sync::{mpsc, Arc, Mutex};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Work {
    Install,
    Repair,
    Reinstall,
    Uninstall,
}

impl Work {
    fn doing(self) -> &'static str {
        match self {
            Work::Install => "Installing Interceptium",
            Work::Repair => "Repairing Interceptium",
            Work::Reinstall => "Reinstalling Interceptium",
            Work::Uninstall => "Uninstalling Interceptium",
        }
    }
    fn done(self) -> &'static str {
        match self {
            Work::Install => "Interceptium is installed",
            Work::Repair => "Interceptium is repaired",
            Work::Reinstall => "Interceptium is reinstalled",
            Work::Uninstall => "Interceptium is uninstalled",
        }
    }
}

#[derive(Clone, PartialEq)]
enum View {
    Install,
    Maintenance,
    Progress(Work),
    Done(Work, PathBuf),
    Error(String),
    Uninstall,
}

pub struct SetupApp {
    installer: Arc<Installer>,
    logo: egui::TextureHandle,
    view: View,
    changed_at: f64,
    dir_text: String,
    desktop: bool,
    start_menu: bool,
    remove_data: bool,
    form_error: String,
    installed: Option<Manifest>,
    progress: Arc<Mutex<(f32, String)>>,
    appearance: String,
    result: Option<mpsc::Receiver<Result<PathBuf, String>>>,
    #[cfg(feature = "shots")]
    shots: crate::gui::shots::Shots,
}

impl SetupApp {
    fn new(cc: &eframe::CreationContext<'_>, args: SetupArgs) -> SetupApp {
        crate::gui::setup_context(&cc.egui_ctx);
        let installer = Arc::new(super::make_installer());
        installer.clean_moved_copies();
        let installed = installer.installed();
        let dir = args
            .dir
            .clone()
            .or_else(|| installed.as_ref().map(|m| PathBuf::from(&m.directory)))
            .unwrap_or_else(default_directory);
        let (desktop, start_menu) = installed.as_ref().map(|m| (m.desktop_shortcut, m.start_menu_shortcut)).unwrap_or((!args.no_desktop, !args.no_start_menu));
        let mut app = SetupApp {
            installer,
            logo: crate::gui::logo_texture(&cc.egui_ctx),
            view: View::Install,
            changed_at: 0.0,
            dir_text: dir.to_string_lossy().into_owned(),
            desktop,
            start_menu,
            remove_data: args.remove_data,
            form_error: String::new(),
            installed,
            progress: Arc::new(Mutex::new((0.0, "Starting".into()))),
            appearance: crate::config::saved_theme(&crate::paths::data_dir_path()),
            result: None,
            #[cfg(feature = "shots")]
            shots: crate::gui::shots::Shots::from_env(),
        };
        let ctx = cc.egui_ctx.clone();
        match args.action {
            Some(Action::Repair) if app.installed.is_some() => app.start(&ctx, Work::Repair),
            Some(Action::Reinstall) if app.installed.is_some() => app.start(&ctx, Work::Reinstall),
            Some(Action::Uninstall) if app.installed.is_some() => app.show(&ctx, View::Uninstall),
            Some(Action::Repair | Action::Reinstall | Action::Uninstall) => app.show(&ctx, View::Error("Interceptium is not installed.".into())),
            Some(Action::Install) => app.show(&ctx, View::Install),
            _ => {
                let v = if app.installed.is_some() { View::Maintenance } else { View::Install };
                app.show(&ctx, v)
            }
        }
        app
    }

    fn show(&mut self, ctx: &egui::Context, view: View) {
        self.view = view;
        self.changed_at = ctx.input(|i| i.time);
    }

    fn start(&mut self, ctx: &egui::Context, work: Work) {
        if work == Work::Install {
            let dir = PathBuf::from(self.dir_text.trim());
            if self.dir_text.trim().is_empty() || !dir.is_absolute() {
                self.form_error = "Choose a full folder path.".into();
                return;
            }
        }
        self.form_error.clear();
        *self.progress.lock().unwrap_or_else(|e| e.into_inner()) = (0.0, "Starting".into());
        let (tx, rx) = mpsc::channel();
        self.result = Some(rx);
        let installer = self.installer.clone();
        let progress = self.progress.clone();
        let repaint = ctx.clone();
        let options = Options { directory: PathBuf::from(self.dir_text.trim()), desktop_shortcut: self.desktop, start_menu_shortcut: self.start_menu };
        let remove_data = self.remove_data;
        let installed_dir = self.installed.as_ref().map(|m| PathBuf::from(&m.directory));
        std::thread::spawn(move || {
            let report = |v: f32, text: &str| {
                *progress.lock().unwrap_or_else(|e| e.into_inner()) = (v, text.to_string());
                repaint.request_repaint();
            };
            let r = match work {
                Work::Install => installer.install(&options, &report).map(|m| PathBuf::from(m.directory)),
                Work::Repair => installer.repair(&report).map(|m| PathBuf::from(m.directory)),
                Work::Reinstall => installer.reinstall(&report).map(|m| PathBuf::from(m.directory)),
                Work::Uninstall => match installed_dir {
                    Some(d) => installer.uninstall(&d, remove_data, &report).map(|_| d),
                    None => Err("Interceptium is not installed.".into()),
                },
            };
            let _ = tx.send(r);
            repaint.request_repaint();
        });
        self.show(ctx, View::Progress(work));
    }

    fn title(ui: &mut Ui, title: &str, sub: &str) {
        widgets::label(ui, title, t::semi(25.0), t::TEXT());
        if !sub.is_empty() {
            ui.add_space(2.0);
            ui.add(egui::Label::new(egui::RichText::new(sub).font(t::regular(13.5)).color(t::MUTED())).wrap());
        }
    }

    fn toggle_row(ui: &mut Ui, id: &str, text: &str, value: &mut bool, width: f32) {
        let (rect, resp) = ui.allocate_exact_size(vec2(width, 48.0), Sense::click());
        ui.painter().text(pos2(rect.left(), rect.center().y), Align2::LEFT_CENTER, text, t::semi(14.0), t::TEXT());
        let tr = Rect::from_center_size(pos2(rect.right() - 20.0, rect.center().y), vec2(40.0, 22.0));
        let mut c = ui.new_child(UiBuilder::new().max_rect(tr));
        let flipped = widgets::toggle(&mut c, id, value).changed();
        if resp.clicked() && !flipped {
            *value = !*value;
        }
    }

    fn view_install(&mut self, ui: &mut Ui, ctx: &egui::Context) {
        let w = ui.available_width();
        Self::title(ui, "Install Interceptium", &format!("Version {}", crate::VERSION));
        ui.add_space(22.0);
        widgets::label(ui, "Location", t::semi(12.5), t::MUTED());
        ui.horizontal(|ui| {
            widgets::text_field(ui, "dir", &mut self.dir_text, "Folder", w - 118.0);
            if widgets::button(ui, "browse", "Browse", Some(Icon::Folder), Kind::Ghost, 110.0, 40.0, true).clicked() {
                if let Some(p) = rfd::FileDialog::new().set_title("Install location").pick_folder() {
                    self.dir_text = p.join("Interceptium").to_string_lossy().into_owned();
                }
            }
        });
        ui.add_space(14.0);
        widgets::card().inner_margin(egui::Margin::symmetric(18, 4)).show(ui, |ui| {
            ui.set_width(w - 36.0);
            ui.spacing_mut().item_spacing.y = 0.0;
            Self::toggle_row(ui, "desk", "Desktop shortcut", &mut self.desktop, w - 36.0);
            let (r, _) = ui.allocate_exact_size(vec2(w - 36.0, 1.0), Sense::hover());
            ui.painter().rect_filled(r, CornerRadius::ZERO, alpha(t::LINE(), 0.7));
            Self::toggle_row(ui, "start", "Start menu shortcut", &mut self.start_menu, w - 36.0);
        });
        if !self.form_error.is_empty() {
            ui.add_space(6.0);
            widgets::label(ui, &self.form_error, t::regular(13.0), t::DANGER());
        }
        footer(ui, |ui| {
            if widgets::button(ui, "install", "Install", Some(Icon::Check), Kind::Primary, 150.0, 44.0, true).clicked() {
                self.start(ctx, Work::Install);
            }
            if self.installed.is_some() {
                if widgets::button(ui, "back", "Back", None, Kind::Ghost, 0.0, 44.0, true).clicked() {
                    self.show(ctx, View::Maintenance);
                }
            } else if widgets::button(ui, "cancel", "Cancel", None, Kind::Ghost, 0.0, 44.0, true).clicked() {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        });
    }

    fn option_card(ui: &mut Ui, id: &str, icon: Icon, title: &str, detail: &str, color: Color32, width: f32) -> bool {
        let (rect, resp) = ui.allocate_exact_size(vec2(width, 74.0), Sense::click());
        let id = Id::unique(id);
        let hover = tween_bool(ui.ctx(), id.with("h"), resp.hovered(), 0.15);
        let press = tween_bool(ui.ctx(), id.with("p"), resp.is_pointer_button_down_on(), 0.08);
        let r = rect.shrink(press * 1.5).translate(vec2(0.0, -1.5 * hover));
        widgets::shadow(ui.painter(), r, 18, 14.0 + 14.0 * hover, 3.0 + 5.0 * hover, t::shade(0.28 + 0.2 * hover));
        ui.painter().rect_filled(r, CornerRadius::same(18), mix(t::CARD(), t::CARD_HOVER(), hover));
        ui.painter().rect_stroke(r, CornerRadius::same(18), Stroke::new(1.0, mix(alpha(t::LINE(), 0.6), alpha(color, 0.5), hover)), StrokeKind::Inside);
        let c = pos2(r.left() + 40.0, r.center().y);
        ui.painter().circle_filled(c, 21.0, alpha(color, 0.14 + 0.08 * hover));
        icons::draw(ui.painter(), icon, c, 20.0, color);
        ui.painter().text(pos2(r.left() + 76.0, r.center().y - 10.0), Align2::LEFT_CENTER, title, t::semi(15.0), t::TEXT());
        ui.painter().text(pos2(r.left() + 76.0, r.center().y + 12.0), Align2::LEFT_CENTER, detail, t::regular(12.5), t::MUTED());
        let ch = pos2(r.right() - 26.0 + hover * 3.0, r.center().y);
        icons::draw(ui.painter(), Icon::Chevron, ch, 16.0, mix(t::FAINT(), t::TEXT(), hover));
        resp.on_hover_cursor(egui::CursorIcon::PointingHand).clicked()
    }

    fn view_maintenance(&mut self, ui: &mut Ui, ctx: &egui::Context) {
        let w = ui.available_width();
        let (version, dir) = self.installed.as_ref().map(|m| (m.version.clone(), m.directory.clone())).unwrap_or_default();
        Self::title(ui, "Interceptium is installed", &format!("Version {version}  ·  {dir}"));
        ui.add_space(20.0);
        ui.spacing_mut().item_spacing.y = 10.0;
        if Self::option_card(ui, "repair", Icon::Wrench, "Repair", "Restore missing or damaged files", t::ACCENT(), w) {
            self.start(ctx, Work::Repair);
        }
        if Self::option_card(ui, "reinstall", Icon::Refresh, "Reinstall", "Remove and install again; configs are kept", t::BLUE(), w) {
            self.start(ctx, Work::Reinstall);
        }
        if Self::option_card(ui, "uninstall", Icon::Trash, "Uninstall", "Remove Interceptium from this PC", t::DANGER(), w) {
            self.show(ctx, View::Uninstall);
        }
        footer(ui, |ui| {
            if widgets::button(ui, "close", "Close", None, Kind::Ghost, 0.0, 44.0, true).clicked() {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        });
    }

    fn view_uninstall(&mut self, ui: &mut Ui, ctx: &egui::Context) {
        let w = ui.available_width();
        Self::title(ui, "Uninstall Interceptium", "Interceptium, its shortcuts and its changes to Roblox will be removed.");
        ui.add_space(22.0);
        widgets::card().inner_margin(egui::Margin::symmetric(18, 4)).show(ui, |ui| {
            ui.set_width(w - 36.0);
            Self::toggle_row(ui, "data", "Also delete configs and settings", &mut self.remove_data, w - 36.0);
        });
        footer(ui, |ui| {
            if widgets::button(ui, "do-uninstall", "Uninstall", Some(Icon::Trash), Kind::Danger, 150.0, 44.0, true).clicked() {
                self.start(ctx, Work::Uninstall);
            }
            if widgets::button(ui, "cancel", "Cancel", None, Kind::Ghost, 0.0, 44.0, true).clicked() {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        });
    }

    fn view_progress(&mut self, ui: &mut Ui, work: Work) {
        let (value, text) = self.progress.lock().unwrap_or_else(|e| e.into_inner()).clone();
        let w = ui.available_width();
        ui.add_space(46.0);
        ui.vertical_centered(|ui| {
            let (r, _) = ui.allocate_exact_size(vec2(84.0, 84.0), Sense::hover());
            ui.painter().circle_filled(r.center(), 40.0, alpha(t::ACCENT(), 0.10));
            ui.painter().circle_stroke(r.center(), 30.0, Stroke::new(3.0, alpha(t::ACCENT(), 0.18)));
            widgets::spinner(ui, r.center(), 30.0, t::ACCENT(), 3.0);
            ui.add_space(14.0);
            widgets::label(ui, work.doing(), t::semi(21.0), t::TEXT());
            ui.add_space(2.0);
            widgets::label(ui, &text, t::regular(13.5), t::MUTED());
            ui.add_space(20.0);
            widgets::progress_bar(ui, "setup-progress", value, (w - 80.0).min(460.0));
            ui.add_space(6.0);
            widgets::label(ui, &format!("{}%", (value * 100.0).round() as u32), t::semi(12.5), t::FAINT());
        });
    }

    fn badge(ui: &mut Ui, at: f64, color: Color32, icon: Icon) {
        let (r, _) = ui.allocate_exact_size(vec2(96.0, 96.0), Sense::hover());
        let pop = anim::progress(ui.ctx(), at, 0.55, anim::ease_out_back);
        let ring = anim::progress(ui.ctx(), at, 0.9, anim::ease_out_cubic);
        let c = r.center();
        ui.painter().circle_stroke(c, 34.0 + ring * 14.0, Stroke::new(2.0, alpha(color, 0.35 * (1.0 - ring))));
        ui.painter().circle_filled(c, 40.0 * pop.max(0.0), alpha(color, 0.16));
        widgets::shadow(ui.painter(), Rect::from_center_size(c, Vec2::splat(56.0 * pop.max(0.0))), 28, 24.0, 6.0, alpha(color, 0.35 * pop.clamp(0.0, 1.0)));
        ui.painter().circle_filled(c, 28.0 * pop.max(0.0), color);
        if pop > 0.5 {
            icons::draw(ui.painter(), icon, c, 30.0 * pop.min(1.0), t::ACCENT_TEXT());
        }
    }

    fn view_done(&mut self, ui: &mut Ui, ctx: &egui::Context, work: Work, dir: PathBuf) {
        ui.add_space(40.0);
        let at = self.changed_at;
        ui.vertical_centered(|ui| {
            Self::badge(ui, at, t::ACCENT(), Icon::Check);
            ui.add_space(12.0);
            widgets::label(ui, work.done(), t::semi(22.0), t::TEXT());
            if work != Work::Uninstall {
                let shown = widgets::elide(ui, &dir.to_string_lossy(), &t::regular(12.5), 520.0);
                widgets::label(ui, &shown, t::regular(12.5), t::FAINT());
            }
        });
        footer(ui, |ui| {
            if work != Work::Uninstall {
                if widgets::button(ui, "open", "Open Interceptium", Some(Icon::Play), Kind::Primary, 0.0, 44.0, true).clicked() {
                    super::launch_app(&dir);
                    ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                }
            }
            if widgets::button(ui, "close", "Close", None, Kind::Ghost, 0.0, 44.0, true).clicked() {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        });
    }

    fn view_error(&mut self, ui: &mut Ui, ctx: &egui::Context, message: &str) {
        ui.add_space(40.0);
        let at = self.changed_at;
        ui.vertical_centered(|ui| {
            Self::badge(ui, at, t::DANGER(), Icon::Close);
            ui.add_space(12.0);
            widgets::label(ui, "Something went wrong", t::semi(22.0), t::TEXT());
            ui.add_space(2.0);
            ui.set_max_width(520.0);
            ui.add(egui::Label::new(egui::RichText::new(message).font(t::regular(13.5)).color(t::MUTED())).wrap());
        });
        footer(ui, |ui| {
            if widgets::button(ui, "close", "Close", None, Kind::Ghost, 0.0, 44.0, true).clicked() {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
            if self.installed.is_some() || self.result.is_some() {
                if widgets::button(ui, "back", "Back", None, Kind::Ghost, 0.0, 44.0, true).clicked() {
                    self.installed = self.installer.installed();
                    let v = if self.installed.is_some() { View::Maintenance } else { View::Install };
                    self.show(ctx, v);
                }
            }
        });
    }
}

fn footer(ui: &mut Ui, add: impl FnOnce(&mut Ui)) {
    let full = ui.max_rect();
    let r = Rect::from_min_max(pos2(full.left(), full.bottom() - 46.0), full.max);
    let mut c = ui.new_child(UiBuilder::new().max_rect(r).layout(egui::Layout::right_to_left(egui::Align::Center)));
    c.spacing_mut().item_spacing.x = 10.0;
    add(&mut c);
}

impl eframe::App for SetupApp {
    fn ui(&mut self, ui: &mut Ui, _frame: &mut eframe::Frame) {
        let ctx = ui.ctx().clone();
        if let Some(rx) = &self.result {
            if let Ok(r) = rx.try_recv() {
                self.result = None;
                let View::Progress(work) = self.view.clone() else { return };
                match r {
                    Ok(dir) => {
                        self.installed = self.installer.installed();
                        self.show(&ctx, View::Done(work, dir));
                    }
                    Err(e) => self.show(&ctx, View::Error(e)),
                }
            }
        }
        let working = matches!(self.view, View::Progress(_));
        if working && ctx.input(|i| i.viewport().close_requested()) {
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
        }
        crate::gui::update_appearance(&ctx, &self.appearance);
        let rect = ui.max_rect();
        ui.painter().rect_filled(rect, CornerRadius::ZERO, t::BG());
        // header
        let head = Rect::from_min_size(rect.min + vec2(28.0, 22.0), vec2(rect.width() - 56.0, 36.0));
        ui.painter().image(self.logo.id(), Rect::from_center_size(pos2(head.left() + 17.0, head.center().y), vec2(34.0, 34.0)), Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
        ui.painter().text(pos2(head.left() + 44.0, head.center().y), Align2::LEFT_CENTER, "Interceptium Setup", t::semi(16.0), t::TEXT());
        ui.painter().text(pos2(head.right(), head.center().y), Align2::RIGHT_CENTER, format!("Version {}", crate::VERSION), t::regular(12.5), t::FAINT());
        let line = Rect::from_min_size(pos2(rect.left(), head.bottom() + 18.0), vec2(rect.width(), 1.0));
        ui.painter().rect_filled(line, CornerRadius::ZERO, alpha(t::LINE(), 0.6));
        // body slides in from the right on every change
        let p = anim::progress(&ctx, self.changed_at, 0.36, anim::ease_out_cubic);
        let body = Rect::from_min_max(pos2(rect.left() + 34.0, line.bottom() + 26.0), pos2(rect.right() - 34.0, rect.bottom() - 26.0));
        let mut c = ui.new_child(UiBuilder::new().max_rect(body.translate(vec2((1.0 - p) * 36.0, 0.0))).layout(egui::Layout::top_down(egui::Align::Min)));
        c.set_clip_rect(rect);
        c.multiply_opacity(p);
        match self.view.clone() {
            View::Install => self.view_install(&mut c, &ctx),
            View::Maintenance => self.view_maintenance(&mut c, &ctx),
            View::Uninstall => self.view_uninstall(&mut c, &ctx),
            View::Progress(w) => self.view_progress(&mut c, w),
            View::Done(w, d) => self.view_done(&mut c, &ctx, w, d),
            View::Error(m) => self.view_error(&mut c, &ctx, &m),
        }
        #[cfg(feature = "shots")]
        {
            let mut shots = std::mem::replace(&mut self.shots, crate::gui::shots::Shots::none());
            shots.tick(&ctx, |scene| {
                if scene.split(',').any(|p| p == "light") {
                    self.appearance = "light".into();
                }
                let scene = scene.split(',').next().unwrap_or("");
                let view = match scene {
                    "maintenance" => {
                        self.installed = Some(super::core::Manifest { version: "1.0.0".into(), directory: self.dir_text.clone(), files: vec![], shortcuts: vec![], desktop_shortcut: true, start_menu_shortcut: true });
                        View::Maintenance
                    }
                    "progress" => {
                        *self.progress.lock().unwrap() = (0.62, "Copying Interceptium".into());
                        View::Progress(Work::Install)
                    }
                    "done" => View::Done(Work::Install, PathBuf::from(self.dir_text.clone())),
                    "error" => View::Error("Can't write to C:\\Program Files\\Interceptium. Choose another folder.".into()),
                    "uninstall" => View::Uninstall,
                    _ => View::Install,
                };
                self.show(&ctx, view);
            });
            self.shots = shots;
        }
    }
}

pub fn run(args: SetupArgs) -> i32 {
    crate::gui::run_window([680.0, 520.0], [680.0, 520.0], false, move |cc| Box::new(SetupApp::new(cc, args.clone())))
}
