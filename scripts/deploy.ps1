# Windows OpenSSH의 scp로 RPi에 동기화하고 원격 uv sync까지 실행.
# secrets/credentials.json, secrets/token.json은 의도적으로 전송 목록에 포함하지 않는다.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $PSScriptRoot "_ssh_config.ps1")

$remote = "1.66-RPi4-Display"
$remoteDir = "~/Project/soma0sd_RPi_Schedule"

Write-Host "Local : $root"
Write-Host "Target: ${remote}:$remoteDir/"

# 동기화 대상 디렉토리는 매번 새로 (rsync --delete 모방).
ssh @SshArgs $remote "mkdir -p $remoteDir && rm -rf $remoteDir/src $remoteDir/tests $remoteDir/scripts"
if ($LASTEXITCODE -ne 0) { throw "원격 디렉토리 준비 실패 (exit $LASTEXITCODE)" }

$dirs = @("src", "tests", "scripts")
$files = @("pyproject.toml", "uv.lock", "README.md", ".gitignore", ".python-version")

scp @SshArgs -r -p @dirs @files "${remote}:$remoteDir/"
if ($LASTEXITCODE -ne 0) { throw "scp 전송 실패 (exit $LASTEXITCODE)" }

# RPi에 uv가 없으면 설치 후 sync.
ssh @SshArgs $remote 'export PATH=$HOME/.local/bin:$PATH; (command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh) && cd ~/Project/soma0sd_RPi_Schedule && $HOME/.local/bin/uv sync'
if ($LASTEXITCODE -ne 0) { throw "원격 uv sync 실패 (exit $LASTEXITCODE)" }

Write-Host "배포 완료"
