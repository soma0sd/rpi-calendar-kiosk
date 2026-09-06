# Build and install the windowless Rust monitor on this Windows data host.
param(
    # 미지정 시 _ssh_config.ps1 이 해석한 $MonitorTarget 을 사용한다.
    [string]$TargetUrl = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $PSScriptRoot "_ssh_config.ps1")

if (-not $TargetUrl) { $TargetUrl = $MonitorTarget }

$remote = $RpiHost
$tokenDir = Join-Path $root "secrets"
$tokenFile = Join-Path $tokenDir "monitor-token.txt"
$binary = Join-Path $root "target\release\rpi-schedule-monitor.exe"

New-Item -ItemType Directory -Path $tokenDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $tokenFile)) {
    $token = [Convert]::ToHexString(
        [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
    ).ToLowerInvariant()
    Set-Content -LiteralPath $tokenFile -Value $token -Encoding ascii -NoNewline
}

cargo build --release --bin rpi-schedule-monitor
if ($LASTEXITCODE -ne 0) { throw "Rust monitor build failed (exit $LASTEXITCODE)" }

ssh @SshArgs $remote "mkdir -p ~/.config/soma0sd_rpi_schedule"
if ($LASTEXITCODE -ne 0) { throw "remote monitor config directory failed (exit $LASTEXITCODE)" }
scp @SshArgs $tokenFile "${remote}:~/.config/soma0sd_rpi_schedule/monitor-token"
if ($LASTEXITCODE -ne 0) { throw "monitor token transfer failed (exit $LASTEXITCODE)" }
ssh @SshArgs $remote "chmod 600 ~/.config/soma0sd_rpi_schedule/monitor-token"
if ($LASTEXITCODE -ne 0) { throw "remote monitor token permission failed (exit $LASTEXITCODE)" }

# 설치 위치는 저장소 트리 밖(기본값 $env:USERPROFILE\rpi-schedule-monitor)을 쓴다.
# 작업 트리 안에 평문 토큰을 두면 유출 방지가 .gitignore 한 줄에만 걸린다.
& (Join-Path $PSScriptRoot "register_host_monitor.ps1") `
    -BinarySource $binary `
    -TokenSource $tokenFile `
    -TargetUrl $TargetUrl
