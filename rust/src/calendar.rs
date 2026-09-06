use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{Context, Result, anyhow};
use chrono::{DateTime, FixedOffset, NaiveDate, SecondsFormat, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::RwLock;

fn kst_offset() -> FixedOffset {
    FixedOffset::east_opt(9 * 60 * 60).expect("valid KST offset")
}

pub fn now_kst() -> DateTime<FixedOffset> {
    Utc::now().with_timezone(&kst_offset())
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct CalendarEvent {
    pub id: String,
    pub summary: String,
    pub start_dt: String,
    pub end_dt: String,
    pub is_all_day: bool,
    #[serde(default)]
    pub location: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub is_holiday: bool,
}

impl CalendarEvent {
    pub fn from_google(value: &Value, is_holiday: bool) -> Result<Self> {
        let start = value
            .get("start")
            .and_then(Value::as_object)
            .context("event start is missing")?;
        let end = value
            .get("end")
            .and_then(Value::as_object)
            .context("event end is missing")?;
        let (start_dt, end_dt, is_all_day) =
            if let Some(start_date) = start.get("date").and_then(Value::as_str) {
                let end_date = end
                    .get("date")
                    .and_then(Value::as_str)
                    .unwrap_or(start_date);
                (
                    all_day_timestamp(start_date)?,
                    all_day_timestamp(end_date)?,
                    true,
                )
            } else {
                let start_value = start
                    .get("dateTime")
                    .and_then(Value::as_str)
                    .context("event start.dateTime is missing")?;
                let end_value = end
                    .get("dateTime")
                    .and_then(Value::as_str)
                    .unwrap_or(start_value);
                (
                    normalize_timestamp(start_value)?,
                    normalize_timestamp(end_value)?,
                    false,
                )
            };
        Ok(Self {
            id: text_field(value, "id", ""),
            summary: text_field(value, "summary", "(제목 없음)"),
            start_dt,
            end_dt,
            is_all_day,
            location: text_field(value, "location", ""),
            description: text_field(value, "description", ""),
            is_holiday,
        })
    }

    pub fn is_legal_korean_holiday(&self) -> bool {
        self.description.trim() == "공휴일"
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct CalendarTask {
    pub id: String,
    pub title: String,
    pub due_date: Option<String>,
    #[serde(default)]
    pub notes: String,
    #[serde(default)]
    pub parent: String,
}

impl CalendarTask {
    pub fn from_google(value: &Value) -> Self {
        let title = text_field(value, "title", "").trim().to_owned();
        // 바이트 슬라이싱은 문자 경계를 벗어나면 panic 하므로 get 으로 안전하게 자른다.
        let due_date = value
            .get("due")
            .and_then(Value::as_str)
            .and_then(|value| value.get(..10))
            .map(str::to_owned);
        Self {
            id: text_field(value, "id", ""),
            title: if title.is_empty() {
                "(제목 없음)".to_owned()
            } else {
                title
            },
            due_date,
            notes: text_field(value, "notes", ""),
            parent: text_field(value, "parent", ""),
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct CalendarData {
    #[serde(default)]
    pub events: Vec<CalendarEvent>,
    #[serde(default)]
    pub tasks: Vec<CalendarTask>,
    pub last_sync: Option<String>,
    pub last_error: Option<String>,
    #[serde(default)]
    pub auth_required: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct CachePayload {
    saved_at: String,
    #[serde(default)]
    events: Vec<CalendarEvent>,
    #[serde(default)]
    tasks: Vec<CalendarTask>,
}

#[derive(Clone)]
pub struct CalendarState {
    inner: Arc<RwLock<CalendarData>>,
    cache_path: PathBuf,
}

impl CalendarState {
    pub fn load(cache_path: PathBuf) -> Self {
        let initial = load_cache(&cache_path).unwrap_or_default();
        Self {
            inner: Arc::new(RwLock::new(initial)),
            cache_path,
        }
    }

    pub async fn snapshot(&self) -> CalendarData {
        self.inner.read().await.clone()
    }

    pub async fn record_error(&self, message: String, auth_required: bool) {
        let mut data = self.inner.write().await;
        data.last_error = Some(message);
        data.auth_required = auth_required;
    }

    pub async fn apply_success(
        &self,
        events: Vec<CalendarEvent>,
        tasks: Option<Vec<CalendarTask>>,
        warning: Option<String>,
    ) {
        let saved_at = now_kst().to_rfc3339_opts(SecondsFormat::Secs, false);
        let cache = {
            let mut data = self.inner.write().await;
            data.events = events;
            if let Some(tasks) = tasks {
                data.tasks = tasks;
            }
            data.last_sync = Some(saved_at.clone());
            data.last_error = warning;
            data.auth_required = false;
            CachePayload {
                saved_at,
                events: data.events.clone(),
                tasks: data.tasks.clone(),
            }
        };
        if let Err(error) = save_cache(&self.cache_path, &cache).await {
            eprintln!("[rpi-schedule] cache save failed: {error:#}");
        }
    }
}

fn load_cache(path: &Path) -> Option<CalendarData> {
    let raw = std::fs::read_to_string(path).ok()?;
    let cache: CachePayload = serde_json::from_str(&raw).ok()?;
    Some(CalendarData {
        events: cache.events,
        tasks: cache.tasks,
        last_sync: Some(cache.saved_at),
        last_error: None,
        auth_required: false,
    })
}

async fn save_cache(path: &Path, payload: &CachePayload) -> Result<()> {
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent)
            .await
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }
    let temporary = path.with_extension("json.tmp");
    let encoded = serde_json::to_vec_pretty(payload)?;
    tokio::fs::write(&temporary, encoded)
        .await
        .with_context(|| format!("failed to write {}", temporary.display()))?;
    tokio::fs::rename(&temporary, path)
        .await
        .with_context(|| format!("failed to replace {}", path.display()))?;
    Ok(())
}

fn normalize_timestamp(value: &str) -> Result<String> {
    let parsed = DateTime::parse_from_rfc3339(value)
        .with_context(|| format!("invalid RFC3339 timestamp: {value}"))?;
    Ok(parsed
        .with_timezone(&kst_offset())
        .to_rfc3339_opts(SecondsFormat::Secs, false))
}

fn all_day_timestamp(value: &str) -> Result<String> {
    let date = NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .with_context(|| format!("invalid all-day date: {value}"))?;
    let local = date
        .and_hms_opt(0, 0, 0)
        .ok_or_else(|| anyhow!("invalid all-day time"))?
        .and_local_timezone(kst_offset())
        .single()
        .ok_or_else(|| anyhow!("invalid KST date"))?;
    Ok(local.to_rfc3339_opts(SecondsFormat::Secs, false))
}

fn text_field(value: &Value, key: &str, fallback: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or(fallback)
        .to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn google_all_day_event_uses_kst_bounds() {
        let raw = serde_json::json!({
            "id": "e1",
            "summary": "휴일",
            "description": "공휴일",
            "start": {"date": "2026-09-04"},
            "end": {"date": "2026-09-05"}
        });
        let event = CalendarEvent::from_google(&raw, true).unwrap();
        assert_eq!(event.start_dt, "2026-09-04T00:00:00+09:00");
        assert_eq!(event.end_dt, "2026-09-05T00:00:00+09:00");
        assert!(event.is_legal_korean_holiday());
    }

    #[test]
    fn google_task_keeps_due_date_only() {
        let raw = serde_json::json!({
            "id": "t1",
            "title": "점검",
            "due": "2026-09-05T00:00:00.000Z"
        });
        let task = CalendarTask::from_google(&raw);
        assert_eq!(task.due_date.as_deref(), Some("2026-09-05"));
    }

    #[test]
    fn google_task_due_survives_unexpected_values() {
        // 문자 경계를 벗어나는 바이트 슬라이싱은 panic 하므로 잘라내지 않고 버린다.
        for due in ["짧음", "가나다라마바사아자차", "2026-09"] {
            let raw = serde_json::json!({"id": "t1", "title": "점검", "due": due});
            assert_eq!(CalendarTask::from_google(&raw).due_date, None, "due {due}");
        }
    }
}
