# Install the windowless Rust monitor on a Windows PC reachable by OpenSSH.
param(
    # 미지정 시 _ssh_config.ps1 이 해석한 $WindowsHost·$MonitorTarget 을 사용한다.
    [string]$Remote = "",
    [string]$TargetUrl = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $PSScriptRoot "_ssh_config.ps1")

if (-not $Remote) { $Remote = $WindowsHost }
if (-not $TargetUrl) { $TargetUrl = $MonitorTarget }

$binary = Join-Path $root "target\release\rpi-schedule-monitor.exe"
$tokenFile = Join-Path $root "secrets\monitor-token.txt"
$registerScript = Join-Path $PSScriptRoot "register_host_monitor.ps1"
if (-not (Test-Path -LiteralPath $tokenFile)) {
    throw "monitor token not found: $tokenFile"
}
if (-not (Test-Path -LiteralPath $binary)) {
    cargo build --release --bin rpi-schedule-monitor
    if ($LASTEXITCODE -ne 0) { throw "Rust monitor build failed (exit $LASTEXITCODE)" }
}

# 스테이징 경로를 고정 이름으로 두면 원격에 쓰기 권한이 있는 다른 계정이 scp 와 ssh 사이에
# register_host_monitor.ps1 을 바꿔치기할 수 있다(ExecutionPolicy Bypass 로 그대로 실행됨).
# 매 실행마다 예측 불가능한 이름을 쓰고, 정리에 실패하면 토큰이 남으므로 오류로 중단한다.
$stagingName = "rpi-schedule-install-" + [Convert]::ToHexString(
    [Security.Cryptography.RandomNumberGenerator]::GetBytes(8)
).ToLowerInvariant()

ssh @SshArgs $Remote "powershell -NoProfile -Command `"New-Item -ItemType Directory -Path '`$HOME\$stagingName' -Force | Out-Null`""
if ($LASTEXITCODE -ne 0) { throw "remote staging directory failed (exit $LASTEXITCODE)" }
scp @SshArgs $binary $tokenFile $registerScript "${Remote}:$stagingName/"
if ($LASTEXITCODE -ne 0) { throw "remote monitor transfer failed (exit $LASTEXITCODE)" }

$remoteCommand = "powershell -NoProfile -ExecutionPolicy Bypass -File $stagingName\register_host_monitor.ps1 -BinarySource $stagingName\rpi-schedule-monitor.exe -TokenSource $stagingName\monitor-token.txt -TargetUrl `"$TargetUrl`" -InstallDirectory rpi-schedule-monitor -DisplayOrder 1"
ssh @SshArgs $Remote $remoteCommand
$registrationExitCode = $LASTEXITCODE

ssh @SshArgs $Remote "powershell -NoProfile -Command `"Remove-Item -LiteralPath '$stagingName' -Recurse -Force`""
if ($LASTEXITCODE -ne 0) {
    throw "remote staging cleanup failed (exit $LASTEXITCODE) - $stagingName 에 모니터 토큰이 남아 있으니 직접 삭제할 것"
}
if ($registrationExitCode -ne 0) { throw "remote monitor registration failed (exit $registrationExitCode)" }
