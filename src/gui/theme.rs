//! Colours and fonts.

use egui::{Color32, FontData, FontDefinitions, FontFamily, FontId};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;

// Every colour has a dark and a light value. `light_amount` (0 = dark, 1 = light) is moved
// smoothly when the appearance changes, so the whole window cross-fades between them.
static LIGHT: AtomicU32 = AtomicU32::new(0);

pub fn light_amount() -> f32 {
    f32::from_bits(LIGHT.load(Ordering::Relaxed))
}

pub fn set_light_amount(v: f32) {
    LIGHT.store(v.clamp(0.0, 1.0).to_bits(), Ordering::Relaxed);
}

const fn rgb(v: u32) -> Color32 {
    Color32::from_rgb((v >> 16) as u8, (v >> 8) as u8, v as u8)
}

macro_rules! palette {
    ($($name:ident = $dark:expr, $light:expr;)*) => {
        $(
            #[allow(non_snake_case)]
            pub fn $name() -> Color32 {
                let f = light_amount();
                if f <= 0.0 { rgb($dark) } else if f >= 1.0 { rgb($light) } else { mix(rgb($dark), rgb($light), f) }
            }
        )*
    };
}

#[cfg(not(feature = "hido"))]
palette! {
    BG = 0x0b0d13, 0xf3f5f9;
    SURFACE = 0x11141c, 0xffffff;
    CARD = 0x171b26, 0xffffff;
    CARD_HOVER = 0x1d2230, 0xf2f5f9;
    CARD_SELECTED = 0x18262c, 0xe4f6f4;
    FIELD = 0x0d1017, 0xeef1f6;
    LINE = 0x262c3b, 0xdde2ea;
    TEXT = 0xeceff5, 0x10141f;
    MUTED = 0x8a92a5, 0x5b6475;
    FAINT = 0x5a6275, 0x8a92a2;
    ACCENT = 0x2fd3c7, 0x14b3a6;
    ACCENT_HOVER = 0x52ddd3, 0x22c2b5;
    ACCENT_PRESS = 0x25b5aa, 0x0f9a8f;
    ACCENT_TEXT = 0x04201d, 0x03201c;
    DANGER = 0xff5d7a, 0xe0365a;
    WARN = 0xf2b54a, 0xc4860c;
    BLUE = 0x7ea6ff, 0x3d6ddf;
    TRACK_OFF = 0x2d3446, 0xcdd3de;
    CHIP = 0x212736, 0xedf0f5;
    GHOST = 0x1e2330, 0xeceff4;
    GHOST_HOVER = 0x272d3d, 0xe1e6ee;
}

// Hidoceptium: deep sea, golden sand and sky (a Blox Fruits-style palette).
#[cfg(feature = "hido")]
palette! {
    BG = 0x061726, 0xe8f5fc;
    SURFACE = 0x0a2034, 0xffffff;
    CARD = 0x0d2840, 0xffffff;
    CARD_HOVER = 0x123555, 0xeef7fd;
    CARD_SELECTED = 0x1a3a46, 0xfff3dc;
    FIELD = 0x071a2b, 0xe9f2f8;
    LINE = 0x1d4263, 0xcfe0ec;
    TEXT = 0xf2f7fb, 0x0c1d2c;
    MUTED = 0x93acc2, 0x4f667a;
    FAINT = 0x5f7d96, 0x8499ab;
    ACCENT = 0xffb534, 0xe48f00;
    ACCENT_HOVER = 0xffc65e, 0xf29c0a;
    ACCENT_PRESS = 0xe89c1d, 0xc97c00;
    ACCENT_TEXT = 0x2a1600, 0x2a1600;
    DANGER = 0xff5f6d, 0xd93a4a;
    WARN = 0xb98cff, 0x7a4fd6;
    BLUE = 0x4cc6ff, 0x1d86c9;
    TRACK_OFF = 0x24486a, 0xc4d5e2;
    CHIP = 0x15334f, 0xe6f0f7;
    GHOST = 0x123150, 0xe5eff6;
    GHOST_HOVER = 0x1a3f63, 0xd8e7f1;
}

/// A shadow colour: full strength on dark, much softer on light backgrounds.
pub fn shade(a: f32) -> Color32 {
    alpha(Color32::BLACK, a * (1.0 - 0.72 * light_amount()))
}

const SEMI: &str = "ui-semibold";
const MEDIUM: &str = "ui-medium";

/// Apple's system font on a Mac (it is part of macOS, so it is loaded from there and never
/// bundled); everywhere else Inter, the closest open-source match. Logs use Geist Mono.
pub fn install_fonts(ctx: &egui::Context) {
    use egui::epaint::text::{FontTweak, VariationCoords};
    let mut fonts = FontDefinitions::default();
    let builtin = fonts.families.get(&FontFamily::Proportional).cloned().unwrap_or_default();
    let mut put = |name: &str, data: FontData| {
        fonts.font_data.insert(name.into(), Arc::new(data));
    };
    put("inter", FontData::from_static(include_bytes!("../../assets/fonts/Inter-Regular.ttf")));
    put("inter-medium", FontData::from_static(include_bytes!("../../assets/fonts/Inter-Medium.ttf")));
    put("inter-display-semibold", FontData::from_static(include_bytes!("../../assets/fonts/InterDisplay-SemiBold.ttf")));
    put("geist-mono", FontData::from_static(include_bytes!("../../assets/fonts/GeistMono-Regular.ttf")));
    let mut regular = vec!["inter".to_string()];
    let mut medium = vec!["inter-medium".to_string()];
    let mut semi = vec!["inter-display-semibold".to_string()];
    if let Some(sf) = apple_system_font() {
        let sf: Arc<dyn AsRef<[u8]> + Send + Sync> = Arc::new(sf);
        for (name, weight, list) in [("sf", 400.0, &mut regular), ("sf-medium", 510.0, &mut medium), ("sf-semibold", 590.0, &mut semi)] {
            let tweak = FontTweak { coords: VariationCoords::new([(*b"wght", weight)]), ..Default::default() };
            put(name, FontData::from_blob(sf.clone(), 0).tweak(tweak));
            list.insert(0, name.to_string());
        }
    }
    fonts.families.insert(FontFamily::Proportional, [regular, builtin.clone()].concat());
    fonts.families.insert(FontFamily::Name(MEDIUM.into()), [medium, builtin.clone()].concat());
    fonts.families.insert(FontFamily::Name(SEMI.into()), [semi, builtin].concat());
    let mono_fallback = fonts.families.get(&FontFamily::Monospace).cloned().unwrap_or_default();
    fonts.families.insert(FontFamily::Monospace, [vec!["geist-mono".to_string()], mono_fallback].concat());
    ctx.set_fonts(fonts);
}

#[cfg(target_os = "macos")]
fn apple_system_font() -> Option<Vec<u8>> {
    std::fs::read("/System/Library/Fonts/SFNS.ttf").ok()
}

#[cfg(not(target_os = "macos"))]
fn apple_system_font() -> Option<Vec<u8>> {
    None
}

pub fn regular(size: f32) -> FontId {
    FontId::new(size, FontFamily::Proportional)
}

pub fn medium(size: f32) -> FontId {
    FontId::new(size, FontFamily::Name(MEDIUM.into()))
}

/// Titles (18 pt and up) use the semibold display cut; smaller text stays medium, never bold.
pub fn semi(size: f32) -> FontId {
    if size >= 18.0 { FontId::new(size, FontFamily::Name(SEMI.into())) } else { medium(size) }
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
        s.visuals = if light_amount() >= 0.5 { egui::Visuals::light() } else { egui::Visuals::dark() };
        s.visuals.panel_fill = BG();
        s.visuals.window_fill = SURFACE();
        s.visuals.extreme_bg_color = FIELD();
        s.visuals.override_text_color = Some(TEXT());
        s.visuals.selection.bg_fill = alpha(ACCENT(), 0.35);
        s.visuals.selection.stroke = egui::Stroke::new(1.0, ACCENT());
        s.visuals.text_cursor.stroke = egui::Stroke::new(2.0, ACCENT());
        s.visuals.hyperlink_color = ACCENT();
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
