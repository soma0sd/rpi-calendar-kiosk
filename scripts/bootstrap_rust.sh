#!/usr/bin/env bash
# RPi 에 Rust 툴체인을 준비한다.
# 설치 스크립트를 파이프로 바로 실행(curl | sh)하지 않는다. 배포 서버가 침해되거나
# 다운로드가 중간에 끊기면 검증 없이 임의 명령이 실행되고, 이 사용자 계정은
# ~/.config/soma0sd_rpi_schedule/token.json (구글 OAuth refresh token) 을 읽을 수 있다.
# 따라서 rustup-init 을 버전 고정으로 내려받아 SHA-256 을 대조한 뒤에만 실행한다.
set -euo pipefail

RUSTUP_VERSION="1.29.1"
RUSTUP_SHA256_AARCH64="15f6e4ce9f583b929c996c91562bad6d4454f3281de858b02cdfdef615fac433"
RUSTUP_SHA256_X86_64="dda7234360b7f578ca8b0ddcb80145646fa61a67c1720a5abc7051b35c9fcb71"

if [ -x "$HOME/.cargo/bin/cargo" ]; then
    "$HOME/.cargo/bin/cargo" --version
    exit 0
fi

if command -v cargo >/dev/null 2>&1; then
    cargo --version
    exit 0
fi

architecture="$(uname -m)"
case "$architecture" in
    aarch64|arm64)
        rustup_target="aarch64-unknown-linux-gnu"
        rustup_sha256="$RUSTUP_SHA256_AARCH64"
        ;;
    x86_64|amd64)
        rustup_target="x86_64-unknown-linux-gnu"
        rustup_sha256="$RUSTUP_SHA256_X86_64"
        ;;
    *)
        echo "지원하지 않는 아키텍처: $architecture" >&2
        echo "rustup 을 수동 설치한 뒤 다시 실행한다." >&2
        exit 1
        ;;
esac

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
installer="$workdir/rustup-init"

curl --proto '=https' --tlsv1.2 -sSfL --retry 3 -o "$installer" \
    "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${rustup_target}/rustup-init"

actual_sha256="$(sha256sum "$installer" | cut -d ' ' -f 1)"
if [ "$actual_sha256" != "$rustup_sha256" ]; then
    echo "rustup-init 체크섬 불일치. 설치를 중단한다." >&2
    echo "  기대: $rustup_sha256" >&2
    echo "  실제: $actual_sha256" >&2
    exit 1
fi

chmod +x "$installer"
"$installer" -y --profile minimal
"$HOME/.cargo/bin/cargo" --version
