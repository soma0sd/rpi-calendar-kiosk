//! 지표 푸시 요청의 HMAC-SHA256 서명.
//!
//! 수신 URL 이 사설망 평문 HTTP 이므로 공유 비밀을 헤더에 그대로 실어 보내면
//! 같은 LAN 에서 스니핑만으로 토큰이 통째로 넘어간다. 대신 요청마다
//! `HMAC-SHA256(token, "<timestamp>\n<body>")` 을 계산해 보내면 비밀 자체는
//! 회선에 오르지 않고, 타임스탬프 허용 범위로 재전송 창도 좁아진다.

use chrono::Utc;
use hmac::{Hmac, Mac};
use sha2::Sha256;
use subtle::ConstantTimeEq;

pub const TIMESTAMP_HEADER: &str = "X-Monitor-Timestamp";
pub const SIGNATURE_HEADER: &str = "X-Monitor-Signature";

/// 송신·수신 시각 차이 허용치(초). 시계 오차를 흡수하되 재전송 창은 좁게 둔다.
pub const TOLERANCE_SECONDS: i64 = 120;

fn payload(timestamp: &str, body: &[u8]) -> Vec<u8> {
    let mut buffer = Vec::with_capacity(timestamp.len() + 1 + body.len());
    buffer.extend_from_slice(timestamp.as_bytes());
    buffer.push(b'\n');
    buffer.extend_from_slice(body);
    buffer
}

fn hex_lower(bytes: &[u8]) -> String {
    use std::fmt::Write;
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        let _ = write!(encoded, "{byte:02x}");
    }
    encoded
}

/// 주어진 타임스탬프와 본문에 대한 서명 문자열(소문자 hex)을 만든다.
pub fn sign(token: &str, timestamp: &str, body: &[u8]) -> String {
    let mut mac =
        Hmac::<Sha256>::new_from_slice(token.as_bytes()).expect("HMAC accepts any key length");
    mac.update(&payload(timestamp, body));
    hex_lower(&mac.finalize().into_bytes())
}

/// 현재 시각 기준 타임스탬프와 서명을 함께 만든다.
pub fn sign_now(token: &str, body: &[u8]) -> (String, String) {
    let timestamp = Utc::now().timestamp().to_string();
    let signature = sign(token, &timestamp, body);
    (timestamp, signature)
}

/// 수신한 서명을 검증한다. 실패 사유는 호출자가 그대로 응답에 쓸 수 있는 짧은 문구다.
pub fn verify(
    token: &str,
    timestamp: &str,
    supplied: &str,
    body: &[u8],
) -> Result<(), &'static str> {
    if timestamp.is_empty() || supplied.is_empty() {
        return Err("missing signature headers");
    }
    let sent_at: i64 = timestamp.parse().map_err(|_| "invalid timestamp")?;
    if (Utc::now().timestamp() - sent_at).abs() > TOLERANCE_SECONDS {
        return Err("timestamp outside the accepted window");
    }
    let expected = sign(token, timestamp, body);
    let matched = supplied.len() == expected.len()
        && bool::from(supplied.as_bytes().ct_eq(expected.as_bytes()));
    if matched {
        Ok(())
    } else {
        Err("signature mismatch")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Python 구현(src/soma0sd_rpi_schedule/signature.py) 과 바이트 단위로 같은 값을 내야 한다.
    // 한쪽 규약이 바뀌면 이 벡터가 먼저 깨지도록 양쪽 테스트에 같은 값을 둔다.
    #[test]
    fn matches_the_cross_language_vector() {
        assert_eq!(
            sign("test-secret", "1757000000", br#"{"hostname":"host-a"}"#),
            "a247c072907332f75d25f7b41790e08df91872d32b795d2998a77865a05965b6"
        );
    }

    #[test]
    fn accepts_a_fresh_signature() {
        let body = br#"{"hostname":"host-a"}"#;
        let (timestamp, signature) = sign_now("secret", body);
        assert!(verify("secret", &timestamp, &signature, body).is_ok());
    }

    #[test]
    fn rejects_another_key_body_or_timestamp() {
        let body = br#"{"hostname":"host-a"}"#;
        let (timestamp, signature) = sign_now("secret", body);
        assert!(verify("other-secret", &timestamp, &signature, body).is_err());
        assert!(verify("secret", &timestamp, &signature, b"{}").is_err());
        let shifted = (Utc::now().timestamp() - TOLERANCE_SECONDS - 1).to_string();
        let replayed = sign("secret", &shifted, body);
        assert!(verify("secret", &shifted, &replayed, body).is_err());
    }

    #[test]
    fn rejects_missing_headers() {
        assert!(verify("secret", "", "", b"{}").is_err());
    }
}
