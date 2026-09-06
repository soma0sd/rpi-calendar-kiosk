use std::env;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

pub const POLL_INTERVAL_SECONDS: u64 = 300;
pub const UPCOMING_DAYS: i64 = 90;
pub const HOLIDAY_CALENDAR_ID: &str = "ko.south_korea#holiday@group.v.calendar.google.com";
pub const TASK_LIST_ID: &str = "@default";

#[derive(Clone, Debug)]
pub struct RuntimePaths {
    pub token: PathBuf,
    pub monitor_token: PathBuf,
    pub cache: PathBuf,
}

impl RuntimePaths {
    pub fn discover() -> Result<Self> {
        let config_root = config_root()?;
        let cache_root = cache_root()?;
        Ok(Self {
            token: config_root.join("token.json"),
            monitor_token: config_root.join("monitor-token"),
            cache: cache_root.join("events.json"),
        })
    }
}

pub fn read_trimmed(path: &Path) -> Result<String> {
    std::fs::read_to_string(path)
        .with_context(|| format!("failed to read {}", path.display()))
        .map(|value| value.trim().to_owned())
}

fn config_root() -> Result<PathBuf> {
    if let Some(path) = env::var_os("XDG_CONFIG_HOME") {
        return Ok(PathBuf::from(path).join("soma0sd_rpi_schedule"));
    }
    home_dir().map(|path| path.join(".config").join("soma0sd_rpi_schedule"))
}

fn cache_root() -> Result<PathBuf> {
    if let Some(path) = env::var_os("XDG_CACHE_HOME") {
        return Ok(PathBuf::from(path).join("soma0sd_rpi_schedule"));
    }
    home_dir().map(|path| path.join(".cache").join("soma0sd_rpi_schedule"))
}

fn home_dir() -> Result<PathBuf> {
    env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .context("HOME or USERPROFILE is not set")
}
