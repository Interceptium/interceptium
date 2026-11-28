//! Thread-safe log fan-out used by the proxy, the CLI and the window.

use std::collections::VecDeque;
use std::sync::{Mutex, OnceLock};

type Listener = Box<dyn Fn(&str) + Send + Sync>;

pub struct LogBus {
    lines: Mutex<VecDeque<String>>,
    listeners: Mutex<Vec<Listener>>,
}

const KEEP: usize = 2000;

pub fn bus() -> &'static LogBus {
    static BUS: OnceLock<LogBus> = OnceLock::new();
    BUS.get_or_init(|| LogBus { lines: Mutex::new(VecDeque::new()), listeners: Mutex::new(Vec::new()) })
}

pub fn emit(topic: &str, message: &str) {
    let (topic, message) = (crate::i18n::t(topic), crate::i18n::t(message));
    let line = format!("{} [{topic}] {message}", chrono::Local::now().format("%H:%M:%S"));
    let b = bus();
    {
        let mut lines = b.lines.lock().unwrap_or_else(|e| e.into_inner());
        if lines.len() >= KEEP {
            lines.pop_front();
        }
        lines.push_back(line.clone());
    }
    let listeners = b.listeners.lock().unwrap_or_else(|e| e.into_inner());
    for l in listeners.iter() {
        l(&line);
    }
}

pub fn subscribe(listener: impl Fn(&str) + Send + Sync + 'static) {
    bus().listeners.lock().unwrap_or_else(|e| e.into_inner()).push(Box::new(listener));
}

pub fn history() -> Vec<String> {
    bus().lines.lock().unwrap_or_else(|e| e.into_inner()).iter().cloned().collect()
}

#[macro_export]
macro_rules! log {
    ($topic:expr, $($arg:tt)*) => { $crate::logbus::emit($topic, &format!($($arg)*)) };
}
