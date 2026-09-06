"use strict";

// Rust 키오스크가 직접 포함하는 운영 UI 동작

// 캘린더는 백엔드 캐시를 사용하고 PC 시스템 지표는 2초마다 새로 표시한다.
const REFRESH_MS = 2 * 1000;
const KST_TZ = "Asia/Seoul";
const RING_ROTATION_STEP = 5;
const RING_ANIMATION_MS = 800;
const WORKSPACE_TRANSITION_MS = 400;
const OUTER_HEX_POINTS = [
  [52, 4], [78, 4], [101, 45], [78, 86], [26, 86], [3, 45], [26, 4],
];
const INNER_HEX_POINTS = [
  [52, 14], [31, 14], [13, 45], [31, 76], [73, 76], [91, 45], [73, 14],
];

let stateCache = null;
let calendarSignature = null;
let monitorTopologySignature = null;
let monitorSampleSignature = null;
let monitorDevices = [];
let selectedMonitorHostname = null;
let ringRotationPhase = 0;
const ringAnimationState = new WeakMap();
let calendarTransitionSequence = 0;
let calendarTransitionTimer = null;
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

// 할일은 마감일이 항상 날짜 단위(종일 개념): 시각이 없으므로 두 줄 분기 없이 단어/일수만.
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
  return `${sH} - ${eH}`;
}

function formatNumber(value, digits = 1) {
  return Number.isFinite(value) ? Number(value).toFixed(digits) : "--";
}

function formatUptime(seconds) {
  if (!Number.isFinite(seconds)) return "가동 시간 없음";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return days > 0 ? `가동 ${days}일 ${hours}시간` : `가동 ${hours}시간`;
}

function cleanHardwareName(value) {
  return String(value || "")
    .replace(/\((?:R|TM)\)/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function limitHardwareName(value, limit = 30) {
  if (value.length <= limit) return value;
  return `${value.slice(0, limit - 1).trimEnd()}…`;
}

function shortCpuName(value) {
  const original = cleanHardwareName(value) || "CPU";
  const compact = original
    .replace(/^(?:\d+(?:st|nd|rd|th)\s+Gen\s+)?Intel\s+/i, "")
    .replace(/^AMD\s+/i, "")
    .replace(/\s+CPU(?:\s+@\s+[\d.]+\s*GHz)?$/i, "")
    .replace(/\s+\d+-Core\s+Processor$/i, "")
    .replace(/\s+Processor$/i, "")
    .trim();
  return limitHardwareName(compact || original);
}

function shortGpuName(value) {
  const original = cleanHardwareName(value) || "GPU";
  const compact = original
    .replace(/^NVIDIA\s+(?:GeForce\s+)?/i, "")
    .replace(/^AMD\s+/i, "")
    .replace(/^Intel\s+/i, "")
    .replace(/\s+Laptop\s+GPU$/i, " Laptop")
    .replace(/\s+Graphics$/i, "")
    .replace(/\s+GPU$/i, "")
    .trim();
  return limitHardwareName(compact || original);
}

function safePercent(value) {
  return Number.isFinite(value) ? Math.max(0, Math.min(100, Number(value))) : 0;
}

function closedHexPath(points) {
  return points.map(([x, y], index) => `${index === 0 ? "M" : "L"} ${x} ${y}`).join(" ") + " Z";
}

function hexSegmentLengths(points) {
  return points.map(([x1, y1], index) => {
    const [x2, y2] = points[(index + 1) % points.length];
    return Math.hypot(x2 - x1, y2 - y1);
  });
}

function partialHexPath(points, phase, percent) {
  const lengths = hexSegmentLengths(points);
  const perimeter = lengths.reduce((total, length) => total + length, 0);
  let distance = (((phase % 100) + 100) % 100) / 100 * perimeter;
  let segmentIndex = 0;
  while (segmentIndex < lengths.length - 1 && distance >= lengths[segmentIndex]) {
    distance -= lengths[segmentIndex];
    segmentIndex += 1;
  }
  const [x1, y1] = points[segmentIndex];
  const [x2, y2] = points[(segmentIndex + 1) % points.length];
  const ratio = lengths[segmentIndex] > 0 ? distance / lengths[segmentIndex] : 0;
  const start = [x1 + (x2 - x1) * ratio, y1 + (y2 - y1) * ratio];
  const ordered = [start];
  let distanceInSegment = distance;
  let remaining = perimeter * safePercent(percent) / 100;
  let guard = 0;
  while (remaining > 0.000001 && guard < points.length + 2) {
    const available = lengths[segmentIndex] - distanceInSegment;
    const travel = Math.min(remaining, available);
    const [segmentX1, segmentY1] = points[segmentIndex];
    const [segmentX2, segmentY2] = points[(segmentIndex + 1) % points.length];
    const endRatio = (distanceInSegment + travel) / lengths[segmentIndex];
    ordered.push([
      segmentX1 + (segmentX2 - segmentX1) * endRatio,
      segmentY1 + (segmentY2 - segmentY1) * endRatio,
    ]);
    remaining -= travel;
    if (travel >= available - 0.000001) {
      segmentIndex = (segmentIndex + 1) % points.length;
      distanceInSegment = 0;
    } else {
      distanceInSegment += travel;
    }
    guard += 1;
  }
  return ordered.map(([x, y], index) =>
    `${index === 0 ? "M" : "L"} ${x.toFixed(3)} ${y.toFixed(3)}`
  ).join(" ");
}

function makeSvgPath(className, pathData, value = null) {
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("class", className);
  path.setAttribute("d", pathData);
  if (value !== null) {
    const percent = safePercent(value);
    path.style.opacity = percent > 0 ? "1" : "0";
  }
  return path;
}

function createHexCard(id, kind, dual, primaryLabel, secondaryLabel = "") {
  const card = document.createElement("article");
  card.id = `hex-card-${id}`;
  card.className = `hex-card ${kind}`;

  const name = document.createElement("div");
  name.id = `hex-name-${id}`;
  name.className = "hex-name";
  card.appendChild(name);

  const temperature = document.createElement("div");
  temperature.id = `hex-temp-${id}`;
  temperature.className = "hex-temp";
  card.appendChild(temperature);

  const gauge = document.createElement("div");
  gauge.className = "hex-gauge";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "hex-svg");
  svg.setAttribute("viewBox", "0 0 104 90");
  svg.setAttribute("aria-hidden", "true");
  const outerPath = closedHexPath(OUTER_HEX_POINTS);
  const innerPath = closedHexPath(INNER_HEX_POINTS);
  svg.appendChild(makeSvgPath("hex-track outer-track", outerPath));
  const outer = makeSvgPath("hex-progress outer-progress", partialHexPath(OUTER_HEX_POINTS, 0, 0), 0);
  outer.id = `hex-outer-${id}`;
  svg.appendChild(outer);
  if (dual) {
    svg.appendChild(makeSvgPath("hex-track inner-track", innerPath));
    const inner = makeSvgPath("hex-progress inner-progress", partialHexPath(INNER_HEX_POINTS, 0, 0), 0);
    inner.id = `hex-inner-${id}`;
    svg.appendChild(inner);
  }
  gauge.appendChild(svg);

  const center = document.createElement("div");
  center.className = "hex-center";
  const colors = kind === "cpu"
    ? ["var(--cpu)", "var(--ram)"]
    : kind === "gpu"
      ? ["var(--gpu)", "var(--vram)"]
      : ["var(--disk)"];
  const primaryLegend = document.createElement("strong");
  primaryLegend.className = "hex-center-label";
  primaryLegend.style.color = colors[0];
  primaryLegend.textContent = primaryLabel;
  center.appendChild(primaryLegend);
  if (secondaryLabel) {
    const secondaryLegend = document.createElement("strong");
    secondaryLegend.className = "hex-center-label";
    secondaryLegend.style.color = colors[1];
    secondaryLegend.textContent = secondaryLabel;
    center.appendChild(secondaryLegend);
  }
  gauge.appendChild(center);
  card.appendChild(gauge);
  return card;
}

function topologyDevices(data) {
  const gpus = Array.isArray(data.gpus) && data.gpus.length
    ? data.gpus
    : Number.isFinite(data.gpu_percent)
      ? [{
          index: 0,
          name: "GPU",
          utilization_percent: data.gpu_percent,
          vram_percent: data.gpu_memory_percent,
          vram_used_gb: data.gpu_memory_used_gb,
          vram_total_gb: data.gpu_memory_total_gb,
          temperature_c: data.gpu_temperature_c,
        }]
      : [];
  const disks = Array.isArray(data.disks) && data.disks.length
    ? data.disks
    : Number.isFinite(data.disk_percent)
      ? [{
          name: "시스템 디스크",
          mountpoint: "",
          usage_percent: data.disk_percent,
          used_gb: data.disk_used_gb,
          total_gb: data.disk_total_gb,
          temperature_c: null,
        }]
      : [];
  return { gpus, disks };
}

function rebuildMonitorCards(data, gpus, disks) {
  const grid = document.getElementById("monitor-devices");
  grid.replaceChildren();
  grid.appendChild(createHexCard("cpu", "cpu", true, "CPU", "RAM"));
  gpus.forEach(gpu => {
    grid.appendChild(createHexCard(`gpu-${gpu.index}`, "gpu", true, "GPU", "VRAM"));
  });
  disks.forEach((disk, index) => {
    const driveLabel = disk.name || disk.mountpoint || `Disk ${index + 1}`;
    grid.appendChild(createHexCard(`disk-${index}`, "disk", false, driveLabel));
  });
  monitorTopologySignature = JSON.stringify({
    cpu: data.cpu_name || "CPU",
    gpus: gpus.map(gpu => [gpu.index, gpu.name]),
    disks: disks.map(disk => [disk.name, disk.mountpoint]),
  });
}

function setRingProgress(element, value, rotationPhase, points) {
  const targetPercent = safePercent(value);
  const previous = ringAnimationState.get(element);
  if (!previous) {
    element.setAttribute("d", partialHexPath(points, rotationPhase, targetPercent));
    element.style.opacity = targetPercent > 0 ? "1" : "0";
    ringAnimationState.set(element, {
      frame: null,
      percent: targetPercent,
      phase: rotationPhase,
    });
    return;
  }
  if (previous.frame !== null) cancelAnimationFrame(previous.frame);
  const startPercent = previous.percent;
  const startPhase = previous.phase;
  if (startPercent === targetPercent && startPhase === rotationPhase) return;
  const startedAt = performance.now();
  const animation = {
    frame: null,
    percent: startPercent,
    phase: startPhase,
  };
  ringAnimationState.set(element, animation);
  const drawFrame = timestamp => {
    const elapsed = Math.min(1, (timestamp - startedAt) / RING_ANIMATION_MS);
    const eased = 1 - Math.pow(1 - elapsed, 3);
    animation.percent = startPercent + (targetPercent - startPercent) * eased;
    animation.phase = startPhase + (rotationPhase - startPhase) * eased;
    element.setAttribute("d", partialHexPath(points, animation.phase, animation.percent));
    element.style.opacity = animation.percent > 0.001 ? "1" : "0";
    if (elapsed < 1) {
      animation.frame = requestAnimationFrame(drawFrame);
    } else {
      animation.frame = null;
      animation.percent = targetPercent;
      animation.phase = rotationPhase;
    }
  };
  animation.frame = requestAnimationFrame(drawFrame);
}

function setHexCard(id, {
  name, fullName = name, temperature, primary, secondary, hideTemperature = false,
}) {
  const nameElement = document.getElementById(`hex-name-${id}`);
  if (!nameElement) return;
  nameElement.textContent = name;
  nameElement.title = fullName;
  const temperatureElement = document.getElementById(`hex-temp-${id}`);
  const showTemperature = !hideTemperature && Number.isFinite(temperature);
  temperatureElement.hidden = !showTemperature;
  temperatureElement.textContent = showTemperature ? `${Math.round(temperature)}℃` : "";
  nameElement.parentElement.classList.toggle("has-temperature", showTemperature);
  setRingProgress(
    document.getElementById(`hex-outer-${id}`), primary, ringRotationPhase, OUTER_HEX_POINTS
  );
  const inner = document.getElementById(`hex-inner-${id}`);
  if (inner) setRingProgress(inner, secondary, ringRotationPhase, INNER_HEX_POINTS);
}

function renderSystemMonitor(monitor) {
  const data = monitor || {};
  const online = data.available === true;
  const sampleSignature = data.captured_at || JSON.stringify([
    data.cpu_percent,
    data.memory_percent,
    data.gpu_percent,
    data.gpu_memory_percent,
    data.disk_percent,
  ]);
  if (monitorSampleSignature !== null && sampleSignature !== monitorSampleSignature) {
    ringRotationPhase += RING_ROTATION_STEP;
  }
  monitorSampleSignature = sampleSignature;
  const status = document.getElementById("monitor-status");
  status.textContent = online ? "ONLINE" : "OFFLINE";
  status.className = `status-pill ${online ? "online" : "offline"}`;
  document.getElementById("monitor-host").textContent = data.hostname || "PC 연결 대기";

  const { gpus, disks } = topologyDevices(data);
  const topology = JSON.stringify({
    cpu: data.cpu_name || "CPU",
    gpus: gpus.map(gpu => [gpu.index, gpu.name]),
    disks: disks.map(disk => [disk.name, disk.mountpoint]),
  });
  if (topology !== monitorTopologySignature) rebuildMonitorCards(data, gpus, disks);

  setHexCard("cpu", {
    name: shortCpuName(data.cpu_name || "CPU · RAM"),
    fullName: data.cpu_name || "CPU · RAM",
    temperature: data.cpu_temperature_c,
    primary: data.cpu_percent,
    secondary: data.memory_percent,
  });
  gpus.forEach(gpu => {
    setHexCard(`gpu-${gpu.index}`, {
      name: shortGpuName(gpu.name),
      fullName: gpu.name || "GPU",
      temperature: gpu.temperature_c,
      primary: gpu.utilization_percent,
      secondary: gpu.vram_percent,
    });
  });
  disks.forEach((disk, index) => {
    setHexCard(`disk-${index}`, {
      name: disk.name || disk.mountpoint || `Disk ${index + 1}`,
      temperature: null,
      primary: disk.usage_percent,
      secondary: null,
      hideTemperature: true,
    });
  });

  document.getElementById("monitor-network").textContent =
    `↓ ${formatNumber(data.network_rx_mbps, 2)} · ↑ ${formatNumber(data.network_tx_mbps, 2)} Mbps`;
  let updated = formatUptime(data.uptime_seconds);
  if (data.captured_at) {
    const date = new Date(data.captured_at);
    if (!Number.isNaN(date.getTime())) {
      const timeText = date.toLocaleTimeString("ko-KR", {
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
      });
      updated = online ? `${timeText} 갱신` : `${timeText} 이후 중단`;
    }
  }
  document.getElementById("monitor-updated").textContent = updated;
}

function monitorKey(monitor) {
  return String((monitor || {}).hostname || "").toLocaleLowerCase("en-US");
}

function renderMonitorSelector() {
  const button = document.getElementById("monitor-next");
  const position = document.getElementById("monitor-position");
  const label = document.getElementById("monitor-next-label");
  if (monitorDevices.length === 0) {
    button.disabled = true;
    position.textContent = "장비 0/0";
    label.textContent = "다음 장비 없음";
    return;
  }
  const selectedIndex = Math.max(0, monitorDevices.findIndex(
    monitor => monitorKey(monitor) === selectedMonitorHostname
  ));
  const nextIndex = (selectedIndex + 1) % monitorDevices.length;
  button.disabled = monitorDevices.length < 2;
  position.textContent = `장비 ${selectedIndex + 1}/${monitorDevices.length}`;
  label.textContent = monitorDevices.length < 2
    ? "연결된 장비 1대"
    : `다음 장비: ${monitorDevices[nextIndex].hostname}`;
}

function renderSystemMonitors(state) {
  const monitors = Array.isArray(state.system_monitors) && state.system_monitors.length
    ? state.system_monitors
    : state.system_monitor && state.system_monitor.hostname
      ? [state.system_monitor]
      : [];
  monitorDevices = [...monitors].sort((left, right) => {
    const leftOrder = Number.isInteger(left.display_order) ? left.display_order : 1001;
    const rightOrder = Number.isInteger(right.display_order) ? right.display_order : 1001;
    return leftOrder - rightOrder
      || monitorKey(left).localeCompare(monitorKey(right), "en-US");
  });
  if (!monitorDevices.some(monitor => monitorKey(monitor) === selectedMonitorHostname)) {
    selectedMonitorHostname = monitorDevices.length ? monitorKey(monitorDevices[0]) : null;
    monitorTopologySignature = null;
    monitorSampleSignature = null;
  }
  const selected = monitorDevices.find(
    monitor => monitorKey(monitor) === selectedMonitorHostname
  );
  renderSystemMonitor(selected || {});
  renderMonitorSelector();
}

function selectNextMonitor() {
  if (monitorDevices.length < 2) return;
  const currentIndex = monitorDevices.findIndex(
    monitor => monitorKey(monitor) === selectedMonitorHostname
  );
  const nextIndex = (Math.max(0, currentIndex) + 1) % monitorDevices.length;
  selectedMonitorHostname = monitorKey(monitorDevices[nextIndex]);
  monitorTopologySignature = null;
  monitorSampleSignature = null;
  renderSystemMonitor(monitorDevices[nextIndex]);
  renderMonitorSelector();
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

function renderEventRow(ev, today) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "event-row is-event";
  row.setAttribute("aria-label", `${ev.summary || "제목 없음"} 상세 보기`);
  row.addEventListener("click", () => openEventDetail(ev));
  const d = calcDday(startDate(ev), today);
  if (d === 0) row.classList.add("is-today");
  else if (d < 0) row.classList.add("is-past");

  const main = document.createElement("div");
  main.className = "main";
  const summary = document.createElement("div");
  summary.className = "summary";
  summary.textContent = ev.summary || "(제목 없음)";
  main.appendChild(summary);

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

function formatTodayHeading() {
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: KST_TZ,
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date()).replace(/,/g, "");
}

function openEventDetail(ev) {
  hideCalendarImmediately();
  document.getElementById("detail-title").textContent = ev.summary || "(제목 없음)";
  document.getElementById("detail-when").textContent =
    `${formatListDate(startDate(ev))} · ${formatTimeRange(ev)}`;
  const locationRow = document.getElementById("detail-location-row");
  const location = (ev.location || "").trim();
  locationRow.hidden = !location;
  document.getElementById("detail-location").textContent = location;
  const description = (ev.description || "").trim();
  document.getElementById("detail-description").textContent = description || "상세 설명 없음";
  document.getElementById("event-detail").hidden = false;
  document.querySelector(".detail-scroll").scrollTop = 0;
  document.getElementById("detail-close").focus();
}

function closeEventDetail() {
  document.getElementById("event-detail").hidden = true;
}

function hideCalendarImmediately() {
  calendarTransitionSequence += 1;
  if (calendarTransitionTimer !== null) clearTimeout(calendarTransitionTimer);
  calendarTransitionTimer = null;
  const calendar = document.getElementById("calendar");
  calendar.onanimationend = null;
  calendar.classList.remove("view-entering", "view-leaving");
  calendar.hidden = true;
  calendar.setAttribute("aria-hidden", "true");
}

function transitionCalendar(visible) {
  const calendar = document.getElementById("calendar");
  const sequence = ++calendarTransitionSequence;
  if (calendarTransitionTimer !== null) clearTimeout(calendarTransitionTimer);
  calendar.onanimationend = null;
  calendar.classList.remove("view-entering", "view-leaving");
  if (visible) calendar.hidden = false;
  calendar.setAttribute("aria-hidden", visible ? "false" : "true");
  calendar.getBoundingClientRect();
  calendar.classList.add(visible ? "view-entering" : "view-leaving");

  const finish = () => {
    if (sequence !== calendarTransitionSequence) return;
    if (calendarTransitionTimer !== null) clearTimeout(calendarTransitionTimer);
    calendarTransitionTimer = null;
    calendar.onanimationend = null;
    calendar.classList.remove("view-entering", "view-leaving");
    if (!visible) calendar.hidden = true;
  };
  calendar.onanimationend = finish;
  calendarTransitionTimer = setTimeout(finish, WORKSPACE_TRANSITION_MS);
}

function openCalendar() {
  const today = kstTodayString();
  const [year, month] = today.split("-").map(Number);
  currentMonth = { year, month };
  if (stateCache) renderMonth(stateCache, today);
  document.getElementById("event-detail").hidden = true;
  transitionCalendar(true);
}

function closeCalendar() {
  transitionCalendar(false);
}

function renderTaskRow(task, today) {
  const row = document.createElement("div");
  row.className = "event-row is-task";

  const main = document.createElement("div");
  main.className = "main";
  const summary = document.createElement("div");
  summary.className = "summary";
  summary.textContent = task.title || "(제목 없음)";
  main.appendChild(summary);

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
    badge.textContent = "-";
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
      // 일반 이벤트 카운트: 공휴일은 제외하여 dot/배지에 섞이지 않도록 처리.
      const eventCount = (state.events || []).reduce((acc, e) => {
        if (e.is_holiday) return acc;
        if (e.is_all_day) {
          return acc + (startDate(e) <= dayStr && endDate(e) > dayStr ? 1 : 0);
        }
        return acc + (startDate(e) === dayStr ? 1 : 0);
      }, 0);
      // 할일 카운트: 마감일이 그 날이고 서브태스크가 아닌 것.
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
  if (stateCache) renderMonth(stateCache, kstTodayString());
}

function getCalendarSignature(state) {
  return JSON.stringify({
    last_sync: state.last_sync,
    events: (state.events || []).map(e => [e.id, e.summary, e.start_dt, e.end_dt, e.location, e.description]),
    tasks: (state.tasks || []).map(t => [t.id, t.title, t.due_date, t.notes, t.parent]),
  });
}

function renderAll(state) {
  stateCache = state;
  renderBanner(state);
  renderSystemMonitors(state);
  document.getElementById("today-date").textContent = formatTodayHeading();
  const nextSignature = getCalendarSignature(state);
  if (nextSignature !== calendarSignature) {
    const today = kstTodayString();
    renderEventList(state, today);
    renderMonth(state, today);
    calendarSignature = nextSignature;
  }
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
document.getElementById("detail-close").addEventListener("click", closeEventDetail);
document.getElementById("calendar-open").addEventListener("click", openCalendar);
document.getElementById("calendar-close").addEventListener("click", closeCalendar);
document.getElementById("monitor-next").addEventListener("click", selectNextMonitor);

poll();
setInterval(poll, REFRESH_MS);
