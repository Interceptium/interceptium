//! The product's name and look. Interceptium is the default; building with
//! `--features hido` makes the Hidoceptium edition (its own name, install, settings and art).

#[cfg(not(feature = "hido"))]
#[macro_export]
macro_rules! brand_name {
    () => {
        "Interceptium"
    };
}

#[cfg(feature = "hido")]
#[macro_export]
macro_rules! brand_name {
    () => {
        "Hidoceptium"
    };
}

#[cfg(not(feature = "hido"))]
#[macro_export]
macro_rules! brand_lower {
    () => {
        "interceptium"
    };
}

#[cfg(feature = "hido")]
#[macro_export]
macro_rules! brand_lower {
    () => {
        "hidoceptium"
    };
}

pub const HIDO: bool = cfg!(feature = "hido");

/// The edition's own version number (Hidoceptium is numbered separately).
pub const VERSION: &str = if HIDO { "1.0.0" } else { env!("CARGO_PKG_VERSION") };
