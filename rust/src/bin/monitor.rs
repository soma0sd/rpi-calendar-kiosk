#![cfg_attr(windows, windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

use clap::Parser;
use soma0sd_rpi_schedule_rust::monitor::{append_log, push_loop};

#[derive(Debug, Parser)]
#[command(
    version,
    about = "Push Windows system metrics to the Raspberry Pi kiosk"
)]
struct Args {
    #[arg(long)]
    target: String,
    #[arg(long)]
    token_file: PathBuf,
    #[arg(long, default_value_t = 2.0)]
    interval_seconds: f64,
    #[arg(long)]
    once: bool,
    #[arg(long)]
    log_file: Option<PathBuf>,
    #[arg(long)]
    display_order: Option<i64>,
}

fn main() -> ExitCode {
    let args = Args::parse();
    let interval = Duration::from_secs_f64(args.interval_seconds.max(0.0));
    match push_loop(
        &args.target,
        &args.token_file,
        interval,
        args.once,
        args.log_file.as_deref(),
        args.display_order,
    ) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            append_log(args.log_file.as_deref(), &format!("fatal error: {error:#}"));
            ExitCode::from(1)
        }
    }
}
