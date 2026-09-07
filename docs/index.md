# rpi-calendar-kiosk 사용 설명서

라즈베리파이 4와 Waveshare HDMI 패널로 만드는 탁상용 일정 키오스크입니다. 구글 캘린더의 일정과 할 일을
D-Day 목록과 월간 달력으로 보여 주고, 같은 네트워크의 Windows PC 시스템 지표를 육각 링으로 함께 표시합니다.

저장소: [github.com/soma0sd/rpi-calendar-kiosk](https://github.com/soma0sd/rpi-calendar-kiosk)

> 아래 화면은 모두 더미 데이터로 렌더한 예시이며 실제 개인 일정과 계정 정보는 포함하지 않습니다.

![시스템 모니터와 일정 목록](screenshots/system-monitor-3-column.png)

## 1. 준비물

| 구분 | 내용 |
|---|---|
| 보드 | Raspberry Pi 4, Debian 12 Bookworm, Wayland(labwc), Chromium 138 이상 |
| 패널 | Waveshare HDMI 400x1280(컴포지터에서 270도 회전하여 1280x400 가로로 사용) |
| 계정 | 구글 계정, Google Cloud Console 의 OAuth 데스크톱 클라이언트, Calendar API 사용 설정 |
| 작업 PC | Windows 11, Rust stable, Python 3.11, uv, PowerShell |

전원은 USB-C 로 공급합니다. PC 지표는 케이블이 아니라 내부 네트워크의 서명된 HTTP 요청으로 전달됩니다.

## 2. 설치

### 2.1 구글 인증

Google Cloud Console 에서 OAuth 데스크톱 클라이언트를 발급하여 `secrets/credentials.json` 으로 저장하고,
같은 콘솔에서 Google Calendar API 를 사용 설정합니다. 그다음 작업 PC 에서 최초 1회 동의 절차를 진행합니다.

```powershell
uv sync
uv run python -m soma0sd_rpi_schedule --auth
```

브라우저 동의가 끝나면 `secrets/token.json` 이 만들어집니다. 이 두 파일은 저장소에 포함되지 않으며 개인
자격 증명이므로 외부에 공유하지 않습니다.

### 2.2 배포 대상 지정

호스트 주소는 저장소에 두지 않고 환경 변수로 전달합니다.

```powershell
$env:SOMA0SD_RPI_HOST = "<라즈베리파이 ssh 별칭 또는 user@host>"
$env:SOMA0SD_WINDOWS_HOST = "<지표 송신기를 설치할 Windows PC ssh 별칭>"
$env:SOMA0SD_MONITOR_TARGET = "http://<라즈베리파이 주소>:8765/api/system"
$env:SOMA0SD_SSH_CONFIG = "<별칭이 정의된 ssh config 경로>"
```

반복해서 쓴다면 `.claude/deploy-local.ps1`(git 비추적)에 적어 둡니다. 배포 스크립트가 자동으로 읽습니다.

### 2.3 설치 순서

```powershell
pwsh scripts/deploy.ps1                       # 서버 소스 동기화 + 라즈베리파이 릴리스 빌드
pwsh scripts/push_token.ps1                   # 구글 token.json 전달(최초 1회)
pwsh scripts/install_host_monitor.ps1         # 작업 PC 지표 송신기 설치(표시 순서 0)
pwsh scripts/install_remote_host_monitor.ps1  # 원격 Windows 지표 송신기 설치(표시 순서 1)
pwsh scripts/install_service.ps1              # systemd 사용자 서비스 등록과 상태 검증
```

설치가 끝나면 라즈베리파이는 부팅과 동시에 `rpi-calendar-kiosk.service` 를 실행하고, Windows PC 는 로그인
시점에 `rpi-calendar-host-monitor` 예약 작업을 시작합니다. 비정상 종료 시 5초 후 자동으로 재시작합니다.

> **버전을 함께 올려야 합니다.** v0.4.0 부터 지표 푸시가 공유 토큰 헤더 대신 HMAC 서명을 씁니다. 서버와
> 송신기 버전이 다르면 푸시가 401 로 거절되므로 `deploy.ps1` 실행 후 송신기 설치 스크립트도 이어서
> 실행합니다. 그동안 지표만 잠시 OFFLINE 으로 표시되며 일정 표시는 영향을 받지 않습니다.

## 3. 화면 보는 법

### 3.1 1열: 시스템 모니터

- CPU 와 RAM, GPU 와 VRAM 을 각각 이중 육각 링으로 표시합니다. 바깥 링은 시계 방향, 안쪽 링은 반시계
  방향으로 돌며, 새 지표를 받을 때마다 시작점이 회전합니다.
- 드라이브는 문자별 육각 링으로 표시하고 가운데 색상 범례로 구분합니다. Windows 물리 파티션에 연결된
  드라이브만 나오며 Google Drive 같은 가상 볼륨은 제외합니다.
- 온도는 수집된 값이 있을 때만 표시합니다.
- 아래쪽 전체 너비 버튼을 누르면 등록된 PC 를 차례로 바꿔 봅니다. 표시 순서가 가장 앞선 장비가 기본입니다.
- 지표는 2초 간격으로 갱신합니다.

### 3.2 2열과 3열: 일정과 달력

- 기본 화면은 D-Day 순서의 일정과 할 일 목록입니다. 분류별로 제목 색상이 다릅니다.
- 오른쪽 세로 `›` 버튼을 누르면 같은 영역에 월간 달력이 나타나고, `‹` 버튼으로 목록으로 돌아옵니다.
- 일정을 터치하면 제목, 일시, 장소, 설명 전체를 스크롤로 확인할 수 있습니다.

![일정 전체 화면 상세](screenshots/event-detail-fullscreen.png)

![월간 달력](screenshots/calendar-view.png)

### 3.3 갱신 주기

| 대상 | 주기 | 실패 시 |
|---|---|---|
| 구글 일정·할 일 | 5분 | 디스크 캐시에 남은 마지막 값을 즉시 표시 |
| PC 시스템 지표 | 2초 | 일정 시간 수신이 없으면 OFFLINE 표시 |

## 4. 운영

```powershell
ssh $env:SOMA0SD_RPI_HOST 'systemctl --user status rpi-calendar-kiosk.service'
ssh $env:SOMA0SD_RPI_HOST 'systemctl --user restart rpi-calendar-kiosk.service'
ssh $env:SOMA0SD_RPI_HOST 'journalctl --user -u rpi-calendar-kiosk.service -f'
```

서비스 설치 중 검증에 실패하면 설치 스크립트가 직전 서비스 정의로 자동 복구하고, 성공하면 임시 복구본을
제거합니다.

## 5. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 지표가 OFFLINE 으로 남습니다 | 서버와 송신기 버전이 같은지 확인합니다. 서명 규약이 다르면 401 로 거절됩니다 |
| 푸시가 401 로 거절됩니다 | 공유 비밀이 양쪽에 같은 값으로 들어갔는지, 두 장비의 시각 차이가 120초 이내인지 확인합니다 |
| 일정이 비어 있습니다 | `secrets/token.json` 이 배포되었는지, 구글 계정 동의가 만료되지 않았는지 확인합니다 |
| 화면이 세로로 나옵니다 | 컴포지터 회전(Transform 270) 설정을 확인합니다. 회전 후 해상도는 1280x400 입니다 |
| 브라우저 번역 풍선이 뜹니다 | 격리 프로필과 관리 정책이 적용되었는지 확인합니다. 정상 설치 시 자동으로 차단됩니다 |

## 6. 접근 범위와 개인정보

| 대상 | 노출 범위 | 근거 |
|---|---|---|
| 화면, `/api/state`, `/healthz` | 루프백 전용 | 개인 일정과 할 일 원문이 담기므로 키오스크 본체에만 응답하고 그 외 접속은 403 으로 거절합니다 |
| `POST /api/system` | 같은 네트워크 허용 | 지표 송신기용이며 요청마다 HMAC-SHA256 서명을 검증합니다 |
| 모든 요청 | Host 헤더 검증 | 도메인 이름 Host 는 400 으로 거절해 DNS 리바인딩 우회를 막습니다 |

지표 푸시는 `HMAC-SHA256(token, "<unix timestamp>\n<body>")` 값을 `X-Monitor-Signature` 헤더로 보냅니다.
공유 비밀 자체는 회선에 오르지 않으며, 타임스탬프 허용 범위(120초)를 벗어난 요청은 거절합니다.

구글 자격 증명(`secrets/`)은 저장소에 포함되지 않습니다. 일정 원문은 키오스크 본체 밖으로 나가지 않습니다.

## 7. 라이선스

이 프로젝트는 MIT 라이선스로 공개합니다. 저작권 표시와 라이선스 전문을 함께 남기면 복제, 수정, 배포,
상업적 이용이 모두 허용되며 무보증 조건이 적용됩니다. 전문은 저장소의
[LICENSE](https://github.com/soma0sd/rpi-calendar-kiosk/blob/main/LICENSE) 에 있습니다.
