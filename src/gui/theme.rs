//! Colours and fonts.

use egui::{Color32, FontData, FontDefinitions, FontFamily, FontId};
use std::sync::Arc;

pub const BG: Color32 = Color32::from_rgb(0x0b, 0x0d, 0x13);
pub const SURFACE: Color32 = Color32::from_rgb(0x11, 0x14, 0x1c);
pub const CARD: Color32 = Color32::from_rgb(0x17, 0x1b, 0x26);
pub const CARD_HOVER: Color32 = Color32::from_rgb(0x1d, 0x22, 0x30);
pub const CARD_SELECTED: Color32 = Color32::from_rgb(0x18, 0x26, 0x2c);
pub const FIELD: Color32 = Color32::from_rgb(0x0d, 0x10, 0x17);
pub const LINE: Color32 = Color32::from_rgb(0x26, 0x2c, 0x3b);
pub const TEXT: Color32 = Color32::from_rgb(0xec, 0xef, 0xf5);
pub const MUTED: Color32 = Color32::from_rgb(0x8a, 0x92, 0xa5);
pub const FAINT: Color32 = Color32::from_rgb(0x5a, 0x62, 0x75);
pub const ACCENT: Color32 = Color32::from_rgb(0x2f, 0xd3, 0xc7);
pub const ACCENT_HOVER: Color32 = Color32::from_rgb(0x52, 0xdd, 0xd3);
pub const ACCENT_PRESS: Color32 = Color32::from_rgb(0x25, 0xb5, 0xaa);
pub const ACCENT_TEXT: Color32 = Color32::from_rgb(0x04, 0x20, 0x1d);
pub const DANGER: Color32 = Color32::from_rgb(0xff, 0x5d, 0x7a);
pub const WARN: Color32 = Color32::from_rgb(0xf2, 0xb5, 0x4a);
pub const BLUE: Color32 = Color32::from_rgb(0x7e, 0xa6, 0xff);
pub const TRACK_OFF: Color32 = Color32::from_rgb(0x2d, 0x34, 0x46);
pub const CHIP: Color32 = Color32::from_rgb(0x21, 0x27, 0x36);
pub const GHOST: Color32 = Color32::from_rgb(0x1e, 0x23, 0x30);
pub const GHOST_HOVER: Color32 = Color32::from_rgb(0x27, 0x2d, 0x3d);

const SEMI: &str = "inter-semibold";

pub fn install_fonts(ctx: &egui::Context) {
    let mut fonts = FontDefinitions::default();
    fonts.font_data.insert("inter".into(), Arc::new(FontData::from_static(include_bytes!("../../assets/fonts/Inter-Regular.ttf"))));
    fonts.font_data.insert(SEMI.into(), Arc::new(FontData::from_static(include_bytes!("../../assets/fonts/Inter-SemiBold.ttf"))));
    fonts.families.entry(FontFamily::Proportional).or_default().insert(0, "inter".into());
    let mut semi = vec![SEMI.to_string()];
    semi.extend(fonts.families.get(&FontFamily::Proportional).cloned().unwrap_or_default());
    fonts.families.insert(FontFamily::Name(SEMI.into()), semi);
    ctx.set_fonts(fonts);
}

pub fn regular(size: f32) -> FontId {
    FontId::new(size, FontFamily::Proportional)
}

pub fn semi(size: f32) -> FontId {
    FontId::new(size, FontFamily::Name(SEMI.into()))
}

pub fn mono(size: f32) -> FontId {
    FontId::new(size, FontFamily::Monospace)
}

pub fn mix(a: Color32, b: Color32, t: f32) -> Color32 {
    let t = t.clamp(0.0, 1.0);
    let l = |x: u8, y: u8| (x as f32 + (y as f32 - x as f32) * t).round() as u8;
    Color32::from_rgba_unmultiplied(l(a.r(), b.r()), l(a.g(), b.g()), l(a.b(), b.b()), l(a.a(), b.a()))
}

pub fn alpha(c: Color32, a: f32) -> Color32 {
    Color32::from_rgba_unmultiplied(c.r(), c.g(), c.b(), (a.clamp(0.0, 1.0) * 255.0).round() as u8)
}

pub fn apply_style(ctx: &egui::Context) {
    ctx.all_styles_mut(|s| {
        s.visuals = egui::Visuals::dark();
        s.visuals.panel_fill = BG;
        s.visuals.window_fill = SURFACE;
        s.visuals.extreme_bg_color = FIELD;
        s.visuals.override_text_color = Some(TEXT);
        s.visuals.selection.bg_fill = alpha(ACCENT, 0.35);
        s.visuals.selection.stroke = egui::Stroke::new(1.0, ACCENT);
        s.visuals.text_cursor.stroke = egui::Stroke::new(2.0, ACCENT);
        s.visuals.hyperlink_color = ACCENT;
        s.spacing.item_spacing = egui::vec2(8.0, 8.0);
        s.spacing.scroll.bar_width = 6.0;
        s.spacing.scroll.floating = true;
        s.interaction.selectable_labels = false;
        s.animation_time = 0.22;
        s.text_styles.insert(egui::TextStyle::Body, regular(14.0));
        s.text_styles.insert(egui::TextStyle::Button, regular(14.0));
        s.text_styles.insert(egui::TextStyle::Monospace, mono(12.5));
        s.text_styles.insert(egui::TextStyle::Small, regular(12.0));
        s.text_styles.insert(egui::TextStyle::Heading, semi(22.0));
    });
}
