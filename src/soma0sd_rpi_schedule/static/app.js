"use strict";

// 백엔드는 5분 폴링. 프런트는 30초마다 캐시 상태를 가져온다 — 화면이 최신 상태에서
// 길어야 ~5분 30초 지연. setInterval 비용은 미미.
const REFRESH_MS = 30 * 1000;
const KST_TZ = "Asia/Seoul";

let stateCache = null;
let currentMonth = (() => {
  const today = kstTodayString();
  const [y, m] = today.split("-");
  return { year: Number(y), month: Number(m) };
})();

function kstTodayString() {
  // en-CA 로케일은 ISO 형식(yyyy-mm-dd)을 제공한다.
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: KST_TZ, year: "numeric", month: "2-digit", day: "2-digit",
  });
  return fmt.format(new Date());
}

function startDate(ev) { return ev.start_dt.substring(0, 10); }
function endDate(ev) { return ev.end_dt.substring(0, 10); }

function calcDday(targetStr, todayStr) {
  const t = new Date(targetStr + "T00:00:00+09:00");
  const td = new Date(todayStr + "T00:00:00+09:00");
  return Math.round((t - td) / 86400000);
}
function formatDday(n) {
  if (n === 0) return "D-Day";
  if (n > 0) return `D-${n}`;
  return `D+${-n}`;
}

// D-1/D-0 구간에서는 일수 단위 대신 "오늘"/"내일" 텍스트를 보여준다. 시간 지정
// 이벤트는 절대 시각(HH:MM)을 같이 노출해 멀리서도 "오늘 14시" 가 한눈에 들어오게.
// 종일 이벤트는 시각이 의미 없으니 단어만, timed 는 두 줄 (CSS .with-time).
// 그 외 (d >= 2 또는 d <= -1) 는 기존 formatDday 그대로.
function eventBadgeContent(ev, d) {
  if (ev.is_all_day) {
    if (d === 0) return { text: "오늘", multiLine: false };
    if (d === 1) return { text: "내일", multiLine: false };
    return { text: formatDday(d), multiLine: false };
  }
  if (d === 0 || d === 1) {
    const label = d === 0 ? "오늘" : "내일";
    const hhmm = new Date(ev.start_dt).toLocaleTimeString("ko-KR", {
      timeZone: KST_TZ, hour: "2-digit", minute: "2-digit", hour12: false,
    });
    return { text: `${label}\n${hhmm}`, multiLine: true };
  }
  return { text: formatDday(d), multiLine: false };
}

// 할일은 마감일이 항상 날짜 단위(종일 개념) — 시각이 없으므로 두 줄 분기 없이 단어/일수만.
function taskBadgeText(d) {
  if (d === 0) return "오늘";
  if (d === 1) return "내일";
  return formatDday(d);
}

function formatTimeRange(ev) {
  if (ev.is_all_day) return "종일";
  const opts = { timeZone: KST_TZ, hour: "2-digit", minute: "2-digit", hour12: false };
  const sH = new Date(ev.start_dt).toLocaleTimeString("ko-KR", opts);
  const eH = new Date(ev.end_dt).toLocaleTimeString("ko-KR", opts);
  return `${sH}–${eH}`;
}

function formatListDate(dayStr) {
  return new Date(dayStr + "T00:00:00+09:00").toLocaleDateString("ko-KR", {
    timeZone: KST_TZ, month: "numeric", day: "numeric", weekday: "short",
  });
}

function renderBanner(state) {
  const b = document.getElementById("banner");
  if (state.auth_required) {
    b.textContent = "Google 재인증 필요 · 로컬에서 --auth 실행 후 push_token.ps1";
    b.className = "banner error";
    b.hidden = false;
  } else if (state.last_error) {
    const msg = state.last_error.replace(/\s+/g, " ");
    const short = msg.length > 160 ? msg.slice(0, 160) + "…" : msg;
    b.textContent = `동기화 실패: ${short} · 마지막 캐시 표시`;
    b.className = "banner";
    b.hidden = false;
  } else if (!state.last_sync) {
    b.textContent = "초기 동기화 중…";
    b.className = "banner";
    b.hidden = false;
  } else {
    b.hidden = true;
    b.textContent = "";
  }
}

// 일정(events)과 할일(tasks)을 하나의 정렬된 목록으로 합친다.
// - 공휴일은 제외(달력 그리드 전용), 서브태스크(parent 있음)도 제외.
// - 마감 지난(overdue) 미완료 할일은 endDate 필터가 없으므로 자연 통과 → is-past 회색.
// - 마감 없는(무기한) 할일은 정렬 키가 없으니 맨 뒤로 모은다.
function buildListItems(state, today) {
  const events = (state.events || [])
    .filter(e => !e.is_holiday)
    .filter(e => endDate(e) >= today)
    .map(e => ({ kind: "event", sortKey: startDate(e), sortSub: e.start_dt, ev: e }));
  const tasks = (state.tasks || [])
    .filter(t => !t.parent)
    .map(t => ({ kind: "task", sortKey: t.due_date, sortSub: (t.due_date || "") + "T99", task: t }));

  const dated = [...events, ...tasks.filter(t => t.sortKey)]
    .sort((a, b) => a.sortKey.localeCompare(b.sortKey) || a.sortSub.localeCompare(b.sortSub));
  const undated = tasks.filter(t => !t.sortKey);
  return [...dated, ...undated].slice(0, 30);
}

function typePill(label, kind) {
  const p = document.createElement("span");
  p.className = `type-pill type-${kind}`;
  p.textContent = label;
  return p;
}

function renderEventRow(ev, today) {
  const row = document.createElement("div");
  row.className = "event-row is-event";
  const d = calcDday(startDate(ev), today);
  if (d === 0) row.classList.add("is-today");
  else if (d < 0) row.classList.add("is-past");

  const main = document.createElement("div");
  main.className = "main";
  const summaryRow = document.createElement("div");
  summaryRow.className = "summary-row";
  summaryRow.appendChild(typePill("일정", "event"));
  const summary = document.createElement("div");
  summary.className = "summary";
  summary.textContent = ev.summary || "(제목 없음)";
  summaryRow.appendChild(summary);
  main.appendChild(summaryRow);

  const when = document.createElement("div");
  when.className = "when";
  when.textContent = `${formatListDate(startDate(ev))} · ${formatTimeRange(ev)}`;
  main.appendChild(when);

  if (ev.location) {
    const loc = document.createElement("div");
    loc.className = "loc";
    loc.textContent = ev.location;
    main.appendChild(loc);
  }
  row.appendChild(main);

  const badge = document.createElement("div");
  badge.className = "dday-badge";
  const { text, multiLine } = eventBadgeContent(ev, d);
  badge.textContent = text;
  if (multiLine) badge.classList.add("with-time");
  row.appendChild(badge);

  return row;
}

function renderTaskRow(task, today) {
  const row = document.createElement("div");
  row.className = "event-row is-task";

  const main = document.createElement("div");
  main.className = "main";
  const summaryRow = document.createElement("div");
  summaryRow.className = "summary-row";
  summaryRow.appendChild(typePill("할일", "task"));
  const summary = document.createElement("div");
  summary.className = "summary";
  summary.textContent = task.title || "(제목 없음)";
  summaryRow.appendChild(summary);
  main.appendChild(summaryRow);

  const when = document.createElement("div");
  when.className = "when";
  const badge = document.createElement("div");
  badge.className = "dday-badge";

  if (task.due_date) {
    when.textContent = formatListDate(task.due_date);
    const d = calcDday(task.due_date, today);
    if (d === 0) row.classList.add("is-today");
    else if (d < 0) row.classList.add("is-past");
    badge.textContent = taskBadgeText(d);
  } else {
    when.textContent = "마감 없음";
    badge.textContent = "—";
    badge.classList.add("is-undated");
  }
  main.appendChild(when);
  row.appendChild(main);
  row.appendChild(badge);

  return row;
}

function renderEventList(state, today) {
  const list = document.getElementById("event-list");
  list.innerHTML = "";
  const items = buildListItems(state, today);
  if (items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = state.last_sync ? "향후 일정·할일이 없다" : "로딩 중…";
    list.appendChild(empty);
    return;
  }
  items.forEach(item => {
    const row = item.kind === "event"
      ? renderEventRow(item.ev, today)
      : renderTaskRow(item.task, today);
    list.appendChild(row);
  });
}

function weekdayMondayBased(year, month) {
  // JS getDay(): 일요일=0. 월요일=0 으로 정규화 (한국 캘린더 관례).
  return (new Date(year, month - 1, 1).getDay() + 6) % 7;
}

// 셀의 점/배지 인디케이터. count<=3 이면 점, 그 이상이면 숫자 배지.
// dotClass/badgeClass 로 일정(초록 기본)과 할일(보라 .dot-task/.badge-task)을 구분.
function appendIndicator(container, count, dotClass, badgeClass) {
  if (count <= 0) return;
  if (count <= 3) {
    const wrap = document.createElement("div");
    wrap.className = "dots";
    for (let k = 0; k < count; k++) {
      const dot = document.createElement("span");
      dot.className = dotClass ? `dot ${dotClass}` : "dot";
      wrap.appendChild(dot);
    }
    container.appendChild(wrap);
  } else {
    const badge = document.createElement("div");
    badge.className = badgeClass ? `badge ${badgeClass}` : "badge";
    badge.textContent = `${count}`;
    container.appendChild(badge);
  }
}

function renderMonth(state, today) {
  const { year, month } = currentMonth;
  document.getElementById("month-title").textContent = `${year}년 ${month}월`;
  const grid = document.getElementById("month-grid");
  grid.innerHTML = "";
  const firstWd = weekdayMondayBased(year, month);
  const startCalDate = new Date(year, month - 1, 1 - firstWd);

  for (let i = 0; i < 42; i++) {
    const d = new Date(startCalDate);
    d.setDate(startCalDate.getDate() + i);
    const dy = d.getFullYear();
    const dm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const dayStr = `${dy}-${dm}-${dd}`;
    const cell = document.createElement("div");
    cell.className = "cell";
    const weekday = d.getDay(); // 0=일, 1=월, ..., 6=토
    if (weekday === 0) cell.classList.add("sunday");
    else if (weekday === 6) cell.classList.add("saturday");
    const inMonth = dy === year && (d.getMonth() + 1) === month;
    if (!inMonth) cell.classList.add("outside");
    if (dayStr === today) cell.classList.add("today");
    const num = document.createElement("div");
    num.className = "num";
    num.textContent = d.getDate();
    cell.appendChild(num);

    if (inMonth) {
      // 공휴일은 셀에 클래스 + 이름 배지로 표시 (이 달 셀만).
      const holiday = (state.events || []).find(e =>
        e.is_holiday && startDate(e) <= dayStr && endDate(e) > dayStr
      );
      if (holiday) {
        cell.classList.add("holiday");
        const hname = document.createElement("div");
        hname.className = "holiday-name";
        hname.textContent = holiday.summary;
        cell.appendChild(hname);
      }
      // 일반 이벤트 카운트 — 공휴일은 제외하여 dot/배지에 섞이지 않도록.
      const eventCount = (state.events || []).reduce((acc, e) => {
        if (e.is_holiday) return acc;
        if (e.is_all_day) {
          return acc + (startDate(e) <= dayStr && endDate(e) > dayStr ? 1 : 0);
        }
        return acc + (startDate(e) === dayStr ? 1 : 0);
      }, 0);
      // 할일 카운트 — 마감일이 그 날이고 서브태스크가 아닌 것.
      const taskCount = (state.tasks || []).reduce((acc, t) =>
        acc + (!t.parent && t.due_date === dayStr ? 1 : 0), 0);

      const indicators = document.createElement("div");
      indicators.className = "indicators";
      appendIndicator(indicators, eventCount, "", "");
      appendIndicator(indicators, taskCount, "dot-task", "badge-task");
      if (indicators.children.length) cell.appendChild(indicators);
    }
    grid.appendChild(cell);
  }
}

function changeMonth(delta) {
  let { year, month } = currentMonth;
  month += delta;
  while (month > 12) { month -= 12; year += 1; }
  while (month < 1) { month += 12; year -= 1; }
  currentMonth = { year, month };
  if (stateCache) renderAll(stateCache);
}

function renderAll(state) {
  stateCache = state;
  const today = kstTodayString();
  renderBanner(state);
  renderEventList(state, today);
  renderMonth(state, today);
}

async function poll() {
  try {
    const resp = await fetch("/api/state", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    renderAll(await resp.json());
  } catch (e) {
    const b = document.getElementById("banner");
    b.textContent = `서버 연결 실패: ${e.message || e}`;
    b.className = "banner error";
    b.hidden = false;
  }
}

document.getElementById("prev").addEventListener("click", () => changeMonth(-1));
document.getElementById("next").addEventListener("click", () => changeMonth(+1));

poll();
setInterval(poll, REFRESH_MS);
