//! The desktop window (egui).

pub mod anim;
pub mod icons;
pub mod main_window;
pub mod popup;
#[cfg(feature = "shots")]
pub mod shots;
pub mod theme;
pub mod widgets;

use std::sync::Arc;

pub const LOGO_PNG: &[u8] = include_bytes!("../../assets/logo.png");
pub const ICON_PNG: &[u8] = include_bytes!("../../assets/logo_96.png");

/// Follow the chosen appearance ("dark", "light" or "system"), cross-fading on changes.
pub fn update_appearance(ctx: &egui::Context, choice: &str) {
    let light = match choice {
        "light" => true,
        "system" => ctx.system_theme() == Some(egui::Theme::Light),
        _ => false,
    };
    let f = anim::tween(ctx, egui::Id::unique("appearance"), if light { 1.0 } else { 0.0 }, 0.45);
    let applied = ctx.data(|d| d.get_temp::<f32>(egui::Id::unique("appearance-applied")));
    if applied.map(|a| (a - f).abs() > 1e-4).unwrap_or(true) {
        theme::set_light_amount(f);
        theme::apply_style(ctx);
        ctx.data_mut(|d| d.insert_temp(egui::Id::unique("appearance-applied"), f));
    }
}

pub fn setup_context(ctx: &egui::Context) {
    theme::install_fonts(ctx);
    theme::apply_style(ctx);
}

fn decode(png: &[u8]) -> Option<(u32, u32, Vec<u8>)> {
    let img = image::load_from_memory_with_format(png, image::ImageFormat::Png).ok()?.to_rgba8();
    Some((img.width(), img.height(), img.into_raw()))
}

pub fn logo_texture(ctx: &egui::Context) -> egui::TextureHandle {
    let (w, h, rgba) = decode(LOGO_PNG).unwrap_or((1, 1, vec![0, 0, 0, 0]));
    let img = egui::ColorImage::from_rgba_unmultiplied([w as usize, h as usize], &rgba);
    ctx.load_texture("logo", img, egui::TextureOptions::LINEAR)
}

pub fn window_icon() -> egui::IconData {
    let (width, height, rgba) = decode(ICON_PNG).unwrap_or((1, 1, vec![0, 0, 0, 0]));
    egui::IconData { rgba, width, height }
}

pub fn native_options(size: [f32; 2], min: [f32; 2], resizable: bool, renderer: eframe::Renderer) -> eframe::NativeOptions {
    eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("Interceptium")
            .with_app_id("interceptium")
            .with_inner_size(size)
            .with_min_inner_size(min)
            .with_resizable(resizable)
            .with_icon(Arc::new(window_icon())),
        renderer,
        centered: true,
        ..Default::default()
    }
}

/// Run an eframe app, falling back to the OpenGL renderer if the default one can't start.
pub fn run_window(size: [f32; 2], min: [f32; 2], resizable: bool, make: impl Fn(&eframe::CreationContext<'_>) -> Box<dyn eframe::App>) -> i32 {
    let forced_gl = std::env::var("INTERCEPTIUM_RENDERER").map(|v| v.eq_ignore_ascii_case("glow")).unwrap_or(false);
    let order = if forced_gl { vec![eframe::Renderer::Glow] } else { vec![eframe::Renderer::Wgpu, eframe::Renderer::Glow] };
    let mut last = String::new();
    for renderer in order {
        let result = eframe::run_native("Interceptium", native_options(size, min, resizable, renderer), Box::new(|cc| Ok(make(cc))));
        match result {
            Ok(()) => return 0,
            Err(e) => last = e.to_string(),
        }
    }
    crate::log!("Window", "could not open a window: {last}");
    eprintln!("Interceptium could not open a window: {last}");
    1
}

pub fn run() -> i32 {
    let ctl = match crate::app::Controller::new() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Interceptium could not start: {e}");
            return 1;
        }
    };
    let code = run_window([1200.0, 800.0], [980.0, 660.0], true, |cc| Box::new(main_window::MainApp::new(cc, ctl.clone())));
    ctl.stop();
    code
}
