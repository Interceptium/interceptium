//! Interceptium draws its own title bar: the window has no system frame, so dragging,
//! double-click to maximize, the window buttons and edge resizing are handled here.

use super::anim::tween_bool;
use super::icons::{self, Icon};
use super::theme::{self as t, alpha, mix};
use egui::{vec2, Color32, CursorIcon, Id, Rect, ResizeDirection, Sense, Ui, ViewportCommand};

/// Corner radius of the whole window (painted ourselves on macOS; Windows 11 rounds natively).
pub fn window_radius(ctx: &egui::Context) -> u8 {
    let maximized = ctx.input(|i| i.viewport().maximized.unwrap_or(false));
    if cfg!(target_os = "macos") && !maximized { 12 } else { 0 }
}

/// Make the empty parts of `rect` move the window; double-click toggles maximize.
/// Call before drawing anything clickable in that area, so buttons on top keep working.
pub fn drag_area(ui: &mut Ui, rect: Rect, can_maximize: bool) {
    let resp = ui.interact(rect, Id::unique("title-drag"), Sense::click_and_drag());
    if resp.drag_started_by(egui::PointerButton::Primary) {
        ui.ctx().send_viewport_cmd(ViewportCommand::StartDrag);
    }
    if can_maximize && resp.double_clicked() {
        let maximized = ui.ctx().input(|i| i.viewport().maximized.unwrap_or(false));
        ui.ctx().send_viewport_cmd(ViewportCommand::Maximized(!maximized));
    }
}

fn window_button(ui: &mut Ui, id: &str, icon: Icon, danger: bool, tip: &str) -> bool {
    let size = 34.0;
    let (rect, resp) = ui.allocate_exact_size(vec2(size, size), Sense::click());
    let hover = tween_bool(ui.ctx(), Id::unique(("winbtn", id)), resp.hovered(), 0.14);
    let press = tween_bool(ui.ctx(), Id::unique(("winbtn-p", id)), resp.is_pointer_button_down_on(), 0.08);
    let fill = if danger { t::DANGER() } else { t::GHOST_HOVER() };
    ui.painter().circle_filled(rect.center(), size / 2.0 - press, alpha(fill, hover * if danger { 0.95 } else { 1.0 }));
    let fg = if danger { mix(t::MUTED(), Color32::WHITE, hover) } else { mix(t::MUTED(), t::TEXT(), hover) };
    icons::draw(ui.painter(), icon, rect.center(), 15.0, fg);
    resp.on_hover_text(crate::i18n::t(tip)).clicked()
}

/// Minimize, maximize/restore and close, right to left. Use inside a right-to-left layout.
pub fn window_buttons(ui: &mut Ui, can_maximize: bool) {
    ui.spacing_mut().item_spacing.x = 4.0;
    if window_button(ui, "close", Icon::Close, true, "Close") {
        ui.ctx().send_viewport_cmd(ViewportCommand::Close);
    }
    if can_maximize {
        let maximized = ui.ctx().input(|i| i.viewport().maximized.unwrap_or(false));
        let (icon, tip) = if maximized { (Icon::Restore, "Restore") } else { (Icon::Maximize, "Maximize") };
        if window_button(ui, "max", icon, false, tip) {
            ui.ctx().send_viewport_cmd(ViewportCommand::Maximized(!maximized));
        }
    }
    if window_button(ui, "min", Icon::Minimize, false, "Minimize") {
        ui.ctx().send_viewport_cmd(ViewportCommand::Minimized(true));
    }
}

/// Invisible handles along the window edges for resizing. Call last, so they sit on top.
pub fn resize_edges(ui: &mut Ui, rect: Rect) {
    if ui.ctx().input(|i| i.viewport().maximized.unwrap_or(false) || i.viewport().fullscreen.unwrap_or(false)) {
        return;
    }
    let e = 5.0;
    let c = 12.0;
    let zones = [
        (Rect::from_min_max(rect.left_top(), rect.right_top() + vec2(0.0, e)), ResizeDirection::North, CursorIcon::ResizeVertical),
        (Rect::from_min_max(rect.left_bottom() - vec2(0.0, e), rect.right_bottom()), ResizeDirection::South, CursorIcon::ResizeVertical),
        (Rect::from_min_max(rect.left_top(), rect.left_bottom() + vec2(e, 0.0)), ResizeDirection::West, CursorIcon::ResizeHorizontal),
        (Rect::from_min_max(rect.right_top() - vec2(e, 0.0), rect.right_bottom()), ResizeDirection::East, CursorIcon::ResizeHorizontal),
        (Rect::from_min_size(rect.left_top(), vec2(c, c)), ResizeDirection::NorthWest, CursorIcon::ResizeNwSe),
        (Rect::from_min_size(rect.right_top() - vec2(c, 0.0), vec2(c, c)), ResizeDirection::NorthEast, CursorIcon::ResizeNeSw),
        (Rect::from_min_size(rect.left_bottom() - vec2(0.0, c), vec2(c, c)), ResizeDirection::SouthWest, CursorIcon::ResizeNeSw),
        (Rect::from_min_size(rect.right_bottom() - vec2(c, c), vec2(c, c)), ResizeDirection::SouthEast, CursorIcon::ResizeNwSe),
    ];
    for (i, (zone, dir, cursor)) in zones.into_iter().enumerate() {
        let resp = ui.interact(zone, Id::unique(("resize-edge", i)), Sense::drag());
        if resp.hovered() || resp.dragged() {
            ui.ctx().set_cursor_icon(cursor);
        }
        if resp.drag_started_by(egui::PointerButton::Primary) {
            ui.ctx().send_viewport_cmd(ViewportCommand::BeginResize(dir));
        }
    }
}

/// Ask Windows 11 for rounded window corners (with the native border and shadow).
/// Earlier Windows versions ignore this and keep square corners.
pub fn round_native_corners(frame: &eframe::Frame) {
    #[cfg(windows)]
    {
        use raw_window_handle::{HasWindowHandle, RawWindowHandle};
        if let Ok(handle) = frame.window_handle() {
            if let RawWindowHandle::Win32(h) = handle.as_raw() {
                crate::winsys::round_corners(h.hwnd.get());
            }
        }
    }
    #[cfg(not(windows))]
    let _ = frame;
}
