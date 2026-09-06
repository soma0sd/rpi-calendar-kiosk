use std::env;
use std::net::{IpAddr, SocketAddr};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use std::time::Duration;

use anyhow::{Context, Result, anyhow};
use clap::Parser;
use serde_json::json;
use soma0sd_rpi_schedule_rust::calendar::CalendarState;
use soma0sd_rpi_schedule_rust::config::{RuntimePaths, read_trimmed};
use soma0sd_rpi_schedule_rust::google::run_sync_loop;
use soma0sd_rpi_schedule_rust::server::{AppState, serve};

#[derive(Debug, Parser)]
#[command(version, about = "Raspberry Pi calendar kiosk Rust runtime")]
struct Args {
    #[arg(long, default_value = "127.0.0.1")]
    host: IpAddr,
    #[arg(long, default_value_t = 8765)]
    port: u16,
    #[arg(long)]
    serve_only: bool,
    #[arg(long)]
    token_file: Option<PathBuf>,
    #[arg(long)]
    monitor_token_file: Option<PathBuf>,
    #[arg(long)]
    cache_file: Option<PathBuf>,
}

#[tokio::main]
async fn main() -> ExitCode {
    match run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("[rpi-schedule] fatal: {error:#}");
            ExitCode::from(1)
        }
    }
}

async fn run() -> Result<()> {
    let args = Args::parse();
    let discovered = RuntimePaths::discover()?;
    let token_path = args.token_file.unwrap_or(discovered.token);
    let monitor_token_path = args.monitor_token_file.unwrap_or(discovered.monitor_token);
    let cache_path = args.cache_file.unwrap_or(discovered.cache);
    let monitor_token = match read_trimmed(&monitor_token_path) {
        Ok(token) => token,
        Err(error) => {
            eprintln!("[rpi-schedule] monitor push disabled: {error:#}");
            String::new()
        }
    };
    let calendar = CalendarState::load(cache_path);
    let state = AppState::new(calendar.clone(), monitor_token);
    tokio::spawn(run_sync_loop(calendar, token_path));

    let address = SocketAddr::new(args.host, args.port);
    if args.serve_only {
        return serve(address, state).await;
    }

    let server_task = tokio::spawn(serve(address, state));
    tokio::time::sleep(Duration::from_millis(600)).await;
    let browser_host = if args.host.is_unspecified() {
        "127.0.0.1".to_owned()
    } else {
        args.host.to_string()
    };
    let url = format!("http://{browser_host}:{}", args.port);
    let browser_task = tokio::task::spawn_blocking(move || run_browser(&url));

    tokio::select! {
        server = server_task => {
            server.context("server task failed")??;
            Ok(())
        }
        browser = browser_task => {
            browser.context("browser task failed")??;
            Err(anyhow!("chromium exited"))
        }
    }
}

fn run_browser(url: &str) -> Result<()> {
    let chromium = find_command(&["chromium", "chromium-browser", "google-chrome"])
        .context("chromium executable not found")?;
    let profile = ensure_kiosk_profile()?;
    println!("[rpi-schedule] chromium kiosk starting: {url}");
    let status = Command::new(chromium)
        .args([
            format!("--user-data-dir={}", profile.display()),
            "--kiosk".to_owned(),
            "--noerrdialogs".to_owned(),
            "--disable-infobars".to_owned(),
            "--disable-translate".to_owned(),
            "--no-first-run".to_owned(),
            "--no-default-browser-check".to_owned(),
            "--disable-pinch".to_owned(),
            "--disable-session-crashed-bubble".to_owned(),
            "--check-for-update-interval=31536000".to_owned(),
            "--autoplay-policy=no-user-gesture-required".to_owned(),
            "--enable-features=UseOzonePlatform".to_owned(),
            "--ozone-platform=wayland".to_owned(),
            "--password-store=basic".to_owned(),
            "--lang=ko-KR".to_owned(),
            url.to_owned(),
        ])
        .status()
        .context("failed to start chromium")?;
    if status.success() {
        Ok(())
    } else {
        Err(anyhow!("chromium exited with {status}"))
    }
}

fn ensure_kiosk_profile() -> Result<PathBuf> {
    let home = env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .context("HOME or USERPROFILE is not set")?;
    let root = home.join(".config").join("chromium-soma0sd-kiosk");
    let default_dir = root.join("Default");
    std::fs::create_dir_all(&default_dir)?;
    let preferences = json!({
        "translate": {"enabled": false},
        "translate_blocked_languages": ["ko", "en", "ja", "zh-CN"],
        "translate_site_blocklist_with_time": {},
        "translate_accepted_count": {},
        "translate_denied_count": {},
        "browser": {
            "check_default_browser": false,
            "has_seen_welcome_page": true,
            "show_home_button": false
        },
        "profile": {
            "default_content_setting_values": {"notifications": 2},
            "exit_type": "Normal",
            "exited_cleanly": true
        },
        "session": {"restore_on_startup": 4}
    });
    std::fs::write(
        default_dir.join("Preferences"),
        serde_json::to_vec(&preferences)?,
    )?;
    let policies = root.join("Policies").join("Managed");
    std::fs::create_dir_all(&policies)?;
    let policy = json!({
        "TranslateEnabled": false,
        "DefaultBrowserSettingEnabled": false,
        "MetricsReportingEnabled": false,
        "PasswordManagerEnabled": false,
        "BrowserSignin": 0,
        "PromotionalTabsEnabled": false
    });
    std::fs::write(policies.join("kiosk.json"), serde_json::to_vec(&policy)?)?;
    Ok(root)
}

fn find_command(names: &[&str]) -> Option<PathBuf> {
    let path = env::var_os("PATH")?;
    for directory in env::split_paths(&path) {
        for name in names {
            let candidate = directory.join(name);
            if is_executable_file(&candidate) {
                return Some(candidate);
            }
        }
    }
    None
}

fn is_executable_file(path: &Path) -> bool {
    path.metadata().is_ok_and(|metadata| metadata.is_file())
}
