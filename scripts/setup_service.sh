#!/bin/bash
# install_service.ps1 가 RPi에 scp 한 뒤 실행하는 부트스트랩.
# - 구버전(soma0sd-*) 유닛/autostart 마이그레이션 (rpi-calendar-kiosk 로 개명)
# - systemd user unit 등록 + enable
# - labwc autostart 에 graphical-session.target 활성화 + 우리 서비스 시작 라인 추가 (idempotent)
set -euo pipefail

mkdir -p "$HOME/.config/systemd/user" "$HOME/.config/labwc"

# --- 구버전(soma0sd-*) 유닛 마이그레이션: 비활성화 + 제거 ---
for u in soma0sd-rpi-schedule.service soma0sd-rpi-screen-off.timer soma0sd-rpi-screen-on.timer; do
    systemctl --user disable --now "$u" 2>/dev/null || true
done
rm -f "$HOME/.config/systemd/user/soma0sd-rpi-schedule.service" \
      "$HOME/.config/systemd/user/soma0sd-rpi-screen-off.service" \
      "$HOME/.config/systemd/user/soma0sd-rpi-screen-off.timer" \
      "$HOME/.config/systemd/user/soma0sd-rpi-screen-on.service" \
      "$HOME/.config/systemd/user/soma0sd-rpi-screen-on.timer"

AUTOSTART="$HOME/.config/labwc/autostart"
MARKER="# rpi-calendar-kiosk 키오스크 자동시작"

# 구버전 autostart 마커/서비스명을 신버전으로 in-place 치환 (있으면).
if [ -f "$AUTOSTART" ]; then
    sed -i 's/# soma0sd_RPi_Schedule 키오스크 자동시작/# rpi-calendar-kiosk 키오스크 자동시작/' "$AUTOSTART"
    sed -i 's/soma0sd-rpi-schedule\.service/rpi-calendar-kiosk.service/g' "$AUTOSTART"
fi

if [ ! -f "$AUTOSTART" ]; then
    cat > "$AUTOSTART" <<'EOF'
#!/bin/bash
EOF
    chmod +x "$AUTOSTART"
fi

if ! grep -F -q "$MARKER" "$AUTOSTART"; then
    cat >> "$AUTOSTART" <<'EOF'

# rpi-calendar-kiosk 키오스크 자동시작
systemctl --user import-environment WAYLAND_DISPLAY XDG_RUNTIME_DIR XDG_SESSION_TYPE PATH
# 부팅 직후 화면 강제 ON (밤사이 off 상태에서 부팅된 경우 안전망)
/usr/bin/wlr-randr --output HDMI-A-1 --on 2>/dev/null || true
systemctl --user start graphical-session.target 2>/dev/null || true
systemctl --user restart rpi-calendar-kiosk.service 2>/dev/null || true
EOF
    echo "labwc autostart 갱신됨 ($AUTOSTART)"
else
    echo "labwc autostart 이미 등록됨: 건너뜀"
    # 기존 마커 블록에 wlr-randr 부팅 안전망 라인이 없으면 import-environment 다음 줄에 삽입.
    if ! grep -F -q "/usr/bin/wlr-randr --output HDMI-A-1 --on" "$AUTOSTART"; then
        awk '
            /systemctl --user import-environment/ && !done {
                print
                print "# 부팅 직후 화면 강제 ON (밤사이 off 상태에서 부팅된 경우 안전망)"
                print "/usr/bin/wlr-randr --output HDMI-A-1 --on 2>/dev/null || true"
                done = 1
                next
            }
            { print }
        ' "$AUTOSTART" > "$AUTOSTART.tmp" && mv "$AUTOSTART.tmp" "$AUTOSTART"
        chmod +x "$AUTOSTART"
        echo "labwc autostart 에 wlr-randr 부팅 안전망 추가됨"
    fi
fi

systemctl --user daemon-reload
systemctl --user enable rpi-calendar-kiosk.service
systemctl --user restart rpi-calendar-kiosk.service
# 번인 완화: 23:00 화면 off, 07:00 on. enable --now 로 timer 즉시 활성화.
systemctl --user enable --now rpi-calendar-kiosk-screen-off.timer
systemctl --user enable --now rpi-calendar-kiosk-screen-on.timer

echo "---"
echo "systemd user unit 활성화 완료."
echo "다음 labwc 재시작(재부팅 또는 로그아웃/로그인) 시 자동 실행."
echo "지금 즉시 띄우려면: systemctl --user start rpi-calendar-kiosk.service"
echo "상태 확인: systemctl --user status rpi-calendar-kiosk.service"
echo "로그: journalctl --user -u rpi-calendar-kiosk.service -f"
echo "번인 타이머 확인: systemctl --user list-timers 'rpi-calendar-kiosk-screen-*.timer'"
