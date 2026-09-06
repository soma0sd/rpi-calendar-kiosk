# 배포 대상 해석 헬퍼. dot-source 로 호출자 스코프에 $SshArgs·$RpiHost·$WindowsHost·$MonitorTarget 을 노출.
# 우선순위: 환경 변수  →  로컬 비추적 설정(.claude/deploy-local.ps1)  →  공개 기본값(플레이스홀더).
# Why: 실제 호스트 별칭과 사설 IP 는 공개 저장소에 남기지 않는다. 각자 환경에 맞는 값은
#      아래 환경 변수로 지정하거나 .claude/deploy-local.ps1 (git 비추적) 에 적어 둔다.
#        $env:SOMA0SD_SSH_CONFIG     별칭이 정의된 ssh config 경로 (표준 ~/.ssh/config 면 불필요)
#        $env:SOMA0SD_RPI_HOST       라즈베리 파이 ssh 별칭 또는 user@host
#        $env:SOMA0SD_WINDOWS_HOST   지표 송신기를 설치할 Windows PC 의 ssh 별칭
#        $env:SOMA0SD_MONITOR_TARGET 지표 수신 URL (http://<rpi>:8765/api/system)

$SshArgs = @()

# 1) 로컬 비추적 설정을 먼저 반영한다 (.claude/ 는 .gitignore 대상).
$localOverride = Join-Path (Split-Path -Parent $PSScriptRoot) ".claude/deploy-local.ps1"
if (Test-Path -LiteralPath $localOverride) { . $localOverride }

# 2) 환경 변수가 있으면 최종 우선한다.
if ($env:SOMA0SD_SSH_CONFIG) { $SshArgs = @("-F", $env:SOMA0SD_SSH_CONFIG) }
if ($env:SOMA0SD_RPI_HOST) { $RpiHost = $env:SOMA0SD_RPI_HOST }
if ($env:SOMA0SD_WINDOWS_HOST) { $WindowsHost = $env:SOMA0SD_WINDOWS_HOST }
if ($env:SOMA0SD_MONITOR_TARGET) { $MonitorTarget = $env:SOMA0SD_MONITOR_TARGET }

# 3) 남은 값은 공개 기본값으로 채운다.
if (-not $RpiHost) { $RpiHost = "rpi-calendar-kiosk" }
if (-not $WindowsHost) { $WindowsHost = "windows-monitor-host" }
if (-not $MonitorTarget) { $MonitorTarget = "http://${RpiHost}:8765/api/system" }
