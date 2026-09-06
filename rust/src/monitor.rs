use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::process::Command;
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail};
use chrono::{Local, SecondsFormat};
use reqwest::blocking::Client;
use reqwest::header::CONTENT_TYPE;
use serde::{Deserialize, Serialize};
#[cfg(windows)]
use sysinfo::DiskKind;
use sysinfo::{Disks, Networks, System};
use tokio::sync::RwLock;

use crate::signature;

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct GpuSample {
    pub index: i64,
    pub name: String,
    pub utilization_percent: Option<f64>,
    pub vram_percent: Option<f64>,
    pub vram_used_gb: Option<f64>,
    pub vram_total_gb: Option<f64>,
    pub temperature_c: Option<f64>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct DiskSample {
    pub name: String,
    pub mountpoint: String,
    pub usage_percent: Option<f64>,
    pub used_gb: Option<f64>,
    pub total_gb: Option<f64>,
    pub temperature_c: Option<f64>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct MonitorSample {
    pub hostname: String,
    pub captured_at: String,
    pub display_order: Option<i64>,
    #[serde(default = "default_cpu_name")]
    pub cpu_name: String,
    pub cpu_percent: Option<f64>,
    pub cpu_temperature_c: Option<f64>,
    pub memory_percent: Option<f64>,
    pub memory_used_gb: Option<f64>,
    pub memory_total_gb: Option<f64>,
    pub disk_percent: Option<f64>,
    pub disk_used_gb: Option<f64>,
    pub disk_total_gb: Option<f64>,
    pub gpu_percent: Option<f64>,
    pub gpu_memory_percent: Option<f64>,
    pub gpu_memory_used_gb: Option<f64>,
    pub gpu_memory_total_gb: Option<f64>,
    pub gpu_temperature_c: Option<f64>,
    pub network_rx_mbps: Option<f64>,
    pub network_tx_mbps: Option<f64>,
    pub uptime_seconds: Option<f64>,
    #[serde(default)]
    pub gpus: Vec<GpuSample>,
    #[serde(default)]
    pub disks: Vec<DiskSample>,
}

impl MonitorSample {
    pub fn normalize(mut self) -> Result<Self> {
        self.hostname = truncate(self.hostname.trim(), 80);
        if self.hostname.is_empty() {
            bail!("hostname is required");
        }
        self.captured_at = truncate(self.captured_at.trim(), 64);
        if self.captured_at.is_empty() {
            bail!("captured_at is required");
        }
        if self
            .display_order
            .is_some_and(|value| !(-1000..=1000).contains(&value))
        {
            bail!("display_order must be between -1000 and 1000");
        }
        self.cpu_name = truncate(self.cpu_name.trim(), 96);
        if self.cpu_name.is_empty() {
            self.cpu_name = default_cpu_name();
        }
        validate_percent("cpu_percent", self.cpu_percent)?;
        validate_percent("memory_percent", self.memory_percent)?;
        validate_percent("disk_percent", self.disk_percent)?;
        validate_percent("gpu_percent", self.gpu_percent)?;
        validate_percent("gpu_memory_percent", self.gpu_memory_percent)?;
        for (name, value) in [
            ("cpu_temperature_c", self.cpu_temperature_c),
            ("memory_used_gb", self.memory_used_gb),
            ("memory_total_gb", self.memory_total_gb),
            ("disk_used_gb", self.disk_used_gb),
            ("disk_total_gb", self.disk_total_gb),
            ("gpu_memory_used_gb", self.gpu_memory_used_gb),
            ("gpu_memory_total_gb", self.gpu_memory_total_gb),
            ("gpu_temperature_c", self.gpu_temperature_c),
            ("network_rx_mbps", self.network_rx_mbps),
            ("network_tx_mbps", self.network_tx_mbps),
            ("uptime_seconds", self.uptime_seconds),
        ] {
            validate_non_negative(name, value)?;
        }
        if self.gpus.len() > 16 {
            bail!("gpus must have at most 16 items");
        }
        if self.disks.len() > 64 {
            bail!("disks must have at most 64 items");
        }
        for (index, gpu) in self.gpus.iter_mut().enumerate() {
            gpu.name = truncate(gpu.name.trim(), 96);
            if gpu.name.is_empty() {
                gpu.name = format!("GPU {index}");
            }
            validate_percent(
                &format!("gpus[{index}].utilization_percent"),
                gpu.utilization_percent,
            )?;
            validate_percent(&format!("gpus[{index}].vram_percent"), gpu.vram_percent)?;
            validate_non_negative(&format!("gpus[{index}].vram_used_gb"), gpu.vram_used_gb)?;
            validate_non_negative(&format!("gpus[{index}].vram_total_gb"), gpu.vram_total_gb)?;
            validate_non_negative(&format!("gpus[{index}].temperature_c"), gpu.temperature_c)?;
        }
        for (index, disk) in self.disks.iter_mut().enumerate() {
            disk.name = truncate(disk.name.trim(), 64);
            if disk.name.is_empty() {
                disk.name = format!("Disk {}", index + 1);
            }
            disk.mountpoint = truncate(disk.mountpoint.trim(), 128);
            validate_percent(&format!("disks[{index}].usage_percent"), disk.usage_percent)?;
            validate_non_negative(&format!("disks[{index}].used_gb"), disk.used_gb)?;
            validate_non_negative(&format!("disks[{index}].total_gb"), disk.total_gb)?;
            validate_non_negative(&format!("disks[{index}].temperature_c"), disk.temperature_c)?;
        }
        round_sample(&mut self);
        Ok(self)
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct MonitorView {
    #[serde(flatten)]
    pub sample: MonitorSample,
    pub available: bool,
    pub stale: bool,
    pub age_seconds: f64,
}

#[derive(Clone)]
pub struct MonitorStore {
    entries: Arc<RwLock<HashMap<String, MonitorEntry>>>,
    stale_after: Duration,
}

struct MonitorEntry {
    sample: MonitorSample,
    received_at: Instant,
}

impl MonitorStore {
    pub fn new(stale_after: Duration) -> Self {
        Self {
            entries: Arc::new(RwLock::new(HashMap::new())),
            stale_after,
        }
    }

    pub async fn update(&self, sample: MonitorSample) -> Result<()> {
        let sample = sample.normalize()?;
        let key = sample.hostname.to_lowercase();
        self.entries.write().await.insert(
            key,
            MonitorEntry {
                sample,
                received_at: Instant::now(),
            },
        );
        Ok(())
    }

    pub async fn snapshots(&self) -> Vec<MonitorView> {
        let entries = self.entries.read().await;
        let mut views: Vec<_> = entries
            .values()
            .map(|entry| {
                let age = entry.received_at.elapsed();
                let stale = age > self.stale_after;
                MonitorView {
                    sample: entry.sample.clone(),
                    available: !stale,
                    stale,
                    age_seconds: round(age.as_secs_f64()),
                }
            })
            .collect();
        views.sort_by_key(|view| {
            (
                view.sample.display_order.unwrap_or(1001),
                view.sample.hostname.to_lowercase(),
            )
        });
        views
    }
}

pub struct MonitorCollector {
    system: System,
    disks: Disks,
    physical_disk_ids: Option<HashSet<String>>,
    physical_disk_ids_refreshed_at: Instant,
    networks: Networks,
    previous_received: u64,
    previous_transmitted: u64,
    previous_network_at: Instant,
    cpu_name: String,
}

impl MonitorCollector {
    pub fn new() -> Self {
        let mut system = System::new_all();
        system.refresh_cpu_usage();
        thread::sleep(sysinfo::MINIMUM_CPU_UPDATE_INTERVAL);
        system.refresh_cpu_usage();
        let cpu_name = system
            .cpus()
            .first()
            .map(|cpu| cpu.brand().trim().to_owned())
            .filter(|name| !name.is_empty())
            .unwrap_or_else(default_cpu_name);
        let disks = Disks::new_with_refreshed_list();
        let physical_disk_ids = initial_physical_disk_ids(&disks);
        let networks = Networks::new_with_refreshed_list();
        let (received, transmitted) = network_totals(&networks);
        Self {
            system,
            disks,
            physical_disk_ids,
            physical_disk_ids_refreshed_at: Instant::now(),
            networks,
            previous_received: received,
            previous_transmitted: transmitted,
            previous_network_at: Instant::now(),
            cpu_name,
        }
    }

    pub fn collect(&mut self) -> Result<MonitorSample> {
        self.system.refresh_cpu_usage();
        self.system.refresh_memory();
        self.disks.refresh(true);
        self.networks.refresh(true);

        if self.physical_disk_ids_refreshed_at.elapsed() >= Duration::from_secs(300) {
            refresh_physical_disk_ids(&self.disks, &mut self.physical_disk_ids);
            self.physical_disk_ids_refreshed_at = Instant::now();
        }

        let disk_samples = collect_disks(&self.disks, self.physical_disk_ids.as_ref());
        let gpu_samples = collect_nvidia_gpus();
        let (received, transmitted) = network_totals(&self.networks);
        let now = Instant::now();
        let elapsed = now
            .duration_since(self.previous_network_at)
            .as_secs_f64()
            .max(0.001);
        let rx_mbps =
            received.saturating_sub(self.previous_received) as f64 * 8.0 / elapsed / 1_000_000.0;
        let tx_mbps = transmitted.saturating_sub(self.previous_transmitted) as f64 * 8.0
            / elapsed
            / 1_000_000.0;
        self.previous_received = received;
        self.previous_transmitted = transmitted;
        self.previous_network_at = now;

        let memory_total = self.system.total_memory() as f64;
        let memory_used = self.system.used_memory() as f64;
        let first_disk = disk_samples.first();
        let first_gpu = gpu_samples.first();
        MonitorSample {
            hostname: hostname::get()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned(),
            captured_at: Local::now().to_rfc3339_opts(SecondsFormat::Secs, false),
            display_order: None,
            cpu_name: self.cpu_name.clone(),
            cpu_percent: Some(self.system.global_cpu_usage() as f64),
            cpu_temperature_c: None,
            memory_percent: (memory_total > 0.0).then(|| memory_used / memory_total * 100.0),
            memory_used_gb: Some(bytes_to_gib(memory_used)),
            memory_total_gb: Some(bytes_to_gib(memory_total)),
            disk_percent: first_disk.and_then(|disk| disk.usage_percent),
            disk_used_gb: first_disk.and_then(|disk| disk.used_gb),
            disk_total_gb: first_disk.and_then(|disk| disk.total_gb),
            gpu_percent: first_gpu.and_then(|gpu| gpu.utilization_percent),
            gpu_memory_percent: first_gpu.and_then(|gpu| gpu.vram_percent),
            gpu_memory_used_gb: first_gpu.and_then(|gpu| gpu.vram_used_gb),
            gpu_memory_total_gb: first_gpu.and_then(|gpu| gpu.vram_total_gb),
            gpu_temperature_c: first_gpu.and_then(|gpu| gpu.temperature_c),
            network_rx_mbps: Some(rx_mbps),
            network_tx_mbps: Some(tx_mbps),
            uptime_seconds: Some(System::uptime() as f64),
            gpus: gpu_samples,
            disks: disk_samples,
        }
        .normalize()
    }
}

impl Default for MonitorCollector {
    fn default() -> Self {
        Self::new()
    }
}

pub fn push_loop(
    target: &str,
    token_file: &Path,
    interval: Duration,
    once: bool,
    log_file: Option<&Path>,
    display_order: Option<i64>,
) -> Result<()> {
    if !target.starts_with("http://") && !target.starts_with("https://") {
        bail!("monitor target must be an HTTP URL");
    }
    if interval < Duration::from_millis(500) {
        bail!("monitor interval must be at least 500 milliseconds");
    }
    if display_order.is_some_and(|value| !(-1000..=1000).contains(&value)) {
        bail!("display_order must be between -1000 and 1000");
    }
    let token = std::fs::read_to_string(token_file)
        .with_context(|| format!("failed to read {}", token_file.display()))?
        .trim()
        .to_owned();
    if token.is_empty() {
        bail!("monitor token is empty");
    }
    let client = Client::builder()
        .timeout(Duration::from_secs(4))
        .user_agent(concat!("soma0sd-rpi-monitor/", env!("CARGO_PKG_VERSION")))
        .build()?;
    let mut collector = MonitorCollector::new();
    let mut last_error_log = UNIX_EPOCH;
    loop {
        let started = Instant::now();
        let result = collector.collect().and_then(|mut sample| {
            sample.display_order = display_order;
            // 토큰을 그대로 보내지 않고 본문에 대한 HMAC 서명만 실어 보낸다.
            let body = serde_json::to_vec(&sample).context("failed to encode monitor sample")?;
            let (timestamp, signature) = signature::sign_now(&token, &body);
            let response = client
                .post(target)
                .header(CONTENT_TYPE, "application/json")
                .header(signature::TIMESTAMP_HEADER, timestamp)
                .header(signature::SIGNATURE_HEADER, signature)
                .body(body)
                .send()
                .context("monitor push failed")?;
            if response.status().as_u16() != 204 {
                bail!("unexpected monitor response: HTTP {}", response.status());
            }
            Ok(())
        });
        if let Err(error) = result {
            let now = SystemTime::now();
            let should_log = once
                || now.duration_since(last_error_log).unwrap_or_default()
                    >= Duration::from_secs(30);
            if should_log {
                append_log(log_file, &format!("push failed: {error:#}"));
                last_error_log = now;
            }
            if once {
                return Err(error);
            }
        } else if once {
            return Ok(());
        }
        if let Some(delay) = interval.checked_sub(started.elapsed()) {
            thread::sleep(delay);
        }
    }
}

pub fn append_log(path: Option<&Path>, message: &str) {
    let Some(path) = path else {
        eprintln!("[system-monitor] {message}");
        return;
    };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    use std::io::Write;
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        let timestamp = Local::now().to_rfc3339_opts(SecondsFormat::Secs, false);
        let _ = writeln!(file, "{timestamp} {message}");
    }
}

fn collect_disks(disks: &Disks, physical_disk_ids: Option<&HashSet<String>>) -> Vec<DiskSample> {
    disks
        .list()
        .iter()
        .filter_map(|disk| {
            let total = disk.total_space();
            if total == 0 {
                return None;
            }
            let available = disk.available_space();
            let used = total.saturating_sub(available);
            let mountpoint = disk.mount_point().to_string_lossy().into_owned();
            if physical_disk_ids.is_some_and(|ids| {
                windows_root_drive_id(&mountpoint).is_none_or(|id| !ids.contains(&id))
            }) {
                return None;
            }
            let name = if cfg!(windows) {
                mountpoint.trim_end_matches(['\\', '/']).to_owned()
            } else {
                disk.name().to_string_lossy().into_owned()
            };
            Some(DiskSample {
                name,
                mountpoint,
                usage_percent: Some(used as f64 / total as f64 * 100.0),
                used_gb: Some(bytes_to_gib(used as f64)),
                total_gb: Some(bytes_to_gib(total as f64)),
                temperature_c: None,
            })
        })
        .collect()
}

fn windows_root_drive_id(value: &str) -> Option<String> {
    let value = value.trim();
    let bytes = value.as_bytes();
    if bytes.len() < 2 || !bytes[0].is_ascii_alphabetic() || bytes[1] != b':' {
        return None;
    }
    if bytes[2..]
        .iter()
        .any(|byte| *byte != b'\\' && *byte != b'/')
    {
        return None;
    }
    Some(format!("{}:", (bytes[0] as char).to_ascii_uppercase()))
}

#[cfg(any(windows, test))]
fn parse_physical_disk_ids(output: &[u8]) -> HashSet<String> {
    String::from_utf8_lossy(output)
        .lines()
        .filter_map(windows_root_drive_id)
        .collect()
}

#[cfg(windows)]
fn query_physical_disk_ids() -> Option<HashSet<String>> {
    let mut command = Command::new("powershell.exe");
    command.args([
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-Command",
        "(Get-CimInstance Win32_LogicalDiskToPartition).Dependent.DeviceID",
    ]);
    configure_hidden_process(&mut command);
    let output = command.output().ok()?;
    if !output.status.success() {
        return None;
    }
    let ids = parse_physical_disk_ids(&output.stdout);
    (!ids.is_empty()).then_some(ids)
}

#[cfg(windows)]
fn fallback_physical_disk_ids(disks: &Disks) -> HashSet<String> {
    disks
        .list()
        .iter()
        .filter(|disk| matches!(disk.kind(), DiskKind::HDD | DiskKind::SSD))
        .filter_map(|disk| windows_root_drive_id(&disk.mount_point().to_string_lossy()))
        .collect()
}

#[cfg(windows)]
fn initial_physical_disk_ids(disks: &Disks) -> Option<HashSet<String>> {
    Some(query_physical_disk_ids().unwrap_or_else(|| fallback_physical_disk_ids(disks)))
}

#[cfg(not(windows))]
fn initial_physical_disk_ids(_disks: &Disks) -> Option<HashSet<String>> {
    None
}

#[cfg(windows)]
fn refresh_physical_disk_ids(disks: &Disks, current: &mut Option<HashSet<String>>) {
    *current = Some(query_physical_disk_ids().unwrap_or_else(|| {
        current
            .clone()
            .unwrap_or_else(|| fallback_physical_disk_ids(disks))
    }));
}

#[cfg(not(windows))]
fn refresh_physical_disk_ids(_disks: &Disks, _current: &mut Option<HashSet<String>>) {}

fn collect_nvidia_gpus() -> Vec<GpuSample> {
    let mut command = Command::new("nvidia-smi");
    command.args([
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]);
    configure_hidden_process(&mut command);
    let Ok(output) = command.output() else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(parse_gpu_line)
        .collect()
}

fn parse_gpu_line(line: &str) -> Option<GpuSample> {
    let parts: Vec<_> = line.splitn(6, ',').map(str::trim).collect();
    if parts.len() != 6 {
        return None;
    }
    let used_mb = parts[3].parse::<f64>().ok()?;
    let total_mb = parts[4].parse::<f64>().ok()?;
    Some(GpuSample {
        index: parts[0].parse().ok()?,
        name: parts[1].to_owned(),
        utilization_percent: parts[2].parse().ok(),
        vram_percent: (total_mb > 0.0).then(|| used_mb / total_mb * 100.0),
        vram_used_gb: Some(used_mb / 1024.0),
        vram_total_gb: Some(total_mb / 1024.0),
        temperature_c: parts[5].parse().ok(),
    })
}

fn network_totals(networks: &Networks) -> (u64, u64) {
    networks
        .values()
        .fold((0_u64, 0_u64), |(received, transmitted), data| {
            (
                received.saturating_add(data.total_received()),
                transmitted.saturating_add(data.total_transmitted()),
            )
        })
}

fn validate_percent(name: &str, value: Option<f64>) -> Result<()> {
    if value.is_some_and(|value| !value.is_finite() || !(0.0..=100.0).contains(&value)) {
        bail!("{name} must be between 0 and 100");
    }
    Ok(())
}

fn validate_non_negative(name: &str, value: Option<f64>) -> Result<()> {
    if value.is_some_and(|value| !value.is_finite() || value < 0.0) {
        bail!("{name} must be non-negative");
    }
    Ok(())
}

fn round(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn round_option(value: &mut Option<f64>) {
    if let Some(number) = value.as_mut() {
        *number = round(*number);
    }
}

fn round_sample(sample: &mut MonitorSample) {
    for value in [
        &mut sample.cpu_percent,
        &mut sample.cpu_temperature_c,
        &mut sample.memory_percent,
        &mut sample.memory_used_gb,
        &mut sample.memory_total_gb,
        &mut sample.disk_percent,
        &mut sample.disk_used_gb,
        &mut sample.disk_total_gb,
        &mut sample.gpu_percent,
        &mut sample.gpu_memory_percent,
        &mut sample.gpu_memory_used_gb,
        &mut sample.gpu_memory_total_gb,
        &mut sample.gpu_temperature_c,
        &mut sample.network_rx_mbps,
        &mut sample.network_tx_mbps,
        &mut sample.uptime_seconds,
    ] {
        round_option(value);
    }
    for gpu in &mut sample.gpus {
        for value in [
            &mut gpu.utilization_percent,
            &mut gpu.vram_percent,
            &mut gpu.vram_used_gb,
            &mut gpu.vram_total_gb,
            &mut gpu.temperature_c,
        ] {
            round_option(value);
        }
    }
    for disk in &mut sample.disks {
        for value in [
            &mut disk.usage_percent,
            &mut disk.used_gb,
            &mut disk.total_gb,
            &mut disk.temperature_c,
        ] {
            round_option(value);
        }
    }
}

fn bytes_to_gib(bytes: f64) -> f64 {
    bytes / 1024.0 / 1024.0 / 1024.0
}

fn truncate(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

fn default_cpu_name() -> String {
    "CPU".to_owned()
}

#[cfg(windows)]
fn configure_hidden_process(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn configure_hidden_process(_command: &mut Command) {}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(hostname: &str, cpu: f64) -> MonitorSample {
        MonitorSample {
            hostname: hostname.to_owned(),
            captured_at: "2026-09-04T12:00:00+09:00".to_owned(),
            cpu_percent: Some(cpu),
            ..MonitorSample::default()
        }
    }

    #[test]
    fn normalization_rejects_invalid_percentage() {
        let error = sample("host", 101.0).normalize().unwrap_err();
        assert!(error.to_string().contains("cpu_percent"));
    }

    #[tokio::test]
    async fn store_keeps_hosts_and_replaces_case_insensitively() {
        let store = MonitorStore::new(Duration::from_secs(12));
        store.update(sample("zeta", 10.0)).await.unwrap();
        store.update(sample("Alpha", 20.0)).await.unwrap();
        store.update(sample("ALPHA", 30.0)).await.unwrap();
        let views = store.snapshots().await;
        assert_eq!(views.len(), 2);
        assert_eq!(views[0].sample.hostname, "ALPHA");
        assert_eq!(views[0].sample.cpu_percent, Some(30.0));
        assert_eq!(views[1].sample.hostname, "zeta");
    }

    #[tokio::test]
    async fn store_orders_hosts_by_display_order() {
        let store = MonitorStore::new(Duration::from_secs(12));
        let mut second = sample("host-b", 10.0);
        second.display_order = Some(1);
        let mut first = sample("host-a", 20.0);
        first.display_order = Some(0);
        store.update(second).await.unwrap();
        store.update(first).await.unwrap();

        let views = store.snapshots().await;
        assert_eq!(views[0].sample.hostname, "host-a");
        assert_eq!(views[1].sample.hostname, "host-b");
    }

    #[test]
    fn parses_nvidia_csv_line() {
        let gpu = parse_gpu_line("0, NVIDIA RTX, 25, 4096, 16384, 50").unwrap();
        assert_eq!(gpu.index, 0);
        assert_eq!(gpu.vram_percent, Some(25.0));
    }

    #[test]
    fn parses_only_rooted_physical_drive_ids() {
        let ids = parse_physical_disk_ids(b"c:\r\nD:\r\ninvalid\r\n");
        assert_eq!(ids, HashSet::from(["C:".to_owned(), "D:".to_owned()]));
        assert_eq!(windows_root_drive_id("E:\\"), Some("E:".to_owned()));
        assert_eq!(windows_root_drive_id("D:\\GoogleDrive\\"), None);
        assert_eq!(windows_root_drive_id("/"), None);
    }
}
