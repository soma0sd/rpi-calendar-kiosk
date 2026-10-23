use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result, anyhow};
use chrono::{DateTime, Duration as ChronoDuration, SecondsFormat, Utc};
use reqwest::{Client, StatusCode};
use serde_json::Value;
use thiserror::Error;
use tokio::io::AsyncWriteExt;

use crate::calendar::{CalendarEvent, CalendarState, CalendarTask, now_kst};
use crate::config::{HOLIDAY_CALENDAR_ID, POLL_INTERVAL_SECONDS, UPCOMING_DAYS};

#[derive(Debug, Error)]
pub enum SyncFailure {
    #[error("Google reauthentication required: {0}")]
    Authentication(String),
    #[error(transparent)]
    Other(#[from] anyhow::Error),
}

pub async fn run_sync_loop(state: CalendarState, token_path: PathBuf) {
    loop {
        match sync_once(&state, &token_path).await {
            Ok(()) => {}
            Err(SyncFailure::Authentication(message)) => {
                eprintln!("[rpi-schedule] {message}");
                state.record_error(message, true).await;
            }
            Err(SyncFailure::Other(error)) => {
                let message = format!("{}: {error:#}", error.root_cause());
                eprintln!("[rpi-schedule] calendar sync failed: {message}");
                state.record_error(message, false).await;
            }
        }
        tokio::time::sleep(Duration::from_secs(POLL_INTERVAL_SECONDS)).await;
    }
}

pub async fn sync_once(state: &CalendarState, token_path: &Path) -> Result<(), SyncFailure> {
    // 조회 중 완료한 할일이 이전 응답으로 되살아나거나 토큰 갱신이 충돌하지 않도록 직렬화한다.
    let _update = state.update_lock.lock().await;
    let mut session = GoogleSession::load(token_path).await?;
    let start = now_kst() - ChronoDuration::days(1);
    let end = now_kst() + ChronoDuration::days(UPCOMING_DAYS);
    let start_text = start.to_rfc3339_opts(SecondsFormat::Secs, false);
    let end_text = end.to_rfc3339_opts(SecondsFormat::Secs, false);

    let events = session
        .list_events("primary", &start_text, &end_text, false)
        .await?;

    let mut warnings = Vec::new();
    let holidays = match session
        .list_events(HOLIDAY_CALENDAR_ID, &start_text, &end_text, true)
        .await
    {
        Ok(events) => events
            .into_iter()
            .filter(CalendarEvent::is_legal_korean_holiday)
            .collect(),
        Err(error) => {
            warnings.push(format!("공휴일 동기화 실패: {error}"));
            Vec::new()
        }
    };

    let tasks = match session
        .list_all_tasks_at("https://tasks.googleapis.com/tasks/v1")
        .await
    {
        Ok(tasks) => Some(tasks),
        Err(error) => {
            warnings.push(format!("할일 동기화 실패: {error}"));
            None
        }
    };

    let mut merged = events;
    merged.extend(holidays);
    let warning = (!warnings.is_empty()).then(|| warnings.join(" | "));
    state.apply_success(merged, tasks, warning).await;
    Ok(())
}

#[derive(Debug, Error)]
pub enum CompleteTaskFailure {
    #[error("목록에 없는 할일입니다. 목록을 새로 확인해 주세요.")]
    NotFound,
    #[error(transparent)]
    Google(#[from] SyncFailure),
}

pub async fn complete_task(
    state: &CalendarState,
    token_path: &Path,
    task_id: &str,
    task_list_id: &str,
) -> Result<(), CompleteTaskFailure> {
    let _update = state.update_lock.lock().await;
    if !state.snapshot().await.tasks.iter().any(|task| {
        task.id == task_id && task.task_list_id == task_list_id && task.parent.is_empty()
    }) {
        return Err(CompleteTaskFailure::NotFound);
    }
    let mut session = GoogleSession::load(token_path).await?;
    let url = format!(
        "https://tasks.googleapis.com/tasks/v1/lists/{}/tasks/{}",
        urlencoding::encode(task_list_id),
        urlencoding::encode(task_id),
    );
    session.complete_task_at(&url).await?;
    state.remove_completed_task(task_id, task_list_id).await;
    Ok(())
}

struct GoogleSession {
    client: Client,
    token_path: PathBuf,
    document: Value,
}

impl GoogleSession {
    async fn complete_task_at(&mut self, url: &str) -> Result<(), SyncFailure> {
        let mut token = self.access_token(false).await?;
        let payload = serde_json::json!({"status": "completed"});
        let mut response = self
            .client
            .patch(url)
            .bearer_auth(&token)
            .json(&payload)
            .send()
            .await
            .map_err(|error| anyhow!(error).context("할일 완료 요청 실패"))?;
        if response.status() == StatusCode::UNAUTHORIZED {
            token = self.access_token(true).await?;
            response = self
                .client
                .patch(url)
                .bearer_auth(&token)
                .json(&payload)
                .send()
                .await
                .map_err(|error| anyhow!(error).context("할일 완료 재요청 실패"))?;
        }
        let status = response.status();
        let body: Value = response
            .json()
            .await
            .map_err(|error| anyhow!(error).context("할일 완료 응답 형식 오류"))?;
        if status == StatusCode::UNAUTHORIZED || status == StatusCode::FORBIDDEN {
            return Err(SyncFailure::Authentication(
                "할일 수정 권한이 필요합니다. PC에서 --auth 재인증 후 토큰을 배포해 주세요."
                    .to_owned(),
            ));
        }
        if !status.is_success() {
            return Err(anyhow!(api_error_message(status, &body)).into());
        }
        if body.get("status").and_then(Value::as_str) != Some("completed") {
            return Err(anyhow!("Google에서 할일 완료를 확인하지 못했습니다.").into());
        }
        Ok(())
    }

    async fn load(token_path: &Path) -> Result<Self, SyncFailure> {
        let raw = tokio::fs::read_to_string(token_path)
            .await
            .map_err(|error| {
                SyncFailure::Authentication(format!(
                    "token file unavailable at {}: {error}",
                    token_path.display()
                ))
            })?;
        let document: Value = serde_json::from_str(&raw)
            .map_err(|error| SyncFailure::Authentication(format!("invalid token file: {error}")))?;
        let client = Client::builder()
            .timeout(Duration::from_secs(20))
            .user_agent(concat!("soma0sd-rpi-schedule/", env!("CARGO_PKG_VERSION")))
            .build()
            .context("failed to create Google API client")?;
        Ok(Self {
            client,
            token_path: token_path.to_path_buf(),
            document,
        })
    }

    async fn access_token(&mut self, force_refresh: bool) -> Result<String, SyncFailure> {
        if !force_refresh
            && !self.token_needs_refresh()
            && let Some(token) = self.document.get("token").and_then(Value::as_str)
            && !token.is_empty()
        {
            return Ok(token.to_owned());
        }
        self.refresh_token().await
    }

    fn token_needs_refresh(&self) -> bool {
        let Some(token) = self.document.get("token").and_then(Value::as_str) else {
            return true;
        };
        if token.is_empty() {
            return true;
        }
        self.document
            .get("expiry")
            .and_then(Value::as_str)
            .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
            .is_some_and(|expiry| {
                expiry.with_timezone(&Utc) <= Utc::now() + ChronoDuration::seconds(60)
            })
    }

    async fn refresh_token(&mut self) -> Result<String, SyncFailure> {
        let refresh_token = required_text(&self.document, "refresh_token")?;
        let client_id = required_text(&self.document, "client_id")?;
        let client_secret = required_text(&self.document, "client_secret")?;
        let token_uri = self
            .document
            .get("token_uri")
            .and_then(Value::as_str)
            .unwrap_or("https://oauth2.googleapis.com/token")
            .to_owned();
        let response = self
            .client
            .post(token_uri)
            .form(&[
                ("grant_type", "refresh_token"),
                ("refresh_token", refresh_token.as_str()),
                ("client_id", client_id.as_str()),
                ("client_secret", client_secret.as_str()),
            ])
            .send()
            .await
            .map_err(|error| anyhow!(error).context("token refresh request failed"))?;
        let status = response.status();
        let body: Value = response
            .json()
            .await
            .map_err(|error| anyhow!(error).context("invalid token refresh response"))?;
        if !status.is_success() {
            return Err(SyncFailure::Authentication(api_error_message(
                status, &body,
            )));
        }
        let access_token = body
            .get("access_token")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .ok_or_else(|| {
                SyncFailure::Authentication("refresh response has no access_token".to_owned())
            })?
            .to_owned();
        let expires_in = body
            .get("expires_in")
            .and_then(Value::as_i64)
            .unwrap_or(3600);
        let expiry = (Utc::now() + ChronoDuration::seconds(expires_in))
            .to_rfc3339_opts(SecondsFormat::Micros, true);
        self.document["token"] = Value::String(access_token.clone());
        self.document["expiry"] = Value::String(expiry);
        save_token_document(&self.token_path, &self.document).await?;
        Ok(access_token)
    }

    async fn get_json(
        &mut self,
        url: &str,
        query: &[(String, String)],
    ) -> Result<Value, SyncFailure> {
        let mut token = self.access_token(false).await?;
        let mut response = self
            .client
            .get(url)
            .bearer_auth(&token)
            .query(query)
            .send()
            .await
            .map_err(|error| anyhow!(error).context("Google API request failed"))?;
        if response.status() == StatusCode::UNAUTHORIZED {
            token = self.access_token(true).await?;
            response = self
                .client
                .get(url)
                .bearer_auth(&token)
                .query(query)
                .send()
                .await
                .map_err(|error| anyhow!(error).context("Google API retry failed"))?;
        }
        let status = response.status();
        let body: Value = response
            .json()
            .await
            .map_err(|error| anyhow!(error).context("invalid Google API response"))?;
        if status == StatusCode::UNAUTHORIZED || status == StatusCode::FORBIDDEN {
            return Err(SyncFailure::Authentication(api_error_message(
                status, &body,
            )));
        }
        if !status.is_success() {
            return Err(anyhow!(api_error_message(status, &body)).into());
        }
        Ok(body)
    }

    async fn list_events(
        &mut self,
        calendar_id: &str,
        time_min: &str,
        time_max: &str,
        is_holiday: bool,
    ) -> Result<Vec<CalendarEvent>, SyncFailure> {
        let url = format!(
            "https://www.googleapis.com/calendar/v3/calendars/{}/events",
            urlencoding::encode(calendar_id)
        );
        let mut events = Vec::new();
        let mut page_token: Option<String> = None;
        loop {
            let mut query = vec![
                ("timeMin".to_owned(), time_min.to_owned()),
                ("timeMax".to_owned(), time_max.to_owned()),
                ("singleEvents".to_owned(), "true".to_owned()),
                ("orderBy".to_owned(), "startTime".to_owned()),
                ("timeZone".to_owned(), "Asia/Seoul".to_owned()),
                ("maxResults".to_owned(), "250".to_owned()),
            ];
            if let Some(token) = page_token.as_ref() {
                query.push(("pageToken".to_owned(), token.clone()));
            }
            let payload = self.get_json(&url, &query).await?;
            if let Some(items) = payload.get("items").and_then(Value::as_array) {
                events.extend(
                    items
                        .iter()
                        .filter_map(|item| CalendarEvent::from_google(item, is_holiday).ok()),
                );
            }
            page_token = payload
                .get("nextPageToken")
                .and_then(Value::as_str)
                .map(str::to_owned);
            if page_token.is_none() {
                break;
            }
        }
        Ok(events)
    }

    async fn list_all_tasks_at(
        &mut self,
        base_url: &str,
    ) -> Result<Vec<CalendarTask>, SyncFailure> {
        let mut list_ids = Vec::new();
        let mut page_token = None;
        loop {
            let mut query = vec![("maxResults".to_owned(), "100".to_owned())];
            if let Some(token) = page_token {
                query.push(("pageToken".to_owned(), token));
            }
            let payload = self
                .get_json(&format!("{base_url}/users/@me/lists"), &query)
                .await?;
            if let Some(items) = payload.get("items").and_then(Value::as_array) {
                list_ids.extend(
                    items
                        .iter()
                        .filter_map(|item| item.get("id").and_then(Value::as_str))
                        .map(str::to_owned),
                );
            }
            page_token = payload
                .get("nextPageToken")
                .and_then(Value::as_str)
                .map(str::to_owned);
            if page_token.is_none() {
                break;
            }
        }
        let mut tasks = Vec::new();
        for list_id in list_ids {
            tasks.extend(self.list_tasks_at(base_url, &list_id).await?);
        }
        Ok(tasks)
    }

    async fn list_tasks_at(
        &mut self,
        base_url: &str,
        task_list_id: &str,
    ) -> Result<Vec<CalendarTask>, SyncFailure> {
        let url = format!(
            "{base_url}/lists/{}/tasks",
            urlencoding::encode(task_list_id)
        );
        let mut tasks = Vec::new();
        let mut page_token: Option<String> = None;
        loop {
            let mut query = vec![
                ("showCompleted".to_owned(), "false".to_owned()),
                ("showHidden".to_owned(), "false".to_owned()),
                ("maxResults".to_owned(), "100".to_owned()),
            ];
            if let Some(token) = page_token.as_ref() {
                query.push(("pageToken".to_owned(), token.clone()));
            }
            let payload = self.get_json(&url, &query).await?;
            if let Some(items) = payload.get("items").and_then(Value::as_array) {
                tasks.extend(
                    items
                        .iter()
                        .filter(|item| {
                            item.get("status").and_then(Value::as_str) != Some("completed")
                        })
                        .map(|item| {
                            let mut task = CalendarTask::from_google(item);
                            task.task_list_id = task_list_id.to_owned();
                            task
                        }),
                );
            }
            page_token = payload
                .get("nextPageToken")
                .and_then(Value::as_str)
                .map(str::to_owned);
            if page_token.is_none() {
                break;
            }
        }
        Ok(tasks)
    }
}

fn required_text(document: &Value, key: &str) -> Result<String, SyncFailure> {
    document
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| SyncFailure::Authentication(format!("token file has no {key}")))
}

async fn save_token_document(path: &Path, document: &Value) -> Result<()> {
    let temporary = path.with_extension("json.tmp");
    let encoded = serde_json::to_vec_pretty(document)?;
    // refresh token 과 client_secret 이 들어 있으므로 파일을 만드는 순간부터 소유자 전용으로 연다.
    // 먼저 쓰고 나중에 chmod 하면 그 사이 umask 기본값(0644)으로 다른 사용자가 읽을 수 있다.
    let _ = tokio::fs::remove_file(&temporary).await;
    let mut options = tokio::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut file = options
        .open(&temporary)
        .await
        .with_context(|| format!("failed to create {}", temporary.display()))?;
    file.write_all(&encoded)
        .await
        .with_context(|| format!("failed to write {}", temporary.display()))?;
    file.sync_all()
        .await
        .with_context(|| format!("failed to flush {}", temporary.display()))?;
    drop(file);
    tokio::fs::rename(&temporary, path)
        .await
        .with_context(|| format!("failed to replace {}", path.display()))?;
    Ok(())
}

fn api_error_message(status: StatusCode, body: &Value) -> String {
    let message = body
        .pointer("/error/message")
        .and_then(Value::as_str)
        .or_else(|| body.pointer("/error_description").and_then(Value::as_str))
        .unwrap_or("Google API request failed");
    format!("HTTP {}: {message}", status.as_u16())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn empty_default_list_does_not_hide_later_lists_and_pages() {
        use axum::{
            Json, Router,
            extract::{Path, Query},
            routing::get,
        };
        use std::collections::HashMap;

        async fn lists(Query(query): Query<HashMap<String, String>>) -> Json<Value> {
            Json(if !query.contains_key("pageToken") {
                serde_json::json!({"items": [{"id": "empty-default"}], "nextPageToken": "lists-next"})
            } else {
                assert_eq!(query["pageToken"], "lists-next");
                serde_json::json!({"items": [{"id": "second"}]})
            })
        }
        async fn tasks(
            Path(id): Path<String>,
            Query(query): Query<HashMap<String, String>>,
        ) -> Json<Value> {
            assert_eq!(query["showCompleted"], "false");
            Json(match id.as_str() {
                "empty-default" => serde_json::json!({"items": []}),
                "second" if !query.contains_key("pageToken") => {
                    serde_json::json!({"items": [{"id": "a", "title": "두 번째 목록"}], "nextPageToken": "tasks-next"})
                }
                "second" => {
                    assert_eq!(query["pageToken"], "tasks-next");
                    serde_json::json!({"items": [{"id": "b", "title": "다음 페이지"}, {"id": "done", "status": "completed"}]})
                }
                _ => panic!("unexpected task list"),
            })
        }
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let app = Router::new()
            .route("/users/@me/lists", get(lists))
            .route("/lists/{id}/tasks", get(tasks));
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let mut session = GoogleSession {
            client: Client::new(),
            token_path: PathBuf::new(),
            document: serde_json::json!({"token": "test-access", "expiry": "2099-01-01T00:00:00Z"}),
        };
        let tasks = session.list_all_tasks_at(&base).await.unwrap();
        assert_eq!(
            tasks
                .iter()
                .map(|task| (task.id.as_str(), task.task_list_id.as_str()))
                .collect::<Vec<_>>(),
            [("a", "second"), ("b", "second")]
        );
        server.abort();
        let _ = server.await;
    }

    #[tokio::test]
    async fn completion_sends_patch_and_requires_confirmed_status() {
        use axum::{Json, Router, extract::State, http::HeaderMap, routing::patch};
        async fn respond(
            State(status): State<StatusCode>,
            headers: HeaderMap,
            Json(body): Json<Value>,
        ) -> (StatusCode, Json<Value>) {
            assert_eq!(headers["authorization"], "Bearer test-access");
            assert_eq!(body, serde_json::json!({"status": "completed"}));
            let payload = if status == StatusCode::OK {
                serde_json::json!({"status": "completed"})
            } else if status == StatusCode::ACCEPTED {
                serde_json::json!({"status": "needsAction"})
            } else {
                serde_json::json!({"error": {"message": "denied"}})
            };
            (status, Json(payload))
        }
        for status in [
            StatusCode::OK,
            StatusCode::ACCEPTED,
            StatusCode::FORBIDDEN,
            StatusCode::INTERNAL_SERVER_ERROR,
        ] {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let url = format!("http://{}/task", listener.local_addr().unwrap());
            let app = Router::new()
                .route("/task", patch(respond))
                .with_state(status);
            let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
            let mut session = GoogleSession {
                client: Client::new(),
                token_path: PathBuf::new(),
                document: serde_json::json!({"token": "test-access", "expiry": "2099-01-01T00:00:00Z"}),
            };
            let result = session.complete_task_at(&url).await;
            match status {
                StatusCode::OK => assert!(result.is_ok()),
                StatusCode::FORBIDDEN => {
                    assert!(matches!(result, Err(SyncFailure::Authentication(_))))
                }
                _ => assert!(matches!(result, Err(SyncFailure::Other(_)))),
            }
            server.abort();
            let _ = server.await;
        }
    }

    #[tokio::test]
    async fn unknown_tasks_never_load_credentials() {
        let directory = tempfile::tempdir().unwrap();
        let state = CalendarState::load(directory.path().join("events.json"));
        let result = complete_task(
            &state,
            &directory.path().join("missing-token.json"),
            "unknown",
            "@default",
        )
        .await;
        assert!(matches!(result, Err(CompleteTaskFailure::NotFound)));
    }

    #[tokio::test]
    async fn completion_removes_parent_and_children_from_persistent_cache() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("events.json");
        let state = CalendarState::load(path.clone());
        let mut tasks: Vec<_> = [
            serde_json::json!({"id": "parent", "title": "상위"}),
            serde_json::json!({"id": "child", "parent": "parent", "title": "하위"}),
            serde_json::json!({"id": "other", "title": "유지"}),
        ]
        .iter()
        .map(CalendarTask::from_google)
        .collect();
        let mut same_id_in_another_list = CalendarTask::from_google(
            &serde_json::json!({"id": "parent", "title": "다른 목록 유지"}),
        );
        same_id_in_another_list.task_list_id = "another-list".to_owned();
        tasks.push(same_id_in_another_list);
        state.apply_success(Vec::new(), Some(tasks), None).await;
        state.remove_completed_task("parent", "@default").await;
        let restored = CalendarState::load(path).snapshot().await;
        assert_eq!(restored.tasks.len(), 2);
        assert_eq!(restored.tasks[0].id, "other");
        assert_eq!(restored.tasks[1].task_list_id, "another-list");
    }

    #[tokio::test]
    async fn token_document_is_written_owner_only_and_replaceable() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("token.json");

        save_token_document(&path, &serde_json::json!({"token": "first"}))
            .await
            .unwrap();
        // 같은 경로로 다시 저장해도 임시 파일 충돌 없이 교체된다.
        save_token_document(&path, &serde_json::json!({"token": "second"}))
            .await
            .unwrap();

        let stored: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(stored["token"], "second");
        assert!(!path.with_extension("json.tmp").exists());

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&path).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o600, "token file must stay owner-only");
        }
    }
}
