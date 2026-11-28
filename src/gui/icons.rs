//! Vector icons drawn with strokes, so they stay sharp at any size and scale.

use egui::epaint::{CubicBezierShape, PathShape, PathStroke};
use egui::{pos2, vec2, Color32, Painter, Pos2, Shape, Stroke};
use std::f32::consts::{PI, TAU};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Icon {
    Plus,
    Close,
    Trash,
    Pencil,
    Export,
    Import,
    Gear,
    Back,
    Search,
    Chevron,
    Folder,
    Play,
    Broom,
    Power,
    Check,
    Wrench,
    Refresh,
    Box,
    Shield,
    File,
    Minimize,
    Book,
    Maximize,
    Restore,
    Warning,
}

fn polyline(p: &Painter, pts: Vec<Pos2>, stroke: Stroke) {
    p.add(Shape::Path(PathShape::line(pts, PathStroke::new(stroke.width, stroke.color))));
}

fn arc_points(c: Pos2, r: f32, a0: f32, a1: f32, n: usize) -> Vec<Pos2> {
    (0..=n).map(|i| {
        let a = a0 + (a1 - a0) * i as f32 / n as f32;
        pos2(c.x + r * a.cos(), c.y + r * a.sin())
    }).collect()
}

pub fn arc(p: &Painter, c: Pos2, r: f32, a0: f32, a1: f32, stroke: Stroke) {
    let n = (((a1 - a0).abs() * r / 3.0).ceil() as usize).clamp(8, 160);
    polyline(p, arc_points(c, r, a0, a1, n), stroke);
}

/// Draw `icon` centred on `c`, fitting a square of side `s`.
pub fn draw(p: &Painter, icon: Icon, c: Pos2, s: f32, color: Color32) {
    let w = (s * 0.11).max(1.4);
    let st = Stroke::new(w, color);
    let h = s / 2.0;
    let at = |x: f32, y: f32| pos2(c.x + x * h, c.y + y * h);
    match icon {
        Icon::Plus => {
            p.line_segment([at(-0.7, 0.0), at(0.7, 0.0)], st);
            p.line_segment([at(0.0, -0.7), at(0.0, 0.7)], st);
        }
        Icon::Close => {
            p.line_segment([at(-0.55, -0.55), at(0.55, 0.55)], st);
            p.line_segment([at(0.55, -0.55), at(-0.55, 0.55)], st);
        }
        Icon::Trash => {
            p.line_segment([at(-0.75, -0.5), at(0.75, -0.5)], st);
            polyline(p, vec![at(-0.25, -0.5), at(-0.2, -0.78), at(0.2, -0.78), at(0.25, -0.5)], st);
            polyline(p, vec![at(-0.55, -0.5), at(-0.45, 0.78), at(0.45, 0.78), at(0.55, -0.5)], st);
            p.line_segment([at(-0.15, -0.15), at(-0.12, 0.45)], st);
            p.line_segment([at(0.15, -0.15), at(0.12, 0.45)], st);
        }
        Icon::Pencil => {
            polyline(p, vec![at(-0.7, 0.7), at(-0.62, 0.3), at(0.38, -0.7), at(0.7, -0.38), at(-0.3, 0.62), at(-0.7, 0.7)], st);
            p.line_segment([at(0.18, -0.5), at(0.5, -0.18)], st);
        }
        Icon::Export | Icon::Import => {
            let (from, to) = if icon == Icon::Export { (0.25, -0.75) } else { (-0.75, 0.25) };
            p.line_segment([at(0.0, from), at(0.0, to)], st);
            let tip = if icon == Icon::Export { -0.75 } else { 0.25 };
            let dir = if icon == Icon::Export { 1.0 } else { -1.0 };
            polyline(p, vec![at(-0.35, tip + 0.35 * dir), at(0.0, tip), at(0.35, tip + 0.35 * dir)], st);
            polyline(p, vec![at(-0.75, 0.1), at(-0.75, 0.75), at(0.75, 0.75), at(0.75, 0.1)], st);
        }
        Icon::Gear => {
            let teeth = 8;
            let mut pts = Vec::new();
            for i in 0..teeth * 4 {
                let a = i as f32 / (teeth * 4) as f32 * TAU;
                let r = if (i / 2) % 2 == 0 { 0.82 } else { 0.62 };
                pts.push(at(r * a.cos(), r * a.sin()));
            }
            pts.push(pts[0]);
            polyline(p, pts, st);
            p.circle_stroke(c, h * 0.26, st);
        }
        Icon::Back => polyline(p, vec![at(0.25, -0.6), at(-0.35, 0.0), at(0.25, 0.6)], st),
        Icon::Chevron => polyline(p, vec![at(-0.2, -0.45), at(0.25, 0.0), at(-0.2, 0.45)], st),
        Icon::Search => {
            p.circle_stroke(at(-0.15, -0.15), h * 0.48, st);
            p.line_segment([at(0.2, 0.2), at(0.68, 0.68)], st);
        }
        Icon::Folder => polyline(p, vec![at(-0.8, 0.65), at(-0.8, -0.6), at(-0.25, -0.6), at(-0.05, -0.38), at(0.8, -0.38), at(0.8, 0.65), at(-0.8, 0.65)], st),
        Icon::Play => {
            let pts = vec![at(-0.35, -0.6), at(0.6, 0.0), at(-0.35, 0.6)];
            p.add(Shape::convex_polygon(pts, color, Stroke::NONE));
        }
        Icon::Broom => {
            p.line_segment([at(0.65, -0.75), at(0.05, 0.05)], st);
            polyline(p, vec![at(-0.2, -0.15), at(0.3, 0.35), at(0.0, 0.8), at(-0.75, 0.05), at(-0.2, -0.15)], st);
        }
        Icon::Power => {
            arc(p, c, h * 0.62, -PI / 2.0 + 0.7, PI * 1.5 - 0.7, st);
            p.line_segment([at(0.0, -0.8), at(0.0, -0.1)], st);
        }
        Icon::Check => polyline(p, vec![at(-0.6, 0.0), at(-0.15, 0.45), at(0.65, -0.45)], st),
        Icon::Wrench => {
            arc(p, at(0.3, -0.3), h * 0.38, PI * 0.75, PI * 2.25 - 0.9, st);
            p.line_segment([at(0.05, -0.05), at(-0.65, 0.65)], st);
        }
        Icon::Refresh => {
            arc(p, c, h * 0.6, -PI * 0.15, PI * 1.45, st);
            let tip = pos2(c.x + h * 0.6 * (-PI * 0.15f32).cos(), c.y + h * 0.6 * (-PI * 0.15f32).sin());
            polyline(p, vec![tip + vec2(-h * 0.38, -h * 0.05), tip, tip + vec2(h * 0.05, -h * 0.4)], st);
        }
        Icon::Box => {
            polyline(p, vec![at(0.0, -0.8), at(0.75, -0.42), at(0.75, 0.42), at(0.0, 0.8), at(-0.75, 0.42), at(-0.75, -0.42), at(0.0, -0.8)], st);
            polyline(p, vec![at(-0.75, -0.42), at(0.0, -0.04), at(0.75, -0.42)], st);
            p.line_segment([at(0.0, -0.04), at(0.0, 0.8)], st);
        }
        Icon::Shield => {
            let shape = CubicBezierShape::from_points_stroke([at(0.0, 0.85), at(-0.75, 0.45), at(-0.7, -0.2), at(-0.7, -0.55)], false, Color32::TRANSPARENT, st);
            p.add(shape);
            let shape = CubicBezierShape::from_points_stroke([at(0.0, 0.85), at(0.75, 0.45), at(0.7, -0.2), at(0.7, -0.55)], false, Color32::TRANSPARENT, st);
            p.add(shape);
            polyline(p, vec![at(-0.7, -0.55), at(0.0, -0.85), at(0.7, -0.55)], st);
        }
        Icon::Book => {
            polyline(p, vec![at(0.0, -0.55), at(-0.35, -0.72), at(-0.8, -0.72), at(-0.8, 0.62), at(-0.35, 0.62), at(0.0, 0.78), at(0.35, 0.62), at(0.8, 0.62), at(0.8, -0.72), at(0.35, -0.72), at(0.0, -0.55)], st);
            p.line_segment([at(0.0, -0.55), at(0.0, 0.78)], st);
        }
        Icon::Minimize => {
            p.line_segment([at(-0.55, 0.0), at(0.55, 0.0)], st);
        }
        Icon::Maximize => {
            let r = egui::Rect::from_center_size(c, egui::vec2(h * 1.1, h * 1.1));
            p.rect_stroke(r, egui::CornerRadius::same((h * 0.22) as u8), st, egui::StrokeKind::Middle);
        }
        Icon::Restore => {
            let back = egui::Rect::from_center_size(at(0.14, -0.14), egui::vec2(h * 0.9, h * 0.9));
            polyline(p, vec![back.left_top() + vec2(0.0, h * 0.22), back.left_top(), back.right_top(), back.right_bottom(), back.right_bottom() - vec2(h * 0.22, 0.0)], st);
            let front = egui::Rect::from_center_size(at(-0.12, 0.12), egui::vec2(h * 0.9, h * 0.9));
            p.rect_stroke(front, egui::CornerRadius::same((h * 0.18) as u8), st, egui::StrokeKind::Middle);
        }
        Icon::Warning => {
            polyline(p, vec![at(0.0, -0.8), at(0.82, 0.68), at(-0.82, 0.68), at(0.0, -0.8)], st);
            p.line_segment([at(0.0, -0.25), at(0.0, 0.2)], st);
            p.circle_filled(at(0.0, 0.45), w * 0.7, color);
        }
        Icon::File => {
            polyline(p, vec![at(-0.55, -0.8), at(0.2, -0.8), at(0.6, -0.4), at(0.6, 0.8), at(-0.55, 0.8), at(-0.55, -0.8)], st);
            polyline(p, vec![at(0.2, -0.8), at(0.2, -0.4), at(0.6, -0.4)], st);
        }
    }
}
