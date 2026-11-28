//! Test-build only (feature "shots"): put the window in a given state, take a screenshot
//! and report frame timings, so the UI can be checked without a person at the screen.

use super::main_window::MainApp;

pub struct Shots {
    out: Option<String>,
    scene: String,
    frame: u32,
    asked: bool,
    times: Vec<f32>,
    done: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

impl Shots {
    pub fn from_env() -> Shots {
        Shots {
            out: std::env::var("INTERCEPTIUM_SHOT").ok(),
            scene: std::env::var("INTERCEPTIUM_SHOT_SCENE").unwrap_or_default(),
            frame: 0,
            asked: false,
            times: Vec::new(),
            done: Default::default(),
        }
    }
}

pub fn drive(app: &mut MainApp, ctx: &egui::Context) {
    let mut shots = std::mem::replace(&mut app.shots, Shots { out: None, scene: String::new(), frame: 0, asked: false, times: vec![], done: Default::default() });
    shots.tick(ctx, |scene| {
        for part in scene.split(',') {
            match part {
                "settings0" => app.shot_open_settings(0),
                "settings1" => app.shot_open_settings(1),
                "settings2" => app.shot_open_settings(2),
                "rule" => app.shot_rule_editor(ctx),
                "log" => app.shot_log(),
                "run" => app.shot_start(ctx),
                "light" => app.shot_light(),
                "cdnwarn" => app.shot_cdn_warning(ctx),
                "cdnrule" => app.shot_cdn_rule(ctx),
                _ => {}
            }
        }
    });
    app.shots = shots;
}

impl Shots {
pub fn tick(&mut self, ctx: &egui::Context, apply: impl FnOnce(&str)) {
    let app = self;
    let Some(out) = app.out.clone() else { return };
    app.frame += 1;
    let frame = app.frame;
    let dt = ctx.input(|i| i.unstable_dt);
    if frame > 3 {
        app.times.push(dt);
    }
    if frame == 3 {
        let scene = app.scene.clone();
        apply(&scene);
    }
    ctx.request_repaint();
    if frame == 90 && !app.asked {
        app.asked = true;
        let done = app.done.clone();
        let path = out.clone();
        let repaint = ctx.clone();
        ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::ScreenshotCallback::new(move |img| {
            let [w, h] = img.size;
            let mut raw = Vec::with_capacity(w * h * 4);
            for p in &img.pixels {
                raw.extend_from_slice(&p.to_array());
            }
            if let Some(buf) = image::RgbaImage::from_raw(w as u32, h as u32, raw) {
                let _ = buf.save(&path);
            }
            done.store(true, std::sync::atomic::Ordering::SeqCst);
            repaint.request_repaint();
        })));
    }
    if app.done.load(std::sync::atomic::Ordering::SeqCst) {
        let t = &app.times;
        if !t.is_empty() {
            let mut s = t.clone();
            s.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let avg = s.iter().sum::<f32>() / s.len() as f32;
            eprintln!("frames {} avg {:.2} ms p95 {:.2} ms max {:.2} ms", s.len(), avg * 1000.0, s[s.len() * 95 / 100] * 1000.0, s[s.len() - 1] * 1000.0);
        }
        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
    }
}
}

impl Shots {
    pub fn none() -> Shots {
        Shots { out: None, scene: String::new(), frame: 0, asked: false, times: vec![], done: Default::default() }
    }
}
