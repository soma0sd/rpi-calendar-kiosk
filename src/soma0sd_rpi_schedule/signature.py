"""지표 푸시 요청의 HMAC-SHA256 서명.

수신 URL 이 사설망 평문 HTTP 이므로 공유 비밀을 헤더에 그대로 실어 보내면 같은 LAN 에서
스니핑만으로 토큰이 통째로 넘어간다. 대신 요청마다 ``HMAC-SHA256(token, "<timestamp>\\n<body>")``
을 계산해 보내면 비밀 자체는 회선에 오르지 않고, 타임스탬프 허용 범위로 재전송 창도 좁아진다.

Rust 구현(``rust/src/signature.rs``) 과 바이트 단위로 같은 규약을 쓴다.
"""

from __future__ import annotations

import hmac
import time
from hashlib import sha256

TIMESTAMP_HEADER = "X-Monitor-Timestamp"
SIGNATURE_HEADER = "X-Monitor-Signature"

# 송신·수신 시각 차이 허용치(초). 시계 오차를 흡수하되 재전송 창은 좁게 둔다.
TOLERANCE_SECONDS = 120


def sign(token: str, timestamp: str, body: bytes) -> str:
    """주어진 타임스탬프와 본문에 대한 서명 문자열(소문자 hex)을 만든다."""
    payload = timestamp.encode("utf-8") + b"\n" + body
    return hmac.new(token.encode("utf-8"), payload, sha256).hexdigest()


def sign_now(token: str, body: bytes) -> tuple[str, str]:
    """현재 시각 기준 타임스탬프와 서명을 함께 만든다."""
    timestamp = str(int(time.time()))
    return timestamp, sign(token, timestamp, body)


def verify(token: str, timestamp: str, supplied: str, body: bytes) -> str | None:
    """서명을 검증한다. 통과하면 ``None``, 실패하면 응답에 쓸 짧은 사유를 돌려준다."""
    if not timestamp or not supplied:
        return "missing signature headers"
    try:
        sent_at = int(timestamp)
    except ValueError:
        return "invalid timestamp"
    if abs(int(time.time()) - sent_at) > TOLERANCE_SECONDS:
        return "timestamp outside the accepted window"
    # compare_digest 는 비 ASCII str 을 받으면 TypeError 를 던진다. 서명은 항상 hex 이므로
    # 바이트로 맞춰 비교해 헤더에 임의 문자가 들어와도 예외 없이 거절되게 한다.
    try:
        supplied_bytes = supplied.encode("ascii")
    except UnicodeEncodeError:
        return "signature mismatch"
    if not hmac.compare_digest(supplied_bytes, sign(token, timestamp, body).encode("ascii")):
        return "signature mismatch"
    return None
