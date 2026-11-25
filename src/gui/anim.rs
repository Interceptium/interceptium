//! Time-based tweens. Values move smoothly to new targets and the window repaints at the
//! display's refresh rate only while something is moving.

use egui::{Context, Id};

#[derive(Clone, Copy)]
struct Tween {
    from: f32,
    to: f32,
    start: f64,
    dur: f32,
}

pub fn ease_out_cubic(t: f32) -> f32 {
    1.0 - (1.0 - t).powi(3)
}

pub fn ease_in_out_cubic(t: f32) -> f32 {
    if t < 0.5 { 4.0 * t * t * t } else { 1.0 - (-2.0 * t + 2.0).powi(3) / 2.0 }
}

pub fn ease_out_back(t: f32) -> f32 {
    let c1 = 1.4;
    let c3 = c1 + 1.0;
    1.0 + c3 * (t - 1.0).powi(3) + c1 * (t - 1.0).powi(2)
}

fn value(t: &Tween, now: f64, ease: fn(f32) -> f32) -> (f32, bool) {
    if t.dur <= 0.0 {
        return (t.to, true);
    }
    let p = (((now - t.start) as f32) / t.dur).clamp(0.0, 1.0);
    (t.from + (t.to - t.from) * ease(p), p >= 1.0)
}

/// Move towards `target`; the first call starts at `initial`.
pub fn tween_from(ctx: &Context, id: Id, initial: f32, target: f32, dur: f32, ease: fn(f32) -> f32) -> f32 {
    let now = ctx.input(|i| i.time);
    let id = id.with("tween");
    let mut state = ctx.data(|d| d.get_temp::<Tween>(id)).unwrap_or(Tween { from: initial, to: target, start: now, dur });
    if (state.to - target).abs() > f32::EPSILON {
        let (current, _) = value(&state, now, ease);
        state = Tween { from: current, to: target, start: now, dur };
    }
    let (v, done) = value(&state, now, ease);
    ctx.data_mut(|d| d.insert_temp(id, state));
    if !done {
        ctx.request_repaint();
    }
    v
}

/// Move towards `target`, starting there on first use (no entrance animation).
pub fn tween(ctx: &Context, id: Id, target: f32, dur: f32) -> f32 {
    tween_from(ctx, id, target, target, dur, ease_out_cubic)
}

pub fn tween_bool(ctx: &Context, id: Id, on: bool, dur: f32) -> f32 {
    tween(ctx, id, if on { 1.0 } else { 0.0 }, dur)
}

/// Forget a tween so the next use starts fresh.
pub fn reset(ctx: &Context, id: Id) {
    ctx.data_mut(|d| d.remove::<Tween>(id.with("tween")));
}

/// Progress 0..1 of a one-shot animation that started at `start` seconds.
pub fn progress(ctx: &Context, start: f64, dur: f32, ease: fn(f32) -> f32) -> f32 {
    let now = ctx.input(|i| i.time);
    let p = (((now - start) as f32) / dur).clamp(0.0, 1.0);
    if p < 1.0 {
        ctx.request_repaint();
    }
    ease(p)
}
