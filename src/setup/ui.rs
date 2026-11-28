//! The setup window.

use super::core::{compare_versions, default_directory, Installer, Manifest, Options};
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
    Update,
}

impl Work {
    fn doing(self) -> &'static str {
        match self {
            Work::Install => concat!("Installing ", crate::brand_name!()),
            Work::Repair => concat!("Repairing ", crate::brand_name!()),
            Work::Reinstall => concat!("Reinstalling ", crate::brand_name!()),
            Work::Uninstall => concat!("Uninstalling ", crate::brand_name!()),
            Work::Update => concat!("Updating ", crate::brand_name!()),
        }
    }
    fn done(self) -> &'static str {
        match self {
            Work::Install => concat!(crate::brand_name!(), " is installed"),
            Work::Repair => concat!(crate::brand_name!(), " is repaired"),
            Work::Reinstall => concat!(crate::brand_name!(), " is reinstalled"),
            Work::Uninstall => concat!(crate::brand_name!(), " is uninstalled"),
            Work::Update => concat!(crate::brand_name!(), " is updated"),
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
    Update,
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
    corners_rounded: bool,
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
            corners_rounded: false,
            result: None,
            #[cfg(feature = "shots")]
            shots: crate::gui::shots::Shots::from_env(),
        };
        let ctx = cc.egui_ctx.clone();
        match args.action {
            Some(Action::Repair) if app.installed.is_some() => app.start(&ctx, Work::Repair),
            Some(Action::Reinstall) if app.installed.is_some() => app.start(&ctx, Work::Reinstall),
            Some(Action::Uninstall) if app.installed.is_some() => app.show(&ctx, View::Uninstall),
            Some(Action::Repair | Action::Reinstall | Action::Uninstall) => app.show(&ctx, View::Error(concat!(crate::brand_name!(), " is not installed.").into())),
            Some(Action::Install) => app.show(&ctx, View::Install),
            _ => {
                let v = app.home_view();
                app.show(&ctx, v)
            }
        }
        app
    }

    /// Not installed: Install. An older version installed: Update. Otherwise: Repair / Reinstall / Uninstall.
    fn home_view(&self) -> View {
        match &self.installed {
            None => View::Install,
            Some(m) if compare_versions(&m.version, crate::VERSION) == std::cmp::Ordering::Less => View::Update,
            Some(_) => View::Maintenance,
        }
    }

    fn is_update(&self) -> bool {
        matches!(self.view, View::Update | View::Progress(Work::Update) | View::Done(Work::Update, _))
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
                // An update installs this version over the old one, keeping its folder and shortcuts.
                Work::Repair | Work::Update => installer.repair(&report).map(|m| PathBuf::from(m.directory)),
                Work::Reinstall => installer.reinstall(&report).map(|m| PathBuf::from(m.directory)),
                Work::Uninstall => match installed_dir {
                    Some(d) => installer.uninstall(&d, remove_data, &report).map(|_| d),
                    None => Err(concat!(crate::brand_name!(), " is not installed.").into()),
                },
            };
            let _ = tx.send(r);
            repaint.request_repaint();
        });
        self.show(ctx, View::Progress(work));
    }

    fn title(ui: &mut Ui, title: &str, sub: &str) {
        let (title, sub) = (crate::i18n::t(title), crate::i18n::t(sub));
        let (title, sub) = (title.as_ref(), sub.as_ref());
        widgets::label(ui, title, t::semi(25.0), t::TEXT());
        if !sub.is_empty() {
            ui.add_space(2.0);
            ui.add(egui::Label::new(egui::RichText::new(sub).font(t::regular(13.5)).color(t::MUTED())).wrap());
        }
    }

    fn toggle_row(ui: &mut Ui, id: &str, text: &str, value: &mut bool, width: f32) {
        let text = crate::i18n::t(text);
        let text = text.as_ref();
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
        Self::title(ui, concat!("Install ", crate::brand_name!()), &format!("Version {}", crate::VERSION));
        ui.add_space(22.0);
        widgets::label(ui, "Location", t::semi(12.5), t::MUTED());
        ui.horizontal(|ui| {
            widgets::text_field(ui, "dir", &mut self.dir_text, "Folder", w - 118.0);
            if widgets::button(ui, "browse", "Browse", Some(Icon::Folder), Kind::Ghost, 110.0, 40.0, true).clicked() {
                if let Some(p) = rfd::FileDialog::new().set_title(crate::i18n::t("Install location")).pick_folder() {
                    self.dir_text = p.join(concat!(crate::brand_name!())).to_string_lossy().into_owned();
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
                    let v = self.home_view();
                    self.show(ctx, v);
                }
            } else if widgets::button(ui, "cancel", "Cancel", None, Kind::Ghost, 0.0, 44.0, true).clicked() {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        });
    }

    fn option_card(ui: &mut Ui, id: &str, icon: Icon, title: &str, detail: &str, color: Color32, width: f32) -> bool {
        let (title, detail) = (crate::i18n::t(title), crate::i18n::t(detail));
        let (title, detail) = (title.as_ref(), detail.as_ref());
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
        Self::title(ui, concat!(crate::brand_name!(), " is installed"), &format!("Version {version}  ·  {dir}"));
        if compare_versions(&version, crate::VERSION) == std::cmp::Ordering::Greater {
            ui.add_space(8.0);
            ui.horizontal(|ui| {
                let (r, _) = ui.allocate_exact_size(vec2(16.0, 16.0), Sense::hover());
                icons::draw(ui.painter(), Icon::Warning, r.center(), 15.0, t::WARN());
                widgets::label(ui, &format!("A newer version is installed. Repair or Reinstall puts back version {}.", crate::VERSION), t::regular(12.5), t::WARN());
            });
        }
        ui.add_space(20.0);
        ui.spacing_mut().item_spacing.y = 10.0;
        if Self::option_card(ui, "repair", Icon::Wrench, "Repair", "Restore missing or damaged files", t::ACCENT(), w) {
            self.start(ctx, Work::Repair);
        }
        if Self::option_card(ui, "reinstall", Icon::Refresh, "Reinstall", "Remove and install again; configs are kept", t::BLUE(), w) {
            self.start(ctx, Work::Reinstall);
        }
        if Self::option_card(ui, "uninstall", Icon::Trash, "Uninstall", concat!("Remove ", crate::brand_name!(), " from this PC"), t::DANGER(), w) {
            self.show(ctx, View::Uninstall);
        }
        footer(ui, |ui| {
            if widgets::button(ui, "close", "Close", None, Kind::Ghost, 0.0, 44.0, true).clicked() {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        });
    }

    fn view_update(&mut self, ui: &mut Ui, ctx: &egui::Context) {
        let w = ui.available_width();
        let (old, dir) = self.installed.as_ref().map(|m| (m.version.clone(), m.directory.clone())).unwrap_or_default();
        Self::title(ui, concat!("Update ", crate::brand_name!()), &format!("Version {old}  \u{2192}  {}", crate::VERSION));
        ui.add_space(22.0);
        widgets::card().inner_margin(egui::Margin::symmetric(18, 6)).show(ui, |ui| {
            ui.set_width(w - 36.0);
            ui.spacing_mut().item_spacing.y = 0.0;
            for (i, (icon, title, detail)) in [
                (Icon::Folder, "Location", dir.as_str()),
                (Icon::Shield, "Configs and settings", "Kept as they are"),
            ]
            .into_iter()
            .enumerate()
            {
                if i > 0 {
                    let (r, _) = ui.allocate_exact_size(vec2(w - 36.0, 1.0), Sense::hover());
                    ui.painter().rect_filled(r, CornerRadius::ZERO, alpha(t::LINE(), 0.7));
                }
                let (r, _) = ui.allocate_exact_size(vec2(w - 36.0, 54.0), Sense::hover());
                let c = pos2(r.left() + 16.0, r.center().y);
                ui.painter().circle_filled(c, 15.0, alpha(t::ACCENT(), 0.12));
                icons::draw(ui.painter(), icon, c, 15.0, t::ACCENT());
                ui.painter().text(pos2(r.left() + 44.0, r.center().y - 9.0), Align2::LEFT_CENTER, crate::i18n::t(title), t::semi(14.0), t::TEXT());
                let d = widgets::elide(ui, &crate::i18n::t(detail), &t::regular(12.5), r.width() - 60.0);
                ui.painter().text(pos2(r.left() + 44.0, r.center().y + 10.0), Align2::LEFT_CENTER, d, t::regular(12.5), t::MUTED());
            }
        });
        footer(ui, |ui| {
            if widgets::button(ui, "update", "Update", Some(Icon::Refresh), Kind::Primary, 150.0, 44.0, true).clicked() {
                self.start(ctx, Work::Update);
            }
            if widgets::button(ui, "more", "More options", None, Kind::Ghost, 0.0, 44.0, true).clicked() {
                self.show(ctx, View::Maintenance);
            }
        });
    }

    fn view_uninstall(&mut self, ui: &mut Ui, ctx: &egui::Context) {
        let w = ui.available_width();
        Self::title(ui, concat!("Uninstall ", crate::brand_name!()), concat!(crate::brand_name!(), ", its shortcuts and its changes to Roblox will be removed."));
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
                if widgets::button(ui, "open", concat!("Open ", crate::brand_name!()), Some(Icon::Play), Kind::Primary, 0.0, 44.0, true).clicked() {
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
                    let v = self.home_view();
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
    fn ui(&mut self, ui: &mut Ui, frame: &mut eframe::Frame) {
        let ctx = ui.ctx().clone();
        if !self.corners_rounded {
            crate::gui::chrome::round_native_corners(frame);
            self.corners_rounded = true;
        }
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
        ui.painter().rect_filled(rect, CornerRadius::same(crate::gui::chrome::window_radius(&ctx)), t::BG());
        if crate::brand::HIDO {
            crate::gui::art::ocean(&ui.painter().with_clip_rect(rect), rect, ctx.input(|i| i.time) as f32);
            ctx.request_repaint();
        }
        crate::gui::chrome::drag_area(ui, Rect::from_min_size(rect.min, vec2(rect.width(), 76.0)), false);
        // header
        let head = Rect::from_min_size(rect.min + vec2(28.0, 22.0), vec2(rect.width() - 56.0, 36.0));
        ui.painter().image(self.logo.id(), Rect::from_center_size(pos2(head.left() + 17.0, head.center().y), vec2(34.0, 34.0)), Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
        ui.painter().text(pos2(head.left() + 44.0, head.center().y), Align2::LEFT_CENTER, crate::i18n::t(if self.is_update() { concat!(crate::brand_name!(), " Update") } else { concat!(crate::brand_name!(), " Setup") }), t::semi(16.0), t::TEXT());
        ui.painter().text(pos2(head.right() - 84.0, head.center().y), Align2::RIGHT_CENTER, crate::i18n::t(&format!("Version {}", crate::VERSION)), t::regular(12.5), t::FAINT());
        let buttons = Rect::from_min_max(pos2(head.right() - 76.0, head.top()), pos2(head.right() + 6.0, head.bottom()));
        let mut wc = ui.new_child(UiBuilder::new().max_rect(buttons).layout(egui::Layout::right_to_left(egui::Align::Center)));
        crate::gui::chrome::window_buttons(&mut wc, false);
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
            View::Update => self.view_update(&mut c, &ctx),
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
                        *self.progress.lock().unwrap() = (0.62, concat!("Copying ", crate::brand_name!()).into());
                        View::Progress(Work::Install)
                    }
                    "done" => View::Done(Work::Install, PathBuf::from(self.dir_text.clone())),
                    "update" => {
                        self.installed = Some(super::core::Manifest { version: "1.0.0".into(), directory: self.dir_text.clone(), files: vec![], shortcuts: vec![], desktop_shortcut: true, start_menu_shortcut: true });
                        View::Update
                    }
                    "error" => View::Error(concat!("Can't write to C:\\Program Files\\", crate::brand_name!(), ". Choose another folder.").into()),
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
