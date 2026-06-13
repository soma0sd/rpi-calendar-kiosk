# systemd user service 등록 + labwc autostart 설정.
# 사전: deploy.ps1 와 push_token.ps1 이 먼저 끝나 있어야 한다.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $PSScriptRoot "_ssh_config.ps1")

$remote = "1.66-RPi4-Display"

ssh @SshArgs $remote "mkdir -p ~/.config/systemd/user ~/.config/labwc"
if ($LASTEXITCODE -ne 0) { throw "원격 디렉터리 준비 실패 (exit $LASTEXITCODE)" }

$units = @(
    "rpi-calendar-kiosk.service",
    "rpi-calendar-kiosk-screen-off.service",
    "rpi-calendar-kiosk-screen-off.timer",
    "rpi-calendar-kiosk-screen-on.service",
    "rpi-calendar-kiosk-screen-on.timer"
)
foreach ($u in $units) {
    scp @SshArgs "$root\scripts\$u" "${remote}:~/.config/systemd/user/$u"
    if ($LASTEXITCODE -ne 0) { throw "$u scp 실패 (exit $LASTEXITCODE)" }
}

scp @SshArgs "$root\scripts\setup_service.sh" "${remote}:/tmp/setup_service.sh"
if ($LASTEXITCODE -ne 0) { throw "setup 스크립트 scp 실패 (exit $LASTEXITCODE)" }

ssh @SshArgs $remote "bash /tmp/setup_service.sh && rm -f /tmp/setup_service.sh"
if ($LASTEXITCODE -ne 0) { throw "setup_service.sh 실행 실패 (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "다음 단계 (선택)"
Write-Host "  - 즉시 시작: ssh ${remote} 'systemctl --user start rpi-calendar-kiosk.service'"
Write-Host "  - 부팅 시 사용자 세션 없어도 user 유닛 유지: ssh ${remote} 'sudo loginctl enable-linger `$(whoami)'"
Write-Host "  - 재부팅 후 자동 시작 확인: ssh -t ${remote} 'sudo reboot'"
