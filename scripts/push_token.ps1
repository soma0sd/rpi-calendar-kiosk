# 로컬에서 OAuth 인증 후 생성된 secrets/token.json을 RPi의 XDG config로 복사.
# credentials.json은 절대 전송하지 않는다 (RPi에서 인증을 수행할 일이 없음).
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $PSScriptRoot "_ssh_config.ps1")

$remote = $RpiHost
$localToken = Join-Path $root "secrets\token.json"
$remoteDir = "~/.config/soma0sd_rpi_schedule"
$remoteToken = "$remoteDir/token.json"

if (-not (Test-Path $localToken)) {
    throw "로컬 token.json이 없다: $localToken. 먼저 'uv run python -m soma0sd_rpi_schedule --auth'를 실행해라."
}

ssh @SshArgs $remote "mkdir -p $remoteDir && chmod 700 $remoteDir"
if ($LASTEXITCODE -ne 0) { throw "원격 디렉터리 준비 실패 (exit $LASTEXITCODE)" }

scp @SshArgs $localToken "${remote}:$remoteToken"
if ($LASTEXITCODE -ne 0) { throw "scp 전송 실패 (exit $LASTEXITCODE)" }

ssh @SshArgs $remote "chmod 600 $remoteToken"
if ($LASTEXITCODE -ne 0) { throw "원격 권한 설정 실패 (exit $LASTEXITCODE)" }

Write-Host "토큰 배포 완료: ${remote}:$remoteToken"
