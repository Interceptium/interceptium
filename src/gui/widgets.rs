//! Rounded, animated widgets.

use super::anim::{self, tween, tween_bool};
use super::icons::{self, Icon};
use super::theme::{self as t, alpha, mix};
use egui::{pos2, vec2, Align2, Color32, CornerRadius, Id, Rect, Response, Sense, Stroke, StrokeKind, Ui, Vec2};
use std::f32::consts::TAU;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Primary,
    Ghost,
    Danger,
}

fn colors(kind: Kind) -> (Color32, Color32, Color32, Color32) {
    // (fill, hover, press, text)
    match kind {
        Kind::Primary => (t::ACCENT(), t::ACCENT_HOVER(), t::ACCENT_PRESS(), t::ACCENT_TEXT()),
        Kind::Ghost => (t::GHOST(), t::GHOST_HOVER(), t::CHIP(), t::TEXT()),
        Kind::Danger => (alpha(t::DANGER(), 0.14), alpha(t::DANGER(), 0.24), alpha(t::DANGER(), 0.32), t::DANGER()),
    }
}

/// A soft drop shadow under a rounded rectangle.
pub fn shadow(p: &egui::Painter, rect: Rect, radius: u8, blur: f32, y: f32, color: Color32) {
    let s = egui::epaint::Shadow { offset: [0, y.round().clamp(-127.0, 127.0) as i8], blur: blur.round().clamp(0.0, 255.0) as u8, spread: 0, color };
    p.add(s.as_shape(rect, CornerRadius::same(radius)));
}

/// A raised panel: shadow, fill, hairline border and a faint top highlight.
pub fn panel(p: &egui::Painter, rect: Rect, radius: u8, fill: Color32) {
    // Hidoceptium: slightly see-through, so the sea behind shows softly
    let fill = if crate::brand::HIDO { alpha(fill, 0.86) } else { fill };
    shadow(p, rect, radius, 32.0, 10.0, t::shade(0.38));
    p.rect_filled(rect, CornerRadius::same(radius), fill);
    p.rect_stroke(rect, CornerRadius::same(radius), Stroke::new(1.0, alpha(t::LINE(), 0.65)), StrokeKind::Inside);
    let top = Rect::from_min_max(rect.min + vec2(radius as f32, 0.5), egui::pos2(rect.right() - radius as f32, rect.top() + 1.5));
    p.rect_filled(top, CornerRadius::ZERO, alpha(Color32::WHITE, 0.035));
}

/// A pill button. `width` 0 sizes it to its text.
pub fn button(ui: &mut Ui, id: impl std::hash::Hash + std::fmt::Debug, text: &str, icon: Option<Icon>, kind: Kind, width: f32, height: f32, enabled: bool) -> Response {
    let font = t::medium(14.0);
    let fade = if enabled { 1.0 } else { 0.45 };
    let text_color = colors(kind).3.gamma_multiply(fade);
    let text = crate::i18n::t(text);
    let galley = ui.painter().layout_no_wrap(text.to_string(), font, text_color);
    let icon_w = if icon.is_some() { 22.0 } else { 0.0 };
    let w = if width > 0.0 { width } else { galley.size().x + icon_w + 36.0 };
    let (rect, resp) = ui.allocate_exact_size(vec2(w, height), if enabled { Sense::click() } else { Sense::hover() });
    let id = Id::unique(id);
    let hover = tween_bool(ui.ctx(), id.with("h"), enabled && resp.hovered(), 0.14);
    let press = tween_bool(ui.ctx(), id.with("p"), enabled && resp.is_pointer_button_down_on(), 0.08);
    if ui.is_rect_visible(rect) {
        let (fill, hov, prs, fg) = colors(kind);
        let mut bg = mix(fill, hov, hover);
        bg = mix(bg, prs, press);
        let r = rect.shrink(press * 1.2).translate(vec2(0.0, -hover * 1.0 + press * 1.0));
        let glow = if kind == Kind::Primary { alpha(t::ACCENT(), (0.22 + 0.20 * hover - 0.12 * press) * fade) } else { t::shade((0.28 + 0.12 * hover) * fade) };
        shadow(ui.painter(), r, (height / 2.0) as u8, 14.0 + 8.0 * hover, 3.0 + 3.0 * hover - 2.0 * press, glow);
        ui.painter().rect_filled(r, CornerRadius::same((height / 2.0) as u8), bg.gamma_multiply(fade));
        let content_w = galley.size().x + icon_w;
        let mut x = r.center().x - content_w / 2.0;
        let fg = fg.gamma_multiply(fade);
        if let Some(ic) = icon {
            icons::draw(ui.painter(), ic, pos2(x + 8.0, r.center().y), 15.0, fg);
            x += icon_w;
        }
        ui.painter().galley(pos2(x, r.center().y - galley.size().y / 2.0), galley, fg);
    }
    if enabled { resp.on_hover_cursor(egui::CursorIcon::PointingHand) } else { resp }
}

/// A round icon-only button with a soft hover circle.
pub fn icon_button(ui: &mut Ui, id: impl std::hash::Hash + std::fmt::Debug, icon: Icon, size: f32, color: Color32, tip: &str) -> Response {
    let (rect, resp) = ui.allocate_exact_size(Vec2::splat(size), Sense::click());
    let id = Id::unique(id);
    let hover = tween_bool(ui.ctx(), id.with("h"), resp.hovered(), 0.14);
    let press = tween_bool(ui.ctx(), id.with("p"), resp.is_pointer_button_down_on(), 0.08);
    if ui.is_rect_visible(rect) {
        let bg = alpha(t::GHOST_HOVER(), hover * 0.9 + press * 0.1);
        ui.painter().circle_filled(rect.center(), size / 2.0 - press, bg);
        icons::draw(ui.painter(), icon, rect.center(), size * 0.5, mix(t::MUTED(), color, 0.35 + 0.65 * hover));
    }
    let resp = resp.on_hover_cursor(egui::CursorIcon::PointingHand);
    if tip.is_empty() { resp } else { resp.on_hover_text(crate::i18n::t(tip)) }
}

/// An iOS-style switch. Returns a response that is `changed()` when flipped.
pub fn toggle(ui: &mut Ui, id: impl std::hash::Hash + std::fmt::Debug, on: &mut bool) -> Response {
    let size = vec2(40.0, 22.0);
    let (rect, mut resp) = ui.allocate_exact_size(size, Sense::click());
    if resp.clicked() {
        *on = !*on;
        resp.mark_changed();
    }
    let id = Id::unique(id);
    let v = anim::tween_from(ui.ctx(), id.with("t"), if *on { 1.0 } else { 0.0 }, if *on { 1.0 } else { 0.0 }, 0.20, anim::ease_out_cubic);
    let hover = tween_bool(ui.ctx(), id.with("h"), resp.hovered(), 0.12);
    if ui.is_rect_visible(rect) {
        let track = mix(t::TRACK_OFF(), t::ACCENT(), v);
        ui.painter().rect_filled(rect, CornerRadius::same(11), mix(track, Color32::WHITE, hover * 0.06));
        let x = egui::lerp(rect.left() + 11.0..=rect.right() - 11.0, v);
        let knob = pos2(x, rect.center().y);
        ui.painter().circle_filled(knob + vec2(0.0, 1.0), 9.0, t::shade(0.25));
        // White knob on light backgrounds; on dark the off knob is slightly dimmed.
        let off_knob = mix(Color32::from_rgb(0xd5, 0xda, 0xe5), Color32::WHITE, t::light_amount());
        ui.painter().circle_filled(knob, 8.5, mix(off_knob, Color32::WHITE, v));
    }
    resp.on_hover_cursor(egui::CursorIcon::PointingHand)
}

/// A rounded text field; the border glows while it has focus.
pub fn text_field(ui: &mut Ui, id: impl std::hash::Hash + std::fmt::Debug, text: &mut String, hint: &str, width: f32) -> Response {
    let id = Id::unique(id);
    let height = 40.0;
    let (rect, _) = ui.allocate_exact_size(vec2(width, height), Sense::hover());
    let edit_id = id.with("edit");
    let focused = ui.memory(|m| m.has_focus(edit_id));
    let f = tween_bool(ui.ctx(), id.with("f"), focused, 0.16);
    ui.painter().rect_filled(rect, CornerRadius::same(12), t::FIELD());
    shadow(ui.painter(), rect, 12, 16.0 * f, 0.0, alpha(t::ACCENT(), 0.16 * f));
    ui.painter().rect_stroke(rect, CornerRadius::same(12), Stroke::new(1.0 + f * 0.5, mix(t::LINE(), t::ACCENT(), f)), StrokeKind::Inside);
    let inner = rect.shrink2(vec2(14.0, 0.0));
    let mut child = ui.new_child(egui::UiBuilder::new().max_rect(inner).layout(egui::Layout::left_to_right(egui::Align::Center)));
    let resp = child.add(
        egui::TextEdit::singleline(text)
            .id(edit_id)
            .hint_text(egui::RichText::new(crate::i18n::t(hint)).color(t::FAINT()))
            .font(t::regular(14.0))
            .text_color(t::TEXT())
            .frame(egui::Frame::NONE)
            .desired_width(inner.width()),
    );
    resp
}

/// Segmented control with a sliding highlight. Returns true when the selection changed.
pub fn segmented(ui: &mut Ui, id: impl std::hash::Hash + std::fmt::Debug, labels: &[&str], selected: &mut usize, width: f32) -> bool {
    let id = Id::unique(id);
    let height = 38.0;
    let (rect, _) = ui.allocate_exact_size(vec2(width, height), Sense::hover());
    ui.painter().rect_filled(rect, CornerRadius::same(19), t::FIELD());
    let n = labels.len().max(1) as f32;
    let seg = (rect.width() - 8.0) / n;
    let pos = tween(ui.ctx(), id.with("pos"), *selected as f32, 0.26);
    let hl = Rect::from_min_size(pos2(rect.left() + 4.0 + pos * seg, rect.top() + 4.0), vec2(seg, height - 8.0));
    shadow(ui.painter(), hl, 15, 10.0, 2.0, t::shade(0.4));
    ui.painter().rect_filled(hl, CornerRadius::same(15), t::CARD_HOVER());
    ui.painter().rect_stroke(hl, CornerRadius::same(15), Stroke::new(1.0, alpha(Color32::WHITE, 0.05)), StrokeKind::Inside);
    let mut changed = false;
    for (i, label) in labels.iter().enumerate() {
        let r = Rect::from_min_size(pos2(rect.left() + 4.0 + i as f32 * seg, rect.top() + 4.0), vec2(seg, height - 8.0));
        let resp = ui.interact(r, id.with(i), Sense::click()).on_hover_cursor(egui::CursorIcon::PointingHand);
        if resp.clicked() && *selected != i {
            *selected = i;
            changed = true;
        }
        let near = (1.0 - (pos - i as f32).abs()).clamp(0.0, 1.0);
        let hover = tween_bool(ui.ctx(), id.with(("h", i)), resp.hovered(), 0.12);
        let color = mix(mix(t::MUTED(), t::TEXT(), hover * 0.6), t::TEXT(), near);
        ui.painter().text(r.center(), Align2::CENTER_CENTER, crate::i18n::t(label), t::medium(13.5), color);
    }
    changed
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Running,
    Stopped,
    Busy,
}

pub fn status_pill(ui: &mut Ui, status: Status, text: &str) {
    let font = t::semi(12.5);
    let text = crate::i18n::t(text);
    let text = text.as_ref();
    let width = ui.painter().layout_no_wrap(text.to_string(), font.clone(), t::TEXT()).size().x;
    let size = vec2(width + 40.0, 30.0);
    let (rect, _) = ui.allocate_exact_size(size, Sense::hover());
    let id = Id::unique("status-pill");
    let color = match status {
        Status::Running => t::ACCENT(),
        Status::Stopped => t::FAINT(),
        Status::Busy => t::WARN(),
    };
    let c = tween(ui.ctx(), id.with("c"), match status { Status::Running => 1.0, Status::Stopped => 0.0, Status::Busy => 0.5 }, 0.3);
    let base = if c >= 0.5 { mix(t::WARN(), t::ACCENT(), (c - 0.5) * 2.0) } else { mix(t::FAINT(), t::WARN(), c * 2.0) };
    let _ = color;
    ui.painter().rect_filled(rect, CornerRadius::same(15), alpha(base, 0.12));
    let dot = pos2(rect.left() + 16.0, rect.center().y);
    if status != Status::Stopped {
        let time = ui.input(|i| i.time) as f32;
        let phase = (time * 0.9).fract();
        ui.painter().circle_filled(dot, 4.0 + phase * 7.0, alpha(base, 0.45 * (1.0 - phase)));
        ui.ctx().request_repaint();
    }
    ui.painter().circle_filled(dot, 4.0, base);
    ui.painter().text(pos2(rect.left() + 28.0, rect.center().y), Align2::LEFT_CENTER, text, font, mix(t::MUTED(), t::TEXT(), c.min(1.0)));
}

pub fn spinner(ui: &Ui, center: egui::Pos2, radius: f32, color: Color32, width: f32) {
    let time = ui.input(|i| i.time) as f32;
    let start = time * 4.2;
    let sweep = 1.2 + 0.9 * (time * 2.1).sin();
    icons::arc(ui.painter(), center, radius, start, start + sweep + 1.0, Stroke::new(width, color));
    ui.ctx().request_repaint();
}

/// The big round proxy switch. Rings orbit while it runs; a spinner shows while it changes.
pub fn power_button(ui: &mut Ui, running: bool, busy: bool) -> Response {
    let size = 148.0;
    let (rect, resp) = ui.allocate_exact_size(Vec2::splat(size), if busy { Sense::hover() } else { Sense::click() });
    let id = Id::unique("power-button");
    let on = tween_bool(ui.ctx(), id.with("on"), running, 0.45);
    let hover = tween_bool(ui.ctx(), id.with("h"), resp.hovered() && !busy, 0.15);
    let press = tween_bool(ui.ctx(), id.with("p"), resp.is_pointer_button_down_on(), 0.08);
    let c = rect.center();
    let p = ui.painter();
    let time = ui.input(|i| i.time) as f32;
    let r = 52.0 - press * 2.0 + hover * 1.5;
    if on > 0.01 {
        // soft breathing glow
        let breathe = 0.5 + 0.5 * (time * 1.6).sin();
        for i in 0..6 {
            let k = i as f32 / 6.0;
            p.circle_filled(c, r + 6.0 + k * 16.0 + breathe * 3.0, alpha(t::ACCENT(), on * 0.05 * (1.0 - k)));
        }
        ui.ctx().request_repaint();
    }
    p.circle_filled(c, r + 10.0, mix(t::SURFACE(), t::alpha(t::ACCENT(), 0.10), on));
    p.circle_stroke(c, r + 10.0, Stroke::new(2.0, mix(t::LINE(), alpha(t::ACCENT(), 0.35), on)));
    if on > 0.01 {
        let a = time * 1.3;
        icons::arc(p, c, r + 10.0, a, a + 1.2, Stroke::new(3.0, alpha(t::ACCENT(), on)));
        let b = -time * 0.8 + 2.0;
        icons::arc(p, c, r + 18.0, b, b + 0.6, Stroke::new(2.0, alpha(t::ACCENT(), on * 0.5)));
    }
    let face = mix(mix(t::CARD(), t::CARD_HOVER(), hover), t::ACCENT(), on);
    p.circle_filled(c + vec2(0.0, 2.0), r, t::shade(0.3));
    p.circle_filled(c, r, face);
    if busy {
        spinner(ui, c, r - 14.0, mix(t::ACCENT(), t::ACCENT_TEXT(), on), 3.0);
    }
    icons::draw(ui.painter(), Icon::Power, c, 40.0, mix(mix(t::MUTED(), t::TEXT(), hover), t::ACCENT_TEXT(), on));
    let _ = TAU;
    resp.on_hover_cursor(if busy { egui::CursorIcon::Wait } else { egui::CursorIcon::PointingHand })
}

pub fn progress_bar(ui: &mut Ui, id: impl std::hash::Hash + std::fmt::Debug, value: f32, width: f32) {
    let (rect, _) = ui.allocate_exact_size(vec2(width, 8.0), Sense::hover());
    let v = tween(ui.ctx(), Id::unique(id).with("pb"), value.clamp(0.0, 1.0), 0.35);
    ui.painter().rect_filled(rect, CornerRadius::same(4), t::FIELD());
    let mut fill = rect;
    fill.set_width((rect.width() * v).max(8.0));
    ui.painter().rect_filled(fill, CornerRadius::same(4), t::ACCENT());
    // a light sheen sliding along the filled part
    let time = ui.input(|i| i.time) as f32;
    let x = fill.left() + (time * 0.8).fract() * (fill.width() + 60.0) - 30.0;
    let sheen = Rect::from_center_size(pos2(x, rect.center().y), vec2(30.0, 8.0)).intersect(fill);
    if sheen.width() > 0.0 {
        ui.painter().rect_filled(sheen, CornerRadius::same(4), alpha(Color32::WHITE, 0.18));
    }
    ui.ctx().request_repaint();
}

/// A rounded card: fill, padding and a hairline border.
pub fn card() -> egui::Frame {
    egui::Frame::new()
        .fill(if crate::brand::HIDO { alpha(t::CARD(), 0.9) } else { t::CARD() })
        .corner_radius(CornerRadius::same(18))
        .stroke(Stroke::new(1.0, alpha(t::LINE(), 0.65)))
        .shadow(egui::Shadow { offset: [0, 10], blur: 30, spread: 0, color: t::shade(0.36) })
        .inner_margin(egui::Margin::same(16))
}

pub fn label(ui: &mut Ui, text: &str, font: egui::FontId, color: Color32) -> Response {
    ui.add(egui::Label::new(egui::RichText::new(crate::i18n::t(text)).font(font).color(color)).selectable(false))
}

/// Shorten `text` with an ellipsis so it fits `width` points.
pub fn elide(ui: &Ui, text: &str, font: &egui::FontId, width: f32) -> String {
    let measure = |s: &str| ui.painter().layout_no_wrap(s.to_string(), font.clone(), Color32::WHITE).size().x;
    if measure(text) <= width {
        return text.to_string();
    }
    let chars: Vec<char> = text.chars().collect();
    let (mut lo, mut hi) = (0usize, chars.len());
    while lo < hi {
        let mid = (lo + hi + 1) / 2;
        let s: String = chars[..mid].iter().collect::<String>() + "…";
        if measure(&s) <= width { lo = mid } else { hi = mid - 1 }
    }
    chars[..lo].iter().collect::<String>().trim_end().to_string() + "…"
}
