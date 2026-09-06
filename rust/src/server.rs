use std::net::{IpAddr, SocketAddr};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use axum::Router;
use axum::body::{Body, Bytes};
use axum::extract::{ConnectInfo, DefaultBodyLimit, Request, State};
use axum::http::header::{CACHE_CONTROL, CONTENT_TYPE, HOST};
use axum::http::{HeaderMap, Response, StatusCode};
use axum::middleware::{self, Next};
use axum::response::IntoResponse;
use axum::routing::{get, post};
use serde_json::{Map, Value, json};


use crate::calendar::{CalendarState, now_kst};
use crate::monitor::{MonitorSample, MonitorStore, MonitorView};
use crate::signature::{SIGNATURE_HEADER, TIMESTAMP_HEADER};
use crate::{APP_NAME, APP_VERSION, signature};

const INDEX_HTML: &[u8] = include_bytes!("../../web/index.html");
const STYLE_CSS: &[u8] = include_bytes!("../../web/style.css");
const APP_JS: &[u8] = include_bytes!("../../web/app.js");

#[derive(Clone)]
pub struct AppState {
    pub calendar: CalendarState,
    pub monitors: MonitorStore,
    monitor_token: Arc<str>,
}

impl AppState {
    pub fn new(calendar: CalendarState, monitor_token: String) -> Self {
        Self {
            calendar,
            monitors: MonitorStore::new(Duration::from_secs(12)),
            monitor_token: Arc::from(monitor_token),
        }
    }
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/", get(index))
        .route("/index.html", get(index))
        .route("/static/style.css", get(style))
        .route("/static/app.js", get(script))
        .route("/api/state", get(api_state))
        .route("/api/system", post(api_system))
        .route("/healthz", get(health))
        .layer(middleware::from_fn(guard_request))
        .layer(DefaultBodyLimit::max(65_536))
        .with_state(state)
}

/// 원격 요청을 지표 수신 경로로만 제한한다.
///
/// 지표를 밀어 넣는 Windows PC 때문에 LAN 바인딩이 필요하지만, 개인 일정과 할일이
/// 그대로 담긴 `/api/state` 와 키오스크 UI 까지 같은 리스너로 공개할 이유는 없다.
/// 브라우저는 DNS 리바인딩으로 사설 IP 를 same-origin 으로 승격시킬 수 있으므로
/// Host 헤더가 도메인이면 함께 막는다.
async fn guard_request(request: Request, next: Next) -> Response<Body> {
    if !host_header_is_allowed(request.headers()) {
        return no_store_json(
            StatusCode::BAD_REQUEST,
            json!({"error": "unexpected Host header"}),
        );
    }
    let from_loopback = request
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .is_some_and(|ConnectInfo(peer)| peer.ip().is_loopback());
    if !from_loopback && request.uri().path() != "/api/system" {
        return no_store_json(
            StatusCode::FORBIDDEN,
            json!({"error": "this endpoint is available on the loopback interface only"}),
        );
    }
    next.run(request).await
}

/// Host 헤더가 IP 리터럴이나 localhost 인지 확인한다. 도메인 이름이면 거부한다.
fn host_header_is_allowed(headers: &HeaderMap) -> bool {
    let Some(host) = headers.get(HOST).and_then(|value| value.to_str().ok()) else {
        // Host 를 붙이지 않는 요청은 브라우저 경유가 아니므로 리바인딩 대상이 아니다.
        return true;
    };
    let host = host.trim();
    let name = if let Some(rest) = host.strip_prefix('[') {
        match rest.split_once(']') {
            Some((inner, _)) => inner,
            None => return false,
        }
    } else {
        host.split(':').next().unwrap_or("")
    };
    name.eq_ignore_ascii_case("localhost") || name.parse::<IpAddr>().is_ok()
}

pub async fn serve(address: SocketAddr, state: AppState) -> Result<()> {
    let listener = tokio::net::TcpListener::bind(address)
        .await
        .with_context(|| format!("failed to bind {address}"))?;
    println!("[{APP_NAME}] Rust {APP_VERSION} serving at http://{address}");
    if !address.ip().is_loopback() {
        println!(
            "[{APP_NAME}] remote clients may reach /api/system only; the UI and /api/state stay on loopback"
        );
    }
    axum::serve(
        listener,
        router(state).into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown_signal())
    .await
    .context("HTTP server failed")
}

async fn api_state(State(state): State<AppState>) -> impl IntoResponse {
    let calendar = state.calendar.snapshot().await;
    let monitors = state.monitors.snapshots().await;
    let mut object = match serde_json::to_value(calendar).unwrap_or_else(|_| json!({})) {
        Value::Object(object) => object,
        _ => Map::new(),
    };
    object.insert(
        "server_time".to_owned(),
        Value::String(now_kst().to_rfc3339()),
    );
    object.insert("system_monitor".to_owned(), legacy_monitor(&monitors));
    object.insert(
        "system_monitors".to_owned(),
        serde_json::to_value(monitors).unwrap_or_else(|_| json!([])),
    );
    no_store_json(StatusCode::OK, Value::Object(object))
}

async fn api_system(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Bytes,
) -> impl IntoResponse {
    if state.monitor_token.is_empty() {
        return no_store_json(
            StatusCode::SERVICE_UNAVAILABLE,
            json!({"error": "monitor push is disabled"}),
        );
    }
    let timestamp = header_text(&headers, TIMESTAMP_HEADER);
    let supplied = header_text(&headers, SIGNATURE_HEADER);
    if let Err(reason) = signature::verify(&state.monitor_token, timestamp, supplied, &body) {
        return no_store_json(StatusCode::UNAUTHORIZED, json!({"error": reason}));
    }
    let sample: MonitorSample = match serde_json::from_slice(&body) {
        Ok(sample) => sample,
        Err(error) => {
            return no_store_json(StatusCode::BAD_REQUEST, json!({"error": error.to_string()}));
        }
    };
    if let Err(error) = state.monitors.update(sample).await {
        return no_store_json(StatusCode::BAD_REQUEST, json!({"error": error.to_string()}));
    }
    (StatusCode::NO_CONTENT, [(CACHE_CONTROL, "no-store")]).into_response()
}

fn header_text<'a>(headers: &'a HeaderMap, name: &str) -> &'a str {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
}

async fn health(State(state): State<AppState>) -> impl IntoResponse {
    let monitors = state.monitors.snapshots().await;
    no_store_json(
        StatusCode::OK,
        json!({
            "status": "ok",
            "app": APP_NAME,
            "runtime": "rust",
            "version": APP_VERSION,
            "known_monitors": monitors.len(),
            "online_monitors": monitors.iter().filter(|monitor| monitor.available).count(),
        }),
    )
}

async fn index() -> Response<Body> {
    static_response("text/html; charset=utf-8", INDEX_HTML)
}

async fn style() -> Response<Body> {
    static_response("text/css; charset=utf-8", STYLE_CSS)
}

async fn script() -> Response<Body> {
    static_response("application/javascript; charset=utf-8", APP_JS)
}

fn static_response(content_type: &'static str, content: &'static [u8]) -> Response<Body> {
    Response::builder()
        .status(StatusCode::OK)
        .header(CONTENT_TYPE, content_type)
        .header(CACHE_CONTROL, "no-store")
        .body(Body::from(content))
        .expect("valid static response")
}

fn no_store_json(status: StatusCode, payload: Value) -> Response<Body> {
    let encoded = serde_json::to_vec(&payload).unwrap_or_else(|_| b"{}".to_vec());
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, "application/json; charset=utf-8")
        .header(CACHE_CONTROL, "no-store")
        .body(Body::from(encoded))
        .expect("valid JSON response")
}

fn legacy_monitor(monitors: &[MonitorView]) -> Value {
    monitors
        .iter()
        .min_by(|left, right| left.age_seconds.total_cmp(&right.age_seconds))
        .and_then(|monitor| serde_json::to_value(monitor).ok())
        .unwrap_or_else(|| json!({"available": false, "stale": true}))
}

async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
}

#[cfg(test)]
mod tests {
    use axum::body::to_bytes;
    use tempfile::tempdir;
    use tower::ServiceExt;

    use super::*;

    const LOOPBACK_PEER: &str = "127.0.0.1:52000";
    const REMOTE_PEER: &str = "192.0.2.10:52000";

    fn test_state() -> AppState {
        let root = tempdir().unwrap().keep();
        let calendar = CalendarState::load(root.join("events.json"));
        AppState::new(calendar, "test-secret".to_owned())
    }

    fn sample(hostname: &str) -> Value {
        json!({
            "hostname": hostname,
            "captured_at": "2026-09-04T12:00:00+09:00",
            "cpu_percent": 20,
            "memory_percent": 40,
            "disk_percent": 60
        })
    }

    fn with_peer(mut request: Request, peer: &str) -> Request {
        request
            .extensions_mut()
            .insert(ConnectInfo(peer.parse::<SocketAddr>().unwrap()));
        request
    }

    fn get(path: &str, peer: &str) -> Request {
        with_peer(Request::get(path).body(Body::empty()).unwrap(), peer)
    }

    fn signed_push(token: &str, hostname: &str, peer: &str) -> Request {
        let body = sample(hostname).to_string();
        let (timestamp, signature) = signature::sign_now(token, body.as_bytes());
        with_peer(
            Request::post("/api/system")
                .header(CONTENT_TYPE, "application/json")
                .header(TIMESTAMP_HEADER, timestamp)
                .header(SIGNATURE_HEADER, signature)
                .body(Body::from(body))
                .unwrap(),
            peer,
        )
    }

    #[tokio::test]
    async fn monitor_endpoint_rejects_a_wrong_signature() {
        let request = signed_push("wrong-secret", "host-a", LOOPBACK_PEER);
        let response = router(test_state()).oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn monitor_endpoint_rejects_a_missing_signature() {
        let request = with_peer(
            Request::post("/api/system")
                .header(CONTENT_TYPE, "application/json")
                .body(Body::from(sample("host-a").to_string()))
                .unwrap(),
            LOOPBACK_PEER,
        );
        let response = router(test_state()).oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn state_lists_multiple_monitors() {
        let app = router(test_state());
        for hostname in ["host-b", "host-a"] {
            let response = app
                .clone()
                .oneshot(signed_push("test-secret", hostname, LOOPBACK_PEER))
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::NO_CONTENT);
        }
        let response = app.oneshot(get("/api/state", LOOPBACK_PEER)).await.unwrap();
        let body = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
        let payload: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(payload["system_monitors"].as_array().unwrap().len(), 2);
        assert_eq!(payload["system_monitors"][0]["hostname"], "host-a");
    }

    #[tokio::test]
    async fn remote_peers_may_push_metrics_but_not_read_state() {
        let app = router(test_state());
        let pushed = app
            .clone()
            .oneshot(signed_push("test-secret", "host-a", REMOTE_PEER))
            .await
            .unwrap();
        assert_eq!(pushed.status(), StatusCode::NO_CONTENT);

        for path in ["/api/state", "/", "/healthz", "/static/app.js"] {
            let response = app.clone().oneshot(get(path, REMOTE_PEER)).await.unwrap();
            assert_eq!(response.status(), StatusCode::FORBIDDEN, "path {path}");
        }
    }

    #[tokio::test]
    async fn rebinding_host_headers_are_rejected() {
        let request = with_peer(
            Request::get("/api/state")
                .header(HOST, "attacker.example:8765")
                .body(Body::empty())
                .unwrap(),
            LOOPBACK_PEER,
        );
        let response = router(test_state()).oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }

    #[tokio::test]
    async fn literal_hosts_are_accepted() {
        for host in ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"] {
            let request = with_peer(
                Request::get("/healthz")
                    .header(HOST, host)
                    .body(Body::empty())
                    .unwrap(),
                LOOPBACK_PEER,
            );
            let response = router(test_state()).oneshot(request).await.unwrap();
            assert_eq!(response.status(), StatusCode::OK, "host {host}");
        }
    }

    #[tokio::test]
    async fn health_identifies_rust_runtime() {
        let response = router(test_state())
            .oneshot(get("/healthz", LOOPBACK_PEER))
            .await
            .unwrap();
        let body = to_bytes(response.into_body(), 64 * 1024).await.unwrap();
        let payload: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(payload["runtime"], "rust");
        assert_eq!(payload["status"], "ok");
    }

    #[test]
    fn embedded_static_assets_are_current() {
        assert!(String::from_utf8_lossy(INDEX_HTML).contains("monitor-next"));
        assert!(String::from_utf8_lossy(APP_JS).contains("renderSystemMonitors"));
    }
}
