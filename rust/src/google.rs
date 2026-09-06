use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result, anyhow};
use chrono::{DateTime, Duration as ChronoDuration, SecondsFormat, Utc};
use reqwest::{Client, StatusCode};
use serde_json::Value;
use thiserror::Error;
use tokio::io::AsyncWriteExt;

use crate::calendar::{CalendarEvent, CalendarState, CalendarTask, now_kst};
use crate::config::{HOLIDAY_CALENDAR_ID, POLL_INTERVAL_SECONDS, TASK_LIST_ID, UPCOMING_DAYS};

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

    let tasks = match session.list_tasks(TASK_LIST_ID).await {
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

struct GoogleSession {
    client: Client,
    token_path: PathBuf,
    document: Value,
}

impl GoogleSession {
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

    async fn list_tasks(&mut self, task_list_id: &str) -> Result<Vec<CalendarTask>, SyncFailure> {
        let url = format!(
            "https://tasks.googleapis.com/tasks/v1/lists/{}/tasks",
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
                        .map(CalendarTask::from_google),
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
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
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
