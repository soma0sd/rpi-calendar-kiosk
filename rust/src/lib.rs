pub mod calendar;
pub mod config;
pub mod google;
pub mod monitor;
pub mod server;
pub mod signature;

pub const APP_NAME: &str = "soma0sd-rpi-schedule";
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");
