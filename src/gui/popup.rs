//! Pop-ups over a dimmed backdrop. Opening scales up, fades in and rises; closing reverses
//! faster. Escape or a click on the backdrop closes the top one.

use super::anim;
use super::theme::{self as t, alpha};
use super::widgets::{self, Kind};
use egui::emath::TSTransform;
use egui::{vec2, Align2, Color32, CornerRadius, Id, LayerId, Order, Sense, Shadow, Stroke, Ui};

pub const OPEN_TIME: f32 = 0.30;
pub const CLOSE_TIME: f32 = 0.18;
pub const DIM: f32 = 0.55;

pub struct Shell {
    pub id: u64,
    pub opened: f64,
    pub closing: Option<f64>,
}

impl Shell {
    pub fn new(ctx: &egui::Context, id: u64) -> Shell {
        Shell { id, opened: ctx.input(|i| i.time), closing: None }
    }
    pub fn close(&mut self, ctx: &egui::Context) {
        if self.closing.is_none() {
            self.closing = Some(ctx.input(|i| i.time));
        }
    }
    pub fn is_closing(&self) -> bool {
        self.closing.is_some()
    }
    /// 0..1 visibility; None once the close animation has finished.
    pub fn visibility(&self, ctx: &egui::Context) -> Option<f32> {
        let open = anim::progress(ctx, self.opened, OPEN_TIME, anim::ease_out_cubic);
        match self.closing {
            None => Some(open),
            Some(c) => {
                let p = anim::progress(ctx, c, CLOSE_TIME, anim::ease_in_out_cubic);
                if p >= 1.0 { None } else { Some(open * (1.0 - p)) }
            }
        }
    }
}

pub enum Event {
    None,
    Cancel,
    Ok,
}

/// Draw one pop-up. `body` draws the content; the footer has Cancel and `ok` buttons.
pub fn show(
    ctx: &egui::Context,
    shell: &Shell,
    vis: f32,
    width: f32,
    title: &str,
    ok: &str,
    ok_kind: Kind,
    body: impl FnOnce(&mut Ui),
) -> Event {
    let mut event = Event::None;
    let screen = ctx.content_rect();
    let back_id = Id::unique(("popup-backdrop", shell.id));
    egui::Area::new(back_id).order(Order::Middle).fixed_pos(screen.min).interactable(true).show(ctx, |ui| {
        let (rect, resp) = ui.allocate_exact_size(screen.size(), Sense::click());
        ui.painter().rect_filled(rect, CornerRadius::ZERO, alpha(Color32::BLACK, DIM * vis));
        if resp.clicked() && !shell.is_closing() {
            event = Event::Cancel;
        }
    });
    let area_id = Id::unique(("popup", shell.id));
    let scale = 0.94 + 0.06 * vis;
    let rise = (1.0 - vis) * 18.0;
    let centre = screen.center();
    let transform = TSTransform::new(vec2(centre.x * (1.0 - scale), centre.y * (1.0 - scale) + rise), scale);
    ctx.set_transform_layer(LayerId::new(Order::Foreground, area_id), transform);
    egui::Area::new(area_id).order(Order::Foreground).anchor(Align2::CENTER_CENTER, vec2(0.0, 0.0)).show(ctx, |ui| {
        ui.multiply_opacity(vis);
        if shell.is_closing() {
            ui.disable();
        }
        egui::Frame::new()
            .fill(t::SURFACE)
            .corner_radius(CornerRadius::same(24))
            .stroke(Stroke::new(1.0, t::LINE))
            .shadow(Shadow { offset: [0, 16], blur: 48, spread: 0, color: alpha(Color32::BLACK, 0.55) })
            .inner_margin(egui::Margin::same(24))
            .show(ui, |ui| {
                ui.set_width(width);
                ui.spacing_mut().item_spacing = vec2(8.0, 8.0);
                widgets::label(ui, title, t::semi(19.0), t::TEXT);
                ui.add_space(10.0);
                body(ui);
                ui.add_space(14.0);
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if widgets::button(ui, ("ok", shell.id), ok, None, ok_kind, 0.0, 40.0, true).clicked() {
                        event = Event::Ok;
                    }
                    if widgets::button(ui, ("cancel", shell.id), "Cancel", None, Kind::Ghost, 0.0, 40.0, true).clicked() {
                        event = Event::Cancel;
                    }
                });
            });
    });
    if shell.is_closing() {
        return Event::None;
    }
    if let Event::None = event {
        if ctx.input(|i| i.key_pressed(egui::Key::Escape)) {
            event = Event::Cancel;
        }
    }
    event
}
