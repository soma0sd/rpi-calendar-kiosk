# SSH config 경로 해석 헬퍼. dot-source 로 호출자 스코프에 $SshArgs 를 노출.
# 우선순위: $env:SOMA0SD_SSH_CONFIG  →  기본(-F 미사용, ssh 가 ~/.ssh/config 사용).
# Why: 별칭 "1.66-RPi4-Display" 가 표준 ~/.ssh/config 가 아닌 외부 ssh config 에 정의돼 있다면,
#      $env:SOMA0SD_SSH_CONFIG 에 그 config 경로를 지정해야 ssh/scp 가 호스트명을 해석한다.
#      (표준 위치에 별칭이 있으면 환경변수 없이도 동작한다.)

$SshArgs = @()
if ($env:SOMA0SD_SSH_CONFIG) {
    $SshArgs = @("-F", $env:SOMA0SD_SSH_CONFIG)
}
