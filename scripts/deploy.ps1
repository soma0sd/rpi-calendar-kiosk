# Copy the Rust runtime sources to the Raspberry Pi and build a locked release binary.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $PSScriptRoot "_ssh_config.ps1")

$remote = $RpiHost
$remoteDir = "~/Project/soma0sd_RPi_Schedule"

Write-Host "Local : $root"
Write-Host "Target: ${remote}:$remoteDir/"

ssh @SshArgs $remote "mkdir -p $remoteDir"
if ($LASTEXITCODE -ne 0) { throw "remote directory preparation failed (exit $LASTEXITCODE)" }

$directories = @("rust", "web", "scripts")
$files = @("Cargo.toml", "Cargo.lock", "README.md", ".gitignore")
scp @SshArgs -r -p @directories @files "${remote}:$remoteDir/"
if ($LASTEXITCODE -ne 0) { throw "source transfer failed (exit $LASTEXITCODE)" }

ssh @SshArgs $remote "cd $remoteDir && bash scripts/bootstrap_rust.sh"
if ($LASTEXITCODE -ne 0) { throw "Rust toolchain bootstrap failed (exit $LASTEXITCODE)" }

ssh @SshArgs $remote 'cd ~/Project/soma0sd_RPi_Schedule && "$HOME/.cargo/bin/cargo" build --locked --release --bin rpi-schedule-kiosk'
if ($LASTEXITCODE -ne 0) { throw "remote Rust release build failed (exit $LASTEXITCODE)" }

ssh @SshArgs $remote 'cd ~/Project/soma0sd_RPi_Schedule && install -d "$HOME/.local/bin" && install -m 755 target/release/rpi-schedule-kiosk "$HOME/.local/bin/rpi-schedule-kiosk.next" && mv -f "$HOME/.local/bin/rpi-schedule-kiosk.next" "$HOME/.local/bin/rpi-schedule-kiosk"'
if ($LASTEXITCODE -ne 0) { throw "remote Rust binary install failed (exit $LASTEXITCODE)" }

ssh @SshArgs $remote 'sha256sum "$HOME/.local/bin/rpi-schedule-kiosk" && "$HOME/.local/bin/rpi-schedule-kiosk" --version'
if ($LASTEXITCODE -ne 0) { throw "remote Rust binary verification failed (exit $LASTEXITCODE)" }

Write-Host "Rust deployment build complete"
