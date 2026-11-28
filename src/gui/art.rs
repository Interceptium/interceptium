//! Hidoceptium's background: an original, animated sea scene drawn in code (sky-to-sea
//! gradient, sunlight, rolling waves and floating swirled fruits). No images are used.

use super::theme::{self as t, alpha, mix};
use egui::epaint::Mesh;
use egui::{pos2, vec2, Color32, Painter, Pos2, Rect, Shape, Stroke};
use std::f32::consts::TAU;

fn rgb(v: u32) -> Color32 {
    Color32::from_rgb((v >> 16) as u8, (v >> 8) as u8, v as u8)
}

fn gradient(p: &Painter, rect: Rect, top: Color32, bottom: Color32) {
    let mut m = Mesh::default();
    m.colored_vertex(rect.left_top(), top);
    m.colored_vertex(rect.right_top(), top);
    m.colored_vertex(rect.left_bottom(), bottom);
    m.colored_vertex(rect.right_bottom(), bottom);
    m.add_triangle(0, 1, 2);
    m.add_triangle(1, 3, 2);
    p.add(Shape::mesh(m));
}

fn wave(p: &Painter, rect: Rect, base: f32, amp: f32, len: f32, phase: f32, top: Color32, bottom: Color32) {
    let mut m = Mesh::default();
    let steps = ((rect.width() / 12.0).ceil() as usize).max(8);
    for i in 0..=steps {
        let x = rect.left() + rect.width() * i as f32 / steps as f32;
        let y = base + amp * ((x / len) * TAU + phase).sin() + amp * 0.35 * ((x / (len * 0.45)) * TAU - phase * 1.7).sin();
        m.colored_vertex(pos2(x, y), top);
        m.colored_vertex(pos2(x, rect.bottom()), bottom);
        if i > 0 {
            let k = (i * 2) as u32;
            m.add_triangle(k - 2, k - 1, k);
            m.add_triangle(k - 1, k + 1, k);
        }
    }
    p.add(Shape::mesh(m));
}

/// A round "devil fruit": body, swirl pattern, stem and leaf.
fn fruit(p: &Painter, c: Pos2, r: f32, body: Color32, swirl: Color32, spin: f32, strength: f32) {
    p.circle_filled(c + vec2(0.0, r * 0.12), r, alpha(Color32::BLACK, 0.10 * strength));
    p.circle_filled(c, r, alpha(body, 0.30 * strength));
    for k in 0..3 {
        let start = spin + k as f32 * TAU / 3.0;
        let pts: Vec<Pos2> = (0..=28)
            .map(|i| {
                let f = i as f32 / 28.0;
                let a = start + f * TAU * 0.9;
                let rr = r * (0.18 + 0.62 * f);
                pos2(c.x + a.cos() * rr, c.y + a.sin() * rr)
            })
            .collect();
        p.add(Shape::line(pts, Stroke::new((r * 0.09).max(1.2), alpha(swirl, 0.42 * strength))));
    }
    p.circle_filled(c + vec2(-r * 0.32, -r * 0.34), r * 0.16, alpha(Color32::WHITE, 0.16 * strength));
    let top = c + vec2(0.0, -r);
    p.line_segment([top, top + vec2(r * 0.12, -r * 0.32)], Stroke::new((r * 0.08).max(1.2), alpha(rgb(0x6b4a2b), 0.5 * strength)));
    let leaf: Vec<Pos2> = (0..12)
        .map(|i| {
            let a = i as f32 / 12.0 * TAU;
            let l = vec2(a.cos() * r * 0.36, a.sin() * r * 0.15);
            let rot = egui::emath::Rot2::from_angle(-0.5);
            top + vec2(r * 0.34, -r * 0.24) + rot * l
        })
        .collect();
    p.add(Shape::convex_polygon(leaf, alpha(rgb(0x3fcf7a), 0.40 * strength), Stroke::NONE));
}

/// Paint the sea scene into `rect`. Moves slowly; repaints continuously while shown.
pub fn ocean(p: &Painter, rect: Rect, time: f32) {
    let l = t::light_amount();
    let sky_top = mix(rgb(0x0b2c4a), rgb(0xbfe7fb), l);
    let sea = mix(rgb(0x04121f), rgb(0x8bd0ef), l);
    gradient(p, rect, sky_top, sea);
    // sunlight
    let sun = pos2(rect.right() - rect.width() * 0.14, rect.top() + rect.height() * 0.12);
    for i in 0..5 {
        let k = i as f32 / 5.0;
        p.circle_filled(sun, 40.0 + k * 220.0, alpha(mix(rgb(0xffc861), rgb(0xffd98a), l), (0.07 - 0.0) * (1.0 - k) * (0.6 + 0.4 * l)));
    }
    // floating fruits
    let fruits = [
        (0.08, 0.30, 34.0, 0x8a5cff, 0xd9c6ff),
        (0.93, 0.46, 42.0, 0xff8a2b, 0xffe0a8),
        (0.50, 0.12, 26.0, 0x2fc4ff, 0xd2f2ff),
        (0.28, 0.80, 30.0, 0xff4f7b, 0xffd0dc),
        (0.74, 0.86, 36.0, 0x46d38a, 0xd4ffe7),
    ];
    for (i, (fx, fy, r, body, swirl)) in fruits.iter().enumerate() {
        let bob = (time * 0.55 + i as f32 * 1.3).sin() * 9.0;
        let drift = (time * 0.21 + i as f32).cos() * 6.0;
        let c = pos2(rect.left() + rect.width() * fx + drift, rect.top() + rect.height() * fy + bob);
        fruit(p, c, *r, rgb(*body), rgb(*swirl), time * 0.25 + i as f32, 1.0 - 0.35 * l);
    }
    // waves
    let h = rect.height();
    let layers = [
        (0.70, 9.0, 520.0, 0.35, 0x1d6fa8, 0x4fb3e6, 0.22),
        (0.78, 7.0, 380.0, -0.5, 0x15598c, 0x3aa2d9, 0.28),
        (0.87, 5.0, 300.0, 0.8, 0x0e4470, 0x2c8fc6, 0.34),
    ];
    for (base, amp, len, speed, dark, light, a) in layers {
        let col = mix(rgb(dark), rgb(light), l);
        wave(p, rect, rect.top() + h * base, amp, len, time * speed, alpha(col, a), alpha(col, a * 1.4));
    }
}
