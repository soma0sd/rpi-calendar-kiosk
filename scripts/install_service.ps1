# systemd user service 등록 + labwc autostart 설정.
# 사전: deploy.ps1 와 push_token.ps1 이 먼저 끝나 있어야 한다.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $PSScriptRoot "_ssh_config.ps1")

$remote = $RpiHost

ssh @SshArgs $remote "mkdir -p ~/.config/systemd/user ~/.config/labwc"
if ($LASTEXITCODE -ne 0) { throw "원격 디렉터리 준비 실패 (exit $LASTEXITCODE)" }

# 스테이징 경로를 /tmp 고정 이름으로 두면 같은 기기의 다른 로컬 계정이 심볼릭 링크나
# 조작된 유닛 파일로 선점해 임의 ExecStart 를 사용자 systemd 에 심을 수 있다.
# mktemp 로 소유자 전용(0700) 임시 디렉터리를 홈 아래에 만들어 쓴다.
$stagingDirectory = (ssh @SshArgs $remote 'mkdir -p "$HOME/.cache" && mktemp -d "$HOME/.cache/rpi-calendar-kiosk-install.XXXXXXXX"' | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) { throw "원격 임시 디렉터리 생성 실패 (exit $LASTEXITCODE)" }
$stagingDirectory = "$stagingDirectory".Trim()
if (-not $stagingDirectory) { throw "원격 임시 디렉터리 경로를 받지 못했습니다" }

# 설치 중 실패에만 사용하는 임시 복구본. 성공 시 스테이징 디렉터리째 제거.
$temporaryUnit = "$stagingDirectory/rpi-calendar-kiosk.service.previous"
$remoteSetupScript = "$stagingDirectory/setup_service.sh"
ssh @SshArgs $remote "if [ -f ~/.config/systemd/user/rpi-calendar-kiosk.service ]; then cp -p ~/.config/systemd/user/rpi-calendar-kiosk.service '$temporaryUnit'; fi"
if ($LASTEXITCODE -ne 0) { throw "임시 서비스 복구본 생성 실패 (exit $LASTEXITCODE)" }

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

scp @SshArgs "$root\scripts\setup_service.sh" "${remote}:$remoteSetupScript"
if ($LASTEXITCODE -ne 0) { throw "setup 스크립트 scp 실패 (exit $LASTEXITCODE)" }

$restorePrevious = "if [ -f '$temporaryUnit' ]; then cp '$temporaryUnit' ~/.config/systemd/user/rpi-calendar-kiosk.service && systemctl --user daemon-reload && systemctl --user restart rpi-calendar-kiosk.service; fi; rm -rf '$stagingDirectory'"

ssh @SshArgs $remote "bash '$remoteSetupScript'"
if ($LASTEXITCODE -ne 0) {
    ssh @SshArgs $remote $restorePrevious
    throw "setup_service.sh 실행 실패, 직전 서비스 정의 복구 수행 (exit $LASTEXITCODE)"
}

Start-Sleep -Seconds 3
ssh @SshArgs $remote "systemctl --user is-active rpi-calendar-kiosk.service && curl -fsS http://127.0.0.1:8765/healthz"
if ($LASTEXITCODE -ne 0) {
    ssh @SshArgs $remote $restorePrevious
    throw "Rust 서비스 검증 실패, 직전 서비스 정의 복구 수행"
}

ssh @SshArgs $remote "rm -rf '$stagingDirectory'"
if ($LASTEXITCODE -ne 0) { throw "원격 임시 디렉터리 정리 실패 (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "다음 단계 (선택)"
Write-Host "  - 즉시 시작: ssh ${remote} 'systemctl --user start rpi-calendar-kiosk.service'"
Write-Host "  - 부팅 시 사용자 세션 없어도 user 유닛 유지: ssh ${remote} 'sudo loginctl enable-linger `$(whoami)'"
Write-Host "  - 재부팅 후 자동 시작 확인: ssh -t ${remote} 'sudo reboot'"
