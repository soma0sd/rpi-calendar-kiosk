"""signature: 지표 푸시 HMAC 서명 규약."""

from __future__ import annotations

import time

from soma0sd_rpi_schedule.signature import TOLERANCE_SECONDS, sign, sign_now, verify

# Rust 구현(rust/src/signature.rs) 과 바이트 단위로 같은 값을 내야 한다.
# 한쪽 규약이 바뀌면 이 벡터가 먼저 깨지도록 양쪽 테스트에 같은 값을 둔다.
GOLDEN_TOKEN = "test-secret"
GOLDEN_TIMESTAMP = "1757000000"
GOLDEN_BODY = b'{"hostname":"host-a"}'
GOLDEN_SIGNATURE = "a247c072907332f75d25f7b41790e08df91872d32b795d2998a77865a05965b6"


def test_matches_the_cross_language_vector() -> None:
    assert sign(GOLDEN_TOKEN, GOLDEN_TIMESTAMP, GOLDEN_BODY) == GOLDEN_SIGNATURE


def test_accepts_a_fresh_signature() -> None:
    timestamp, signature = sign_now(GOLDEN_TOKEN, GOLDEN_BODY)
    assert verify(GOLDEN_TOKEN, timestamp, signature, GOLDEN_BODY) is None


def test_rejects_another_key_body_or_timestamp() -> None:
    timestamp, signature = sign_now(GOLDEN_TOKEN, GOLDEN_BODY)
    assert verify("other-secret", timestamp, signature, GOLDEN_BODY) == "signature mismatch"
    assert verify(GOLDEN_TOKEN, timestamp, signature, b"{}") == "signature mismatch"

    stale = str(int(time.time()) - TOLERANCE_SECONDS - 1)
    replayed = sign(GOLDEN_TOKEN, stale, GOLDEN_BODY)
    assert verify(GOLDEN_TOKEN, stale, replayed, GOLDEN_BODY) == (
        "timestamp outside the accepted window"
    )


def test_rejects_missing_or_malformed_headers() -> None:
    assert verify(GOLDEN_TOKEN, "", "", GOLDEN_BODY) == "missing signature headers"
    assert verify(GOLDEN_TOKEN, "not-a-number", "abc", GOLDEN_BODY) == "invalid timestamp"


def test_rejects_non_ascii_signature_without_raising() -> None:
    # hmac.compare_digest 는 비 ASCII str 에 TypeError 를 던지므로 예외 없이 거절해야 한다.
    timestamp = str(int(time.time()))
    assert verify(GOLDEN_TOKEN, timestamp, "서명", GOLDEN_BODY) == "signature mismatch"
