const $ = (selector) => document.querySelector(selector);
const state = {
  bootstrap: null,
  selectedPreset: null,
  busy: false,
  detailProjectId: null,
  gallery: {
    offset: 0,
    total: 0,
    loading: false,
    current: null,
    selected: new Set(),
    items: [],
    range: null,
    filter: { start: "", end: "" },
    filterMode: "interval",
  },
  cameraTune: { open: false, dirty: false, applying: false, previewTimer: null, previewBusy: false },
  mainPreview: { timer: null, busy: false },
  latestPhotoPath: null,
};
const modeNames = { sky: "天空", grow: "植物", life: "日常起居" };
const statusNames = {
  idle: "空闲",
  starting: "启动中",
  capturing: "拍摄中",
  waiting: "等待窗口",
  paused: "已暂停",
  error: "错误",
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch (_) {}
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return response.json();
}

function notice(message, error = false, sticky = false) {
  const el = $("#notice");
  el.textContent = message;
  el.classList.remove("hidden");
  el.style.borderColor = error ? "#995044" : "";
  clearTimeout(notice.timer);
  if (!sticky) {
    notice.timer = setTimeout(() => el.classList.add("hidden"), 5000);
  }
}

function formatAgo(value) {
  if (!value) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value)) / 1000));
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  return `${Math.floor(seconds / 3600)} 小时前`;
}

function formatInterval(seconds) {
  if (!seconds) return "—";
  if (seconds >= 3600) return `${seconds / 3600} 小时`;
  if (seconds >= 60) return `${seconds / 60} 分钟`;
  return `${seconds} 秒`;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatScheduleTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function formatCountdown(value) {
  const ms = new Date(value).getTime() - Date.now();
  if (!Number.isFinite(ms) || ms <= 0) return "即将开始";
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `还剩 ${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `还剩 ${minutes} 分 ${seconds} 秒`;
  return `还剩 ${seconds} 秒`;
}

function defaultScheduleValue(offsetMinutes = 10) {
  const date = new Date(Date.now() + offsetMinutes * 60 * 1000);
  date.setSeconds(0, 0);
  return formatDateTimeLocal(date);
}

function updateScheduleHint(runtime) {
  const bar = $("#scheduleBar");
  const startAt = runtime?.scheduled_start_at;
  const stopAt = runtime?.scheduled_stop_at;
  bar.classList.toggle("hidden", !startAt && !stopAt);
  if (startAt && stopAt) {
    $("#scheduleHint").textContent =
      `将于 ${formatScheduleTime(startAt)} 开拍（${formatCountdown(startAt)}），${formatScheduleTime(stopAt)} 停止`;
  } else if (startAt) {
    $("#scheduleHint").textContent = `将于 ${formatScheduleTime(startAt)} 自动开拍（${formatCountdown(startAt)}），需手动停止`;
  } else if (stopAt) {
    $("#scheduleHint").textContent = `将于 ${formatScheduleTime(stopAt)} 自动停止（${formatCountdown(stopAt)}）`;
  }
}

function setPreview(url = "/api/preview.jpg") {
  const image = $("#preview");
  return new Promise((resolve) => {
    image.onload = () => {
      image.classList.add("ready");
      $("#viewerEmpty").classList.add("hidden");
      resolve(true);
    };
    image.onerror = () => {
      image.classList.remove("ready");
      $("#viewerEmpty").classList.remove("hidden");
      resolve(false);
    };
    image.src = `${url}${url.includes("?") ? "&" : "?"}t=${Date.now()}`;
  });
}

function updateLatestPhoto(runtime) {
  const image = $("#latestPhotoThumb");
  const empty = $("#latestPhotoEmpty");
  if (!image || !empty) return;
  const path = runtime?.last_frame || null;
  if (!path) {
    state.latestPhotoPath = null;
    image.removeAttribute("src");
    image.classList.remove("ready");
    empty.classList.remove("hidden");
    return;
  }
  if (state.latestPhotoPath === path && image.classList.contains("ready")) return;
  state.latestPhotoPath = path;
  image.onload = () => {
    image.classList.add("ready");
    empty.classList.add("hidden");
  };
  image.onerror = () => {
    image.classList.remove("ready");
    empty.classList.remove("hidden");
  };
  image.src = `/api/latest-thumbnail.jpg?t=${Date.now()}`;
}

function startMainPreviewLoop() {
  if (state.mainPreview.timer) clearTimeout(state.mainPreview.timer);
  const tick = async () => {
    if (!state.cameraTune.open && !state.mainPreview.busy) {
      state.mainPreview.busy = true;
      try {
        await setPreview();
      } finally {
        state.mainPreview.busy = false;
      }
    }
    const active = ["starting", "capturing", "waiting", "paused"].includes(
      state.bootstrap?.runtime?.status
    );
    state.mainPreview.timer = setTimeout(tick, active ? 2500 : 1400);
  };
  tick();
}

function setTunePreview(url = "/api/preview.jpg") {
  const image = $("#tunePreview");
  const empty = $("#tuneViewerEmpty");
  if (!image) return;
  image.onload = () => {
    image.classList.add("ready");
    empty?.classList.add("hidden");
  };
  image.onerror = () => {
    image.classList.remove("ready");
    if (empty) {
      empty.classList.remove("hidden");
      empty.textContent = "暂无实时画面，请点刷新";
    }
  };
  image.src = `${url}${url.includes("?") ? "&" : "?"}t=${Date.now()}`;
}

function stopTunePreviewLoop() {
  if (state.cameraTune.previewTimer) {
    clearTimeout(state.cameraTune.previewTimer);
    state.cameraTune.previewTimer = null;
  }
}

async function refreshTuneLiveMeta() {
  if (!state.cameraTune.open) return;
  try {
    const tune = await api("/api/camera/tune");
    renderLiveMeta(tune.live);
    state.cameraTune.dirty = Boolean(tune.dirty);
    $("#cameraTuneStatus").textContent = tune.dirty
      ? "已实时应用到相机，尚未写入当前相机设置"
      : "与当前相机设置一致";
  } catch (_) {}
}

async function refreshTunePreview() {
  if (!state.cameraTune.open || state.cameraTune.previewBusy) return;
  state.cameraTune.previewBusy = true;
  try {
    setTunePreview();
    await refreshTuneLiveMeta();
  } finally {
    state.cameraTune.previewBusy = false;
  }
}

function startTunePreviewLoop() {
  stopTunePreviewLoop();
  const tick = async () => {
    if (!state.cameraTune.open) return;
    if (!state.cameraTune.applying && !state.busy) {
      await refreshTunePreview();
    }
    state.cameraTune.previewTimer = setTimeout(tick, 1400);
  };
  tick();
}

function formatMemory(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${(bytes / (1024 * 1024)).toFixed(0)} MB`;
}

function applyRuntime(runtime, encode, system) {
  if (state.bootstrap) {
    state.bootstrap.runtime = runtime;
    if (encode) state.bootstrap.encode = encode;
    if (system) state.bootstrap.system = system;
  }
  render(runtime, encode || state.bootstrap?.encode || { status: "idle" }, system || state.bootstrap?.system);
}

function renderSystem(system) {
  const cpu = system?.cpu_percent;
  const temp = system?.cpu_temp_c;
  const used = system?.memory_used_bytes;
  const total = system?.memory_total_bytes;
  const percent = system?.memory_percent;
  $("#cpuUsage").textContent = Number.isFinite(cpu) ? `${cpu}%` : "—";
  $("#cpuTemp").textContent = Number.isFinite(temp) ? `${temp} °C` : "—";
  $("#memUsage").textContent =
    Number.isFinite(used) && Number.isFinite(total) ? `${formatMemory(used)} / ${formatMemory(total)}` : "—";
  $("#memPercent").textContent = Number.isFinite(percent) ? `${percent}%` : "—";
}

function updateLiveRotationHint(project) {
  const rotationHint = $("#liveRotationHint");
  if (!rotationHint) return;
  const rotation = Number(project?.camera?.rotation) === 180 ? 180 : 0;
  rotationHint.textContent = rotation ? `已旋转 ${rotation}°` : "未旋转";
}

function render(runtime, encode, system) {
  const project = runtime.project;
  const active = ["capturing", "waiting", "starting"].includes(runtime.status);
  const canOperate = Boolean(project) && !state.busy;
  $("#projectName").textContent = project?.name || "尚未创建项目";
  $("#projectMeta").textContent = project
    ? `${modeNames[project.mode]} · ${project.preset} · ${project.project_id}`
    : "选择一个拍摄方案，开始记录时间。";
  updateLiveRotationHint(project);
  $("#frameCount").textContent = Number(runtime.frames_total || 0).toLocaleString();
  $("#interval").textContent = formatInterval(project?.capture.interval_sec);
  const windowLabel = runtime.window_label || "—";
  $("#captureWindow").textContent =
    runtime.status === "waiting"
      ? `${windowLabel}（等待中）`
      : runtime.window_open === false && project
        ? `${windowLabel}（当前关闭）`
        : windowLabel;
  $("#lastCapture").textContent = formatAgo(runtime.last_ok_at);
  $("#freeSpace").textContent = runtime.storage ? `${runtime.storage.free_gb} GB` : "—";
  $("#storagePath").textContent = runtime.storage?.root || "未配置存储位置";
  $("#storageUsed").style.width = `${runtime.storage?.used_percent || 0}%`;
  updateLatestPhoto(runtime);

  const pill = $("#statusPill");
  const scheduled = Boolean(runtime.scheduled_start_at) && runtime.status === "idle";
  let statusText = statusNames[runtime.status] || runtime.status;
  if (runtime.status === "waiting" && runtime.window_label) {
    statusText = `等待窗口 ${runtime.window_label}`;
  } else if (scheduled) {
    statusText = "定时等待";
  }
  pill.querySelector("span").textContent = statusText;
  pill.className = `status-pill ${active ? "live" : ""} ${runtime.status === "error" ? "error" : ""} ${scheduled ? "scheduled" : ""}`;
  const idle = runtime.status === "idle" || runtime.status === "error";
  $("#startButton").disabled = !canOperate || active || state.cameraTune.open;
  $("#scheduleButton").disabled =
    !canOperate || active || runtime.status === "paused" || state.cameraTune.open;
  $("#pauseButton").disabled = !canOperate || (!active && runtime.status !== "paused");
  $("#pauseButton").textContent = runtime.status === "paused" ? "继续" : "暂停";
  $("#pauseButton").dataset.action = runtime.status === "paused" ? "resume" : "pause";
  $("#stopButton").disabled = !canOperate || runtime.status === "idle";
  $("#clearRestartButton").disabled = !canOperate || state.cameraTune.open;
  const tuneButton = $("#tuneCameraButton");
  if (tuneButton) {
    tuneButton.disabled = !canOperate || !idle || state.cameraTune.open;
    tuneButton.title = idle ? "空闲时可打开相机设置" : "拍摄中不可打开，请先停止";
  }
  $("#openGalleryButton").disabled = !canOperate || Number(runtime.frames_total || 0) === 0;
  $("#exportButton").disabled = !canOperate || encode.status === "encoding";
  updateScheduleHint(runtime);
  renderSystem(system || state.bootstrap?.system);

  if (runtime.error) notice(runtime.error, true);
  renderContract(project);
  renderExport(encode);
}

function fmtNum(value, digits = 2) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

function yn(value) {
  return value ? "开" : "关";
}

function renderContract(project) {
  const afRangeNames = { normal: "常规", macro: "微距", full: "全范围" };
  const afSpeedNames = { normal: "正常", fast: "快速" };
  const awbModeNames = {
    auto: "自动",
    tungsten: "钨丝灯",
    fluorescent: "荧光灯",
    indoor: "室内",
    daylight: "日光",
    cloudy: "阴天",
  };
  const values = project
    ? [
        ["模式", modeNames[project.mode]],
        ["拍摄窗口", project.window.type],
        ["画面旋转", `${Number(project.camera.rotation || 0)}°`],
        ["分辨率", project.capture.still_config.main_size.join(" × ")],
        ["JPEG 质量", project.capture.still_config.jpeg_quality],
        [
          "曝光",
          project.camera.ae_enable
            ? `自动  EV ${fmtNum(project.camera.exposure_value)}`
            : `${project.camera.exposure_time_us} μs`,
        ],
        ["曝光补偿 EV", fmtNum(project.camera.exposure_value)],
        ["快门", `${project.camera.exposure_time_us} μs`],
        ["模拟增益", project.camera.ae_enable ? "自动" : fmtNum(project.camera.analogue_gain, 1)],
        [
          "对焦",
          project.camera.af_mode === "auto_once"
            ? `单次自动对焦 / ${fmtNum(project.camera.lens_position)}`
            : `手动 / ${fmtNum(project.camera.lens_position)}`,
        ],
        ["对焦范围", afRangeNames[project.camera.af_range] || project.camera.af_range || "—"],
        ["对焦速度", afSpeedNames[project.camera.af_speed] || project.camera.af_speed || "—"],
        [
          "白平衡",
          project.camera.awb_enable
            ? `自动 / ${awbModeNames[project.camera.awb_mode] || project.camera.awb_mode || "auto"}`
            : `锁定 ${project.camera.colour_gains.map((g) => fmtNum(g)).join(" / ")}`,
        ],
        ["亮度", fmtNum(project.camera.brightness)],
        ["对比度", fmtNum(project.camera.contrast)],
        ["饱和度", fmtNum(project.camera.saturation)],
        ["锐度", fmtNum(project.camera.sharpness, 1)],
        ["HDR", yn(project.camera.hdr)],
        ["成片帧率", `${project.encode.fps} fps`],
      ]
    : [["模式", "—"]];
  $("#contract").replaceChildren(
    ...values.map(([key, value]) => {
      const row = document.createElement("div");
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value;
      row.append(dt, dd);
      return row;
    })
  );
}

function renderExport(encode) {
  const labels = {
    idle: "尚未导出",
    encoding: `正在处理 ${encode.frame_count || 0} 帧，请勿断电…`,
    done: `已生成 ${encode.filename}`,
    failed: encode.error || "导出失败",
  };
  $("#exportState").textContent = labels[encode.status] || encode.status;
  $("#downloadButton").classList.toggle("hidden", encode.status !== "done");
}

function updateWatermarkFields() {
  const type = $("#watermarkType").value;
  $("#timestampFormatField").classList.toggle("hidden", type !== "timestamp");
  $("#watermarkTextField").classList.toggle("hidden", type !== "text");
  $("#watermarkPositionField").classList.toggle("hidden", type === "none");
}

function updateGallerySummary() {
  const loaded = $("#photoGrid").querySelectorAll(".photo-card").length;
  const missing = $("#photoGrid").querySelectorAll(".photo-card:not(.thumb-ready)").length;
  let text =
    state.gallery.total > 0
      ? `当前范围 ${state.gallery.total.toLocaleString()} 张，已加载 ${loaded.toLocaleString()} 张。勾选可批量删除，点击图片查看全图。`
      : "当前时间范围内没有照片";
  if (missing > 0) text += ` 缩略图生成中 ${missing} 张…`;
  $("#gallerySummary").textContent = text;
  $("#galleryEmpty").classList.toggle("hidden", state.gallery.total !== 0);
  $("#loadMorePhotos").classList.toggle("hidden", loaded >= state.gallery.total);
  updateSelectionBar();
}

function toDateTimeLocal(value) {
  if (!value) return "";
  return String(value).slice(0, 16);
}

function parseLocalDateTime(value) {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  return new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4]),
    Number(match[5])
  );
}

function formatDateTimeLocal(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function addMinutes(value, minutes) {
  const date = parseLocalDateTime(value);
  if (!date) return "";
  return formatDateTimeLocal(new Date(date.getTime() + minutes * 60000));
}

function clampDateTime(value, min, max) {
  if (!value) return min || "";
  if (min && value < min) return min;
  if (max && value > max) return max;
  return value;
}

const SPAN_OPTIONS = {
  5: { label: "5分钟内", earlier: "前5分钟", later: "后5分钟" },
  10: { label: "10分钟内", earlier: "前10分钟", later: "后10分钟" },
  30: { label: "30分钟内", earlier: "前30分钟", later: "后30分钟" },
  60: { label: "1个小时内", earlier: "前1小时", later: "后1小时" },
  1440: { label: "1天内", earlier: "前1天", later: "后1天" },
};

function currentSpanMinutes() {
  return Number($("#gallerySpan").value) || 5;
}

function galleryFilterMode() {
  return document.querySelector('input[name="galleryFilterMode"]:checked')?.value || "interval";
}

function updateFilterModeUI() {
  const interval = galleryFilterMode() === "interval";
  state.gallery.filterMode = interval ? "interval" : "custom";
  $("#gallerySpanField").classList.toggle("hidden", !interval);
  $("#shiftEarlier").classList.toggle("hidden", !interval);
  $("#shiftLater").classList.toggle("hidden", !interval);
  $("#galleryEndField").classList.toggle("hidden", interval);
  if (!interval) {
    const window = intervalWindow();
    const range = state.gallery.range;
    const max = toDateTimeLocal(range?.end_max);
    if (window?.end && (!$("#galleryEnd").value || $("#galleryEnd").value <= window.start)) {
      $("#galleryEnd").value = clampDateTime(window.end, window.start, max);
    }
  }
  updateShiftButtons();
}

function updateShiftButtons() {
  const option = SPAN_OPTIONS[currentSpanMinutes()] || SPAN_OPTIONS[5];
  $("#shiftEarlier").textContent = option.earlier;
  $("#shiftLater").textContent = option.later;
  const range = state.gallery.range;
  const start = $("#galleryStart").value;
  const span = currentSpanMinutes();
  const min = toDateTimeLocal(range?.start_min);
  const max = toDateTimeLocal(range?.end_max);
  $("#shiftEarlier").disabled = !start || (min && start <= min);
  $("#shiftLater").disabled = !start || (max && addMinutes(start, span) >= max);
  const first = toDateTimeLocal(range?.earliest);
  const latest = toDateTimeLocal(range?.latest);
  $("#jumpFirstTime").disabled = !first || start === first;
  $("#jumpLatestTime").disabled = !latest || start === latest;
}

function intervalWindow() {
  const start = $("#galleryStart").value;
  if (!start) return null;
  return { start, end: addMinutes(start, currentSpanMinutes()) };
}

function bindGalleryRange(range) {
  state.gallery.range = range || null;
  const start = $("#galleryStart");
  const end = $("#galleryEnd");
  const enabled = Boolean(range?.start_min && range?.end_max);
  start.disabled = end.disabled = !enabled;
  $("#gallerySpan").disabled = !enabled;
  $("#applyGalleryFilter").disabled = !enabled;
  $("#resetGalleryFilter").disabled = !enabled;
  $("#jumpFirstTime").disabled = !enabled;
  $("#jumpLatestTime").disabled = !enabled;
  if (!enabled) {
    start.value = "";
    end.value = "";
    start.removeAttribute("min");
    start.removeAttribute("max");
    end.removeAttribute("min");
    end.removeAttribute("max");
    updateShiftButtons();
    return;
  }
  const min = toDateTimeLocal(range.start_min);
  const max = toDateTimeLocal(range.end_max);
  start.min = end.min = min;
  start.max = end.max = max;
  start.value = clampDateTime(start.value, min, max);
  end.value = clampDateTime(end.value, min, max);
  if (end.value < start.value) end.value = max;
  updateShiftButtons();
}

function photosQuery(offset) {
  const params = new URLSearchParams({ offset: String(offset), limit: "24" });
  if (state.gallery.filter.start) params.set("start", state.gallery.filter.start);
  if (state.gallery.filter.end) params.set("end", state.gallery.filter.end);
  return `/api/photos?${params.toString()}`;
}

function applyTimeFilter() {
  const range = state.gallery.range;
  if (!range?.start_min || !range?.end_max) return;
  const min = toDateTimeLocal(range.start_min);
  const max = toDateTimeLocal(range.end_max);
  if (galleryFilterMode() === "interval") {
    const window = intervalWindow();
    if (!window?.start) {
      notice("请选择开始时间", true);
      return;
    }
    if (window.start < min || window.start > max) {
      notice("开始时间必须在最早到最晚拍摄时间之间", true);
      return;
    }
    state.gallery.filter = window;
    loadPhotos(true);
    return;
  }
  const startValue = $("#galleryStart").value;
  const endValue = $("#galleryEnd").value;
  if (!startValue || !endValue) {
    notice("请选择开始和结束时间", true);
    return;
  }
  if (startValue < min || endValue > max) {
    notice("时间范围必须在最早到最晚拍摄时间之间", true);
    return;
  }
  const start = parseLocalDateTime(startValue);
  const end = parseLocalDateTime(endValue);
  if (!start || !end || end < start) {
    notice("结束时间不能早于开始时间", true);
    return;
  }
  const span = (end - start) / 60000;
  const available = (parseLocalDateTime(max) - parseLocalDateTime(min)) / 60000;
  if (available >= 5 && span < 5) {
    notice("筛选时间段至少为 5 分钟", true);
    return;
  }
  state.gallery.filter = { start: startValue, end: endValue };
  loadPhotos(true);
}

function jumpStartTime(which) {
  const range = state.gallery.range;
  if (!range?.earliest || !range?.latest) return;
  const min = toDateTimeLocal(range.start_min);
  const max = toDateTimeLocal(range.end_max);
  const target = toDateTimeLocal(which === "latest" ? range.latest : range.earliest);
  $("#galleryStart").value = clampDateTime(target, min, max);
  if (galleryFilterMode() === "custom") {
    const end = $("#galleryEnd").value;
    if (!end || end < $("#galleryStart").value) $("#galleryEnd").value = max;
  }
  updateShiftButtons();
  applyTimeFilter();
}

function shiftTimeWindow(direction) {
  const range = state.gallery.range;
  if (!range?.start_min || !range?.end_max) return;
  const start = $("#galleryStart").value;
  if (!start) {
    notice("请先选择开始时间", true);
    return;
  }
  const span = currentSpanMinutes();
  const min = toDateTimeLocal(range.start_min);
  const max = toDateTimeLocal(range.end_max);
  const shifted = addMinutes(start, direction * span);
  $("#galleryStart").value = clampDateTime(shifted, min, max);
  updateShiftButtons();
  applyTimeFilter();
}

function resetTimeFilter() {
  const range = state.gallery.range;
  state.gallery.filter = { start: "", end: "" };
  if (range?.start_min && range?.end_max) {
    $("#galleryStart").value = toDateTimeLocal(range.start_min);
    $("#galleryEnd").value = toDateTimeLocal(range.end_max);
  }
  updateShiftButtons();
  loadPhotos(true);
}

function updateSelectionBar() {
  const count = state.gallery.selected.size;
  $("#selectionCount").textContent = `已选 ${count} 张`;
  $("#deleteSelectedButton").disabled = count === 0 || state.busy;
  const cards = [...$("#photoGrid").querySelectorAll(".photo-card")];
  const allSelected =
    cards.length > 0 && cards.every((card) => state.gallery.selected.has(card.dataset.photoId));
  $("#selectAllPhotos").checked = allSelected;
}

function togglePhotoSelection(photoId, selected) {
  if (selected) state.gallery.selected.add(photoId);
  else state.gallery.selected.delete(photoId);
  const card = $(`#photoGrid .photo-card[data-photo-id="${CSS.escape(photoId)}"]`);
  if (card) card.classList.toggle("selected", selected);
  updateSelectionBar();
}

function photoCard(photo) {
  const card = document.createElement("div");
  card.className = "photo-card";
  card.dataset.photoId = photo.id;
  if (state.gallery.selected.has(photo.id)) card.classList.add("selected");

  const check = document.createElement("input");
  check.type = "checkbox";
  check.className = "photo-check";
  check.checked = state.gallery.selected.has(photo.id);
  check.title = "选择删除";
  check.onclick = (event) => event.stopPropagation();
  check.onchange = () => togglePhotoSelection(photo.id, check.checked);

  const wrap = document.createElement("div");
  wrap.className = "thumb-wrap";
  const status = document.createElement("div");
  status.className = "thumb-status";
  status.textContent = photo.has_thumbnail ? "加载中…" : "生成缩略图…";
  const image = document.createElement("img");
  image.alt = photo.filename;
  // Dialogs break native lazy-loading; load eagerly after the card is inserted.
  image.loading = "eager";
  image.decoding = "async";
  image.onload = () => {
    image.classList.add("ready");
    wrap.classList.add("ready");
    card.classList.add("thumb-ready");
    updateGallerySummary();
  };
  image.onerror = () => {
    status.textContent = "缩略图失败";
    wrap.classList.add("ready");
  };

  wrap.append(status, image);
  const label = document.createElement("span");
  label.className = "photo-meta";
  label.textContent = photo.filename
    .replace(/^frame_/, "")
    .replace(/\.jpe?g$/i, "")
    .replace("_", " ");

  card.append(check, wrap, label);
  card.onclick = (event) => {
    if (event.target === check) return;
    openFullPhoto(photo);
  };
  // Assign src after listeners so the first paint shows the placeholder.
  queueMicrotask(() => {
    image.src = `${photo.thumbnail_url}&t=${Date.now()}`;
  });
  return card;
}

async function warmThumbnails(photos) {
  const missing = photos.filter((photo) => !photo.has_thumbnail).map((photo) => photo.id);
  if (!missing.length) return;
  try {
    await api("/api/photos/ensure-thumbnails", {
      method: "POST",
      body: JSON.stringify({ photo_ids: missing }),
    });
    // Reload any still-pending images after server-side generation.
    $("#photoGrid")
      .querySelectorAll(".photo-card:not(.thumb-ready) img")
      .forEach((image) => {
        const url = new URL(image.src, window.location.origin);
        url.searchParams.set("t", String(Date.now()));
        image.src = url.pathname + "?" + url.searchParams.toString();
      });
  } catch (_) {}
}

async function loadPhotos(reset = false) {
  if (state.gallery.loading) return false;
  if (reset) {
    state.gallery.offset = 0;
    state.gallery.total = 0;
    state.gallery.current = null;
    state.gallery.items = [];
    state.gallery.selected.clear();
    $("#photoGrid").replaceChildren();
  }
  state.gallery.loading = true;
  $("#gallerySummary").textContent = "正在读取照片…";
  try {
    const data = await api(photosQuery(state.gallery.offset));
    bindGalleryRange(data.range);
    data.items.forEach((photo) => {
      state.gallery.items.push(photo);
      $("#photoGrid").append(photoCard(photo));
    });
    state.gallery.offset += data.items.length;
    state.gallery.total = data.total;
    updateGallerySummary();
    warmThumbnails(data.items);
    return data.items.length > 0;
  } catch (error) {
    $("#gallerySummary").textContent = error.message;
    notice(error.message, true);
    return false;
  } finally {
    state.gallery.loading = false;
  }
}

function currentPhotoIndex() {
  const id = state.gallery.current?.photo?.id;
  if (!id) return -1;
  return state.gallery.items.findIndex((photo) => photo.id === id);
}

function updateFullNavButtons() {
  const index = currentPhotoIndex();
  const hasPrev = index > 0;
  const hasNext = index >= 0 && (index < state.gallery.items.length - 1 || state.gallery.items.length < state.gallery.total);
  $("#prevPhotoButton").disabled = !hasPrev;
  $("#nextPhotoButton").disabled = !hasNext;
}

function showFullPhoto(photo) {
  const card = $(`#photoGrid .photo-card[data-photo-id="${CSS.escape(photo.id)}"]`);
  state.gallery.current = { photo, card };
  $("#photoTitle").textContent = photo.filename;
  $("#fullPhoto").classList.remove("ready");
  $("#fullPhotoLoading").classList.remove("hidden");
  $("#fullPhotoLoading").textContent = "正在加载全图…";
  const image = $("#fullPhoto");
  image.onload = () => {
    image.classList.add("ready");
    $("#fullPhotoLoading").classList.add("hidden");
  };
  image.onerror = () => {
    $("#fullPhotoLoading").textContent = "全图加载失败";
  };
  image.src = `${photo.full_url}&t=${Date.now()}`;
  const download = $("#downloadPhotoButton");
  download.href = `${photo.full_url}&download=1`;
  download.download = photo.filename || "photo.jpg";
  updateFullNavButtons();
}

function openFullPhoto(photo) {
  showFullPhoto(photo);
  if (!$("#photoDialog").open) $("#photoDialog").showModal();
}

async function stepFullPhoto(direction) {
  const index = currentPhotoIndex();
  if (index < 0) return;
  const target = index + direction;
  if (target < 0) return;
  if (target >= state.gallery.items.length) {
    const loaded = await loadPhotos(false);
    if (!loaded) {
      updateFullNavButtons();
      return;
    }
  }
  const photo = state.gallery.items[Math.min(target, state.gallery.items.length - 1)];
  if (photo) showFullPhoto(photo);
}

function closeFullPhoto() {
  $("#photoDialog").close();
  $("#fullPhoto").removeAttribute("src");
  $("#fullPhotoLoading").textContent = "正在加载全图…";
  state.gallery.current = null;
}

async function removeCurrentPhoto() {
  const current = state.gallery.current;
  if (!current) return;
  if (!confirm(`确定永久删除 ${current.photo.filename}？此操作不可恢复。`)) return;
  $("#deletePhotoButton").disabled = true;
  try {
    const data = await api("/api/photos", {
      method: "DELETE",
      body: JSON.stringify({ photo_id: current.photo.id }),
    });
    current.card?.remove();
    state.gallery.items = state.gallery.items.filter((photo) => photo.id !== current.photo.id);
    state.gallery.selected.delete(current.photo.id);
    state.gallery.total = Math.max(0, state.gallery.total - 1);
    state.gallery.offset = Math.max(0, state.gallery.offset - 1);
    closeFullPhoto();
    updateGallerySummary();
    if (data.runtime) applyRuntime(data.runtime, state.bootstrap?.encode);
    notice("照片及对应缩略图已删除");
    if (data.runtime?.last_frame) setPreview();
    else {
      $("#preview").classList.remove("ready");
      $("#viewerEmpty").classList.remove("hidden");
    }
  } catch (error) {
    notice(error.message, true);
  } finally {
    $("#deletePhotoButton").disabled = false;
  }
}

async function deleteSelectedPhotos() {
  const ids = [...state.gallery.selected];
  if (!ids.length) return;
  if (!confirm(`确定永久删除选中的 ${ids.length} 张照片？此操作不可恢复。`)) return;
  $("#deleteSelectedButton").disabled = true;
  try {
    const data = await api("/api/photos/delete", {
      method: "POST",
      body: JSON.stringify({ photo_ids: ids }),
    });
    const deletedIds = new Set((data.result?.deleted || []).map((item) => item.id));
    deletedIds.forEach((id) => {
      const card = $(`#photoGrid .photo-card[data-photo-id="${CSS.escape(id)}"]`);
      card?.remove();
      state.gallery.selected.delete(id);
    });
    state.gallery.items = state.gallery.items.filter((photo) => !deletedIds.has(photo.id));
    state.gallery.total = Math.max(0, state.gallery.total - deletedIds.size);
    state.gallery.offset = Math.max(0, state.gallery.offset - deletedIds.size);
    updateGallerySummary();
    if (data.runtime) applyRuntime(data.runtime, state.bootstrap?.encode);
    const failed = data.result?.errors?.length || 0;
    notice(
      failed
        ? `已删除 ${deletedIds.size} 张，失败 ${failed} 张`
        : `已删除 ${deletedIds.size} 张照片`
    );
    if (data.runtime?.last_frame) setPreview();
  } catch (error) {
    notice(error.message, true);
  } finally {
    updateSelectionBar();
  }
}

function renderProjects(items, activeId) {
  const list = $("#projectList");
  list.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "还没有项目";
    list.append(empty);
    return;
  }
  items.forEach((project) => {
    const row = document.createElement("div");
    row.className = "project-row";
    if (project.project_id === activeId) row.classList.add("active");
    const thumb = document.createElement("div");
    thumb.className = "project-thumb";
    const thumbImage = document.createElement("img");
    thumbImage.alt = `${project.name} 第一张照片`;
    const thumbEmpty = document.createElement("span");
    thumbEmpty.textContent = "暂无照片";
    if (project.first_photo?.thumbnail_url) {
      thumbImage.onload = () => {
        thumbImage.classList.add("ready");
        thumbEmpty.classList.add("hidden");
      };
      thumbImage.onerror = () => {
        thumbImage.classList.remove("ready");
        thumbEmpty.classList.remove("hidden");
      };
      thumbImage.src = project.first_photo.thumbnail_url;
    }
    thumb.append(thumbImage, thumbEmpty);
    const info = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = project.name;
    const meta = document.createElement("small");
    meta.textContent = `${project.project_id} · ${formatInterval(project.capture.interval_sec)}`;
    info.append(name, meta);
    const button = document.createElement("button");
    button.className = "button subtle";
    button.textContent = "详情";
    button.onclick = (event) => {
      event.stopPropagation();
      openProjectDetail(project.project_id);
    };
    row.onclick = () => openProjectDetail(project.project_id);
    row.append(thumb, info, button);
    list.append(row);
  });
}

function renderDetailFacts(detail) {
  const project = detail.project;
  const rows = [
    ["项目 ID", project.project_id],
    ["模式", modeNames[project.mode] || project.mode],
    ["预设", project.preset],
    ["拍摄间隔", formatInterval(project.capture.interval_sec)],
    ["照片数量", Number(detail.frames_total || 0).toLocaleString()],
    ["当前项目", detail.is_active ? "是" : "否"],
    ["存储位置", project.storage?.root || "—"],
  ];
  $("#detailFacts").replaceChildren(
    ...rows.map(([key, value]) => {
      const row = document.createElement("div");
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value;
      row.append(dt, dd);
      return row;
    })
  );
}

function renderExportList(projectId, exports) {
  const holder = $("#detailExportList");
  holder.replaceChildren();
  if (!exports?.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "暂无导出";
    holder.append(empty);
    return;
  }
  exports.forEach((item) => {
    const row = document.createElement("div");
    row.className = "export-row";
    const info = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = item.filename;
    const meta = document.createElement("small");
    meta.textContent = `${formatBytes(item.size)} · ${item.modified_at.replace("T", " ")}`;
    info.append(name, meta);
    const link = document.createElement("a");
    link.className = "button subtle";
    link.textContent = "下载";
    link.href = `/api/projects/${encodeURIComponent(projectId)}/exports/file?name=${encodeURIComponent(item.filename)}`;
    row.append(info, link);
    holder.append(row);
  });
}

function fillDetailCover(detail) {
  const image = $("#detailCover");
  const empty = $("#detailCoverEmpty");
  if (!image || !empty) return;
  const cover = detail.first_photo;
  image.onload = null;
  image.onerror = null;
  if (!cover?.thumbnail_url) {
    image.removeAttribute("src");
    image.classList.remove("ready");
    empty.textContent = "暂无照片";
    empty.classList.remove("hidden");
    return;
  }
  image.onload = () => {
    image.classList.add("ready");
    empty.classList.add("hidden");
  };
  image.onerror = () => {
    image.classList.remove("ready");
    empty.textContent = "暂无照片";
    empty.classList.remove("hidden");
  };
  image.src = `${cover.thumbnail_url}${cover.thumbnail_url.includes("?") ? "&" : "?"}t=${Date.now()}`;
}

function setDetailActionHint(message, isError = false) {
  const hint = $("#detailActionHint");
  if (!hint) return;
  hint.textContent = message;
  hint.classList.remove("hidden", "ok", "error");
  hint.classList.add(isError ? "error" : "ok");
}

function fillProjectDetail(detail) {
  const project = detail.project;
  state.detailProjectId = project.project_id;
  $("#detailName").textContent = project.name;
  $("#detailMeta").textContent = detail.is_active
    ? "当前正在使用此项目"
    : "此项目未激活，可切换后继续拍摄";
  $("#detailInterval").value = project.capture.interval_sec;
  const windowType = project.window?.type === "clock" ? "clock" : "always";
  $("#detailWindowType").value = windowType;
  $("#detailClockStart").value = project.window?.clock?.start || "07:00";
  $("#detailClockEnd").value = project.window?.clock?.end || "23:00";
  $("#detailClockFields").classList.toggle("hidden", windowType !== "clock");
  $("#switchDetailButton").disabled = detail.is_active || state.busy;
  $("#switchDetailButton").textContent = detail.is_active ? "已是当前项目" : "切换到此项目";
  fillDetailCover(detail);
  renderDetailFacts(detail);
  renderExportList(project.project_id, detail.exports);
}

async function openProjectDetail(projectId, clearHint = true) {
  try {
    if (clearHint) $("#detailActionHint")?.classList.add("hidden");
    const detail = await api(`/api/projects/${encodeURIComponent(projectId)}`);
    fillProjectDetail(detail);
    if (!$("#projectDetailDialog").open) $("#projectDetailDialog").showModal();
  } catch (error) {
    notice(error.message, true);
  }
}

function closeProjectDetail() {
  $("#projectDetailDialog").close();
  state.detailProjectId = null;
}

async function saveProjectInterval() {
  const projectId = state.detailProjectId;
  const interval = Number($("#detailInterval").value);
  const windowType = $("#detailWindowType").value;
  if (!projectId) return;
  if (!Number.isFinite(interval) || interval <= 0) {
    setDetailActionHint("保存失败：请输入有效的拍摄间隔", true);
    notice("请输入有效的拍摄间隔", true);
    return;
  }
  const body = { interval_sec: interval, window_type: windowType };
  if (windowType === "clock") {
    body.clock_start = $("#detailClockStart").value;
    body.clock_end = $("#detailClockEnd").value;
    if (!body.clock_start || !body.clock_end) {
      setDetailActionHint("保存失败：请填写窗口开始和结束时间", true);
      notice("请填写窗口开始和结束时间", true);
      return;
    }
  }
  try {
    const data = await api(`/api/projects/${encodeURIComponent(projectId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    if (data.runtime) applyRuntime(data.runtime, state.bootstrap?.encode);
    await loadBootstrap();
    await openProjectDetail(projectId, false);
    setDetailActionHint("保存成功：项目设置已更新");
    notice("项目设置已更新");
  } catch (error) {
    setDetailActionHint(`保存失败：${error.message}`, true);
    notice(error.message, true);
  }
}

async function deleteViewedProject() {
  const projectId = state.detailProjectId;
  if (!projectId) return;
  if (
    !confirm(
      "将永久删除此项目及其全部照片、缩略图和导出视频。此操作不可恢复，确认继续？"
    )
  ) {
    return;
  }
  if (state.busy) return;
  state.busy = true;
  try {
    const data = await api(`/api/projects/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    });
    closeProjectDetail();
    if ($("#galleryDialog").open) $("#galleryDialog").close();
    if ($("#photoDialog").open) closeFullPhoto();
    if (data.runtime) applyRuntime(data.runtime, data.encode);
    notice("项目已删除");
    await loadBootstrap();
  } catch (error) {
    notice(error.message, true);
  } finally {
    state.busy = false;
  }
}

function renderPresetCards(presets) {
  const holder = $("#presetCards");
  holder.replaceChildren();
  presets.forEach((preset) => {
    const card = document.createElement("div");
    card.className = "preset-card";
    const title = document.createElement("strong");
    title.textContent = preset.name;
    const meta = document.createElement("small");
    const summary = preset.summary ? `\n${preset.summary}` : "";
    meta.textContent = `${modeNames[preset.mode]} · ${formatInterval(preset.interval_sec)}${summary}`;
    card.append(title, meta);
    card.onclick = () => selectPreset(preset.id, card);
    holder.append(card);
  });
  if (presets[0]) holder.firstElementChild.click();
}

async function loadBootstrap() {
  try {
    state.bootstrap = await api("/api/bootstrap");
    renderProjects(state.bootstrap.projects, state.bootstrap.runtime.project?.project_id);
    renderPresetCards(state.bootstrap.presets);
    render(state.bootstrap.runtime, state.bootstrap.encode, state.bootstrap.system);
  } catch (error) {
    notice(error.message, true);
  }
}

async function pollStatus() {
  if (state.busy) return;
  try {
    const data = await api("/api/status");
    applyRuntime(data.runtime, data.encode, data.system);
  } catch (_) {}
}

async function selectPreset(id, card) {
  state.selectedPreset = id;
  document.querySelectorAll(".preset-card").forEach((el) => el.classList.remove("selected"));
  card.classList.add("selected");
  try {
    const preset = await api(`/api/presets/${id}`);
    const form = $("#projectForm");
    form.interval_sec.value = preset.capture.interval_sec;
    form.resolution.value = preset.capture.still_config.main_size.join("x");
  } catch (error) {
    notice(error.message, true);
  }
}

async function action(path, successMessage) {
  if (state.busy) return null;
  state.busy = true;
  if (state.bootstrap?.runtime) {
    render(state.bootstrap.runtime, state.bootstrap.encode || { status: "idle" });
  }
  try {
    const data = await api(path, { method: "POST" });
    applyRuntime(data.runtime || data, data.encode);
    if (successMessage) notice(successMessage);
    return data;
  } catch (error) {
    notice(error.message, true);
    try {
      const data = await api("/api/status");
      applyRuntime(data.runtime, data.encode);
    } catch (_) {}
    return null;
  } finally {
    state.busy = false;
    if (state.bootstrap?.runtime) {
      render(state.bootstrap.runtime, state.bootstrap.encode || { status: "idle" });
    }
  }
}

async function switchProject(projectId) {
  const running = ["capturing", "waiting", "paused", "starting"].includes(
    state.bootstrap?.runtime?.status
  );
  if (running && !confirm("切换方案会停止当前拍摄，确认继续？")) return;
  if (state.busy) return;
  state.busy = true;
  try {
    if ($("#galleryDialog").open) $("#galleryDialog").close();
    if ($("#photoDialog").open) closeFullPhoto();
    const data = await api("/api/projects/switch", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    });
    applyRuntime(data.runtime || data, data.encode);
    notice("项目已切换");
    await loadBootstrap();
    if (state.detailProjectId) {
      await openProjectDetail(state.detailProjectId, false);
      setDetailActionHint("切换成功：当前项目已更新");
    }
  } catch (error) {
    if (state.detailProjectId) setDetailActionHint(`切换失败：${error.message}`, true);
    notice(error.message, true);
  } finally {
    state.busy = false;
  }
}

$("#newProjectButton").onclick = () => $("#projectDialog").showModal();
$("#closeDialog").onclick = $("#cancelDialog").onclick = () => $("#projectDialog").close();
$("#closeProjectDetail").onclick = closeProjectDetail;
$("#saveIntervalButton").onclick = saveProjectInterval;
$("#detailWindowType").onchange = () => {
  $("#detailClockFields").classList.toggle("hidden", $("#detailWindowType").value !== "clock");
};
$("#switchDetailButton").onclick = () => {
  if (state.detailProjectId) switchProject(state.detailProjectId);
};
$("#deleteProjectButton").onclick = deleteViewedProject;
$("#refreshPreview").onclick = () => setPreview();

function formEl(name) {
  return $("#cameraTuneForm").elements.namedItem(name);
}

function formatShutter(us) {
  const n = Number(us);
  if (!Number.isFinite(n) || n <= 0) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 ? 2 : 0)} 秒`;
  return `1/${Math.max(1, Math.round(1_000_000 / n))} 秒`;
}

function fillCameraTuneForm(camera, still) {
  const form = $("#cameraTuneForm");
  if (!camera || !form) return;
  form.rotation.value = Number(camera.rotation) === 180 ? "180" : "0";
  form.af_mode.value = camera.af_mode;
  form.lens_position.value = camera.lens_position;
  form.af_range.value = camera.af_range || "full";
  form.af_speed.value = camera.af_speed || "normal";
  form.ae_enable.checked = Boolean(camera.ae_enable);
  form.exposure_value.value = camera.exposure_value ?? 0;
  form.exposure_time_us.value = camera.exposure_time_us;
  form.analogue_gain.value = camera.analogue_gain;
  form.awb_enable.checked = Boolean(camera.awb_enable);
  form.awb_mode.value = camera.awb_mode || "auto";
  form.colour_gain_r.value = camera.colour_gains?.[0] ?? 1.6;
  form.colour_gain_b.value = camera.colour_gains?.[1] ?? 1.5;
  form.brightness.value = camera.brightness ?? 0;
  form.contrast.value = camera.contrast ?? 1;
  form.saturation.value = camera.saturation ?? 1;
  form.sharpness.value = camera.sharpness ?? 1;
  form.hdr.checked = Boolean(camera.hdr);
  if (still) {
    form.main_size.value = still.main_size.join("x");
    form.jpeg_quality.value = still.jpeg_quality;
  }
  updateTuneLabels();
  syncTuneDisabled();
}

function updateTuneLabels() {
  const form = $("#cameraTuneForm");
  $("#lensPositionValue").textContent = Number(form.lens_position.value).toFixed(2);
  $("#evValue").textContent = Number(form.exposure_value.value).toFixed(3);
  $("#shutterValue").textContent = `${form.exposure_time_us.value} · ${formatShutter(form.exposure_time_us.value)}`;
  $("#gainValue").textContent = Number(form.analogue_gain.value).toFixed(1);
  $("#gainRValue").textContent = Number(form.colour_gain_r.value).toFixed(2);
  $("#gainBValue").textContent = Number(form.colour_gain_b.value).toFixed(2);
  $("#brightnessValue").textContent = Number(form.brightness.value).toFixed(2);
  $("#contrastValue").textContent = Number(form.contrast.value).toFixed(2);
  $("#saturationValue").textContent = Number(form.saturation.value).toFixed(2);
  $("#sharpnessValue").textContent = Number(form.sharpness.value).toFixed(1);
  $("#jpegValue").textContent = form.jpeg_quality.value;
}

function syncTuneDisabled() {
  const form = $("#cameraTuneForm");
  const autoExp = form.ae_enable.checked;
  const autoWb = form.awb_enable.checked;
  const manualAf = form.af_mode.value === "manual";
  form.exposure_value.disabled = !autoExp;
  form.exposure_time_us.disabled = autoExp;
  form.analogue_gain.disabled = autoExp;
  form.awb_mode.disabled = !autoWb;
  form.colour_gain_r.disabled = autoWb;
  form.colour_gain_b.disabled = autoWb;
  form.lens_position.disabled = !manualAf;
}

function readCameraTunePayload() {
  const form = $("#cameraTuneForm");
  const project = state.bootstrap?.runtime?.project;
  const [width, height] = form.main_size.value.split("x").map(Number);
  return {
    camera: {
      lens: project?.camera?.lens || "wide",
      camera_led: Boolean(project?.camera?.camera_led),
      rotation: Number(form.rotation.value) === 180 ? 180 : 0,
      af_mode: form.af_mode.value,
      lens_position: Number(form.lens_position.value),
      af_range: form.af_range.value,
      af_speed: form.af_speed.value,
      ae_enable: form.ae_enable.checked,
      exposure_time_us: Number(form.exposure_time_us.value),
      analogue_gain: Number(form.analogue_gain.value),
      exposure_value: Number(form.exposure_value.value),
      awb_enable: form.awb_enable.checked,
      awb_mode: form.awb_mode.value,
      colour_gains: [Number(form.colour_gain_r.value), Number(form.colour_gain_b.value)],
      brightness: Number(form.brightness.value),
      contrast: Number(form.contrast.value),
      saturation: Number(form.saturation.value),
      sharpness: Number(form.sharpness.value),
      hdr: form.hdr.checked,
    },
    jpeg_quality: Number(form.jpeg_quality.value),
    main_size: [width, height],
  };
}

function renderLiveMeta(live) {
  const rows = [
    ["实测快门", live?.exposure_time ? `${live.exposure_time} μs (${formatShutter(live.exposure_time)})` : "—"],
    ["实测增益", live?.analogue_gain != null ? Number(live.analogue_gain).toFixed(2) : "—"],
    ["实测焦距", live?.lens_position != null ? Number(live.lens_position).toFixed(2) : "—"],
    ["色增益", live?.colour_gains ? live.colour_gains.map((n) => Number(n).toFixed(2)).join(" / ") : "—"],
    ["照度", live?.lux != null ? Number(live.lux).toFixed(1) : "—"],
  ];
  $("#cameraLiveMeta").replaceChildren(
    ...rows.map(([key, value]) => {
      const row = document.createElement("div");
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value;
      row.append(dt, dd);
      return row;
    })
  );
}

function setTuneCommitHint(message, failed = false) {
  const el = $("#cameraTuneCommitHint");
  if (!el) return;
  el.textContent = message;
  el.classList.remove("hidden", "ok", "fail");
  el.classList.add(failed ? "fail" : "ok");
}

function applyTuneState(tune, refill = true) {
  if (!tune) return;
  state.cameraTune.dirty = Boolean(tune.dirty);
  $("#cameraTuneStatus").textContent = tune.dirty
    ? "已实时应用到相机，尚未写入当前相机设置"
    : "与当前相机设置一致";
  if (refill && tune.camera) fillCameraTuneForm(tune.camera, tune.still);
  renderLiveMeta(tune.live);
  if (state.bootstrap?.runtime?.project && tune.camera) {
    state.bootstrap.runtime.project.camera = tune.camera;
    if (tune.still) state.bootstrap.runtime.project.capture.still_config = tune.still;
    renderContract(state.bootstrap.runtime.project);
    updateLiveRotationHint(state.bootstrap.runtime.project);
  }
}

async function applyCameraTuneLive() {
  if (!state.cameraTune.open || state.cameraTune.applying) return;
  updateTuneLabels();
  syncTuneDisabled();
  clearTimeout(applyCameraTuneLive.timer);
  applyCameraTuneLive.timer = setTimeout(async () => {
    state.cameraTune.applying = true;
    try {
      const tune = await api("/api/camera/tune", {
        method: "PATCH",
        body: JSON.stringify(readCameraTunePayload()),
      });
      applyTuneState(tune, false);
      setTunePreview();
      setPreview();
    } catch (error) {
      notice(error.message, true);
    } finally {
      state.cameraTune.applying = false;
    }
  }, 180);
}

async function closeCameraTune(discard) {
  const dialog = $("#cameraTuneDialog");
  stopTunePreviewLoop();
  try {
    const data = await api("/api/camera/tune/close", {
      method: "POST",
      body: JSON.stringify({ discard }),
    });
    applyRuntime(data.runtime, data.encode);
    await loadBootstrap();
  } catch (error) {
    notice(error.message, true);
  } finally {
    state.cameraTune.open = false;
    if (dialog.open) dialog.close();
    if (state.bootstrap?.runtime) {
      render(state.bootstrap.runtime, state.bootstrap.encode || { status: "idle" });
    }
  }
}

$("#tuneCameraButton") && ($("#tuneCameraButton").onclick = async () => {
  if (state.busy) return;
  const status = state.bootstrap?.runtime?.status;
  if (status && status !== "idle" && status !== "error") {
    notice("拍摄进行中，请先停止后再打开相机设置", true);
    return;
  }
  state.busy = true;
  try {
    notice("正在打开相机设置…", false, true);
    const tune = await api("/api/camera/tune/session", { method: "POST" });
    state.cameraTune.open = true;
    applyTuneState(tune);
    $("#cameraTuneDialog").showModal();
    $("#cameraTuneCommitHint")?.classList.add("hidden");
    const empty = $("#tuneViewerEmpty");
    if (empty) {
      empty.classList.remove("hidden");
      empty.textContent = "正在获取实时画面…";
    }
    $("#tunePreview")?.classList.remove("ready");
    startTunePreviewLoop();
    notice("相机已启动：拖动参数会实时应用到画面");
  } catch (error) {
    notice(error.message, true);
  } finally {
    state.busy = false;
    if (state.bootstrap?.runtime) {
      render(state.bootstrap.runtime, state.bootstrap.encode || { status: "idle" });
    }
  }
});

const cameraTuneForm = $("#cameraTuneForm");
if (cameraTuneForm) {
  cameraTuneForm.addEventListener("input", applyCameraTuneLive);
  cameraTuneForm.addEventListener("change", applyCameraTuneLive);
}

(() => {
  const presets = [
    [1000, "1/1000"],
    [4000, "1/250"],
    [8000, "1/125"],
    [16667, "1/60"],
    [33333, "1/30"],
    [66667, "1/15"],
    [125000, "1/8"],
    [250000, "1/4"],
    [500000, "1/2"],
    [1000000, "1s"],
    [2000000, "2s"],
  ];
  const box = $("#shutterPresets");
  if (!box) return;
  presets.forEach(([us, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.onclick = () => {
      formEl("ae_enable").checked = false;
      formEl("exposure_time_us").value = String(us);
      applyCameraTuneLive();
    };
    box.append(button);
  });
})();

$("#tuneRefreshPreview") && ($("#tuneRefreshPreview").onclick = () => refreshTunePreview());
$("#tuneTestButton") && ($("#tuneTestButton").onclick = async () => {
  if (state.busy) return;
  state.busy = true;
  $("#tuneTestButton").textContent = "拍摄中…";
  notice("正在测试拍摄，请稍候…", false, true);
  try {
    const data = await api("/api/capture/test", { method: "POST" });
    setTunePreview(data.url);
    setPreview(data.url);
    const tune = await api("/api/camera/tune");
    applyTuneState(tune);
    notice("测试照片已保存");
  } catch (error) {
    notice(error.message, true);
  } finally {
    state.busy = false;
    $("#tuneTestButton").textContent = "测试拍摄";
  }
});
$("#tuneAutoButton") && ($("#tuneAutoButton").onclick = async () => {
  if (state.busy) return;
  state.busy = true;
  const label = $("#tuneAutoButton").textContent;
  $("#tuneAutoButton").textContent = "自动中…";
  notice("正在按当前画面自动设定焦距、快门和曝光…", false, true);
  try {
    const tune = await api("/api/camera/tune/auto", { method: "POST" });
    applyTuneState(tune);
    const measured = tune.measured || {};
    const shutter = measured.exposure_time;
    const focus = measured.lens_position;
    notice(
      `已切到自动：焦距 ${Number(focus || 0).toFixed(2)}，快门 ${shutter || "—"} μs（${formatShutter(shutter)}），曝光自动`
    );
    setTunePreview();
  } catch (error) {
    notice(error.message, true);
  } finally {
    state.busy = false;
    $("#tuneAutoButton").textContent = label;
  }
});
$("#tuneFocusButton") && ($("#tuneFocusButton").onclick = async () => {
  if (state.busy) return;
  state.busy = true;
  const keepManual = formEl("af_mode").value === "manual";
  notice("正在自动对焦…", false, true);
  try {
    const data = await api("/api/capture/focus", {
      method: "POST",
      body: JSON.stringify({ lock_manual: keepManual }),
    });
    applyTuneState(data.camera);
    formEl("af_mode").value = keepManual ? "manual" : "auto_once";
    formEl("lens_position").value = String(data.lens_position);
    updateTuneLabels();
    syncTuneDisabled();
    notice(
      keepManual
        ? `单次自动对焦完成，已锁定到焦距 ${Number(data.lens_position).toFixed(2)}`
        : `单次自动对焦完成：${Number(data.lens_position).toFixed(2)}（写入当前相机设置后才会保存）`
    );
    setTunePreview();
  } catch (error) {
    notice(error.message, true);
  } finally {
    state.busy = false;
  }
});
$("#commitCameraTune") && ($("#commitCameraTune").onclick = async () => {
  if (state.busy) return;
  state.busy = true;
  const button = $("#commitCameraTune");
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "写入中…";
  setTuneCommitHint("正在写入当前相机设置…");
  try {
    await api("/api/camera/tune", {
      method: "PATCH",
      body: JSON.stringify(readCameraTunePayload()),
    });
    const data = await api("/api/camera/tune/commit", { method: "POST" });
    applyTuneState(data.tune);
    applyRuntime(data.runtime, data.encode);
    setTunePreview();
    if (data.tune?.dirty !== false) {
      throw new Error("服务器未确认保存");
    }
    const ok = "写入成功：当前相机设置已更新，后续拍照和延时将使用这套配置";
    setTuneCommitHint(ok);
    notice(ok);
  } catch (error) {
    const fail = `写入失败：${error.message || "未知错误"}`;
    setTuneCommitHint(fail, true);
    notice(fail, true);
  } finally {
    state.busy = false;
    button.disabled = false;
    button.textContent = label;
  }
});
$("#revertCameraTune") && ($("#revertCameraTune").onclick = async () => {
  await closeCameraTune(true);
  const tune = await api("/api/camera/tune/session", { method: "POST" });
  state.cameraTune.open = true;
  applyTuneState(tune);
  $("#cameraTuneDialog").showModal();
  startTunePreviewLoop();
  notice("已还原为当前相机设置");
});
$("#cancelCameraTune") && ($("#cancelCameraTune").onclick = () => closeCameraTune(true));
$("#closeCameraTune") && ($("#closeCameraTune").onclick = () => closeCameraTune(true));
$("#cameraTuneDialog")?.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeCameraTune(true);
});
$("#startButton").onclick = async () => {
  const data = await action("/api/capture/start", null);
  if (!data) return;
  const runtime = data.runtime || data;
  if (runtime.window_open === false) {
    notice(`已启动，但当前不在拍摄窗口（${runtime.window_label}）内，到点后才会拍帧`, true);
  } else {
    notice("拍摄已开始");
  }
};
function syncScheduleUntilField() {
  const enabled = $("#scheduleStopEnable").checked;
  $("#scheduleUntilField").classList.toggle("hidden", !enabled);
  if (enabled && !$("#scheduleUntil").value && $("#scheduleAt").value) {
    const next = parseLocalDateTime($("#scheduleAt").value);
    if (next) {
      next.setHours(next.getHours() + 1);
      $("#scheduleUntil").value = formatDateTimeLocal(next);
    }
  }
}

$("#scheduleButton").onclick = () => {
  $("#scheduleAt").value = defaultScheduleValue(10);
  $("#scheduleStopEnable").checked = false;
  $("#scheduleUntil").value = "";
  syncScheduleUntilField();
  $("#scheduleDialog").showModal();
};
$("#closeScheduleDialog").onclick = $("#cancelScheduleDialog").onclick = () => $("#scheduleDialog").close();
$("#scheduleStopEnable").onchange = syncScheduleUntilField;
$("#scheduleAt").onchange = () => {
  if (!$("#scheduleStopEnable").checked) return;
  const start = $("#scheduleAt").value;
  const stop = $("#scheduleUntil").value;
  if (start && (!stop || stop <= start)) {
    const next = parseLocalDateTime(start);
    if (next) {
      next.setHours(next.getHours() + 1);
      $("#scheduleUntil").value = formatDateTimeLocal(next);
    }
  }
};
$("#confirmScheduleButton").onclick = async () => {
  const startAt = $("#scheduleAt").value;
  const stopEnabled = $("#scheduleStopEnable").checked;
  const stopAt = stopEnabled ? $("#scheduleUntil").value : "";
  if (!startAt) {
    notice("请选择开始时间", true);
    return;
  }
  if (stopEnabled && !stopAt) {
    notice("请选择结束时间", true);
    return;
  }
  if (stopEnabled && stopAt <= startAt) {
    notice("结束时间必须晚于开始时间", true);
    return;
  }
  try {
    const body = { start_at: startAt };
    if (stopEnabled) body.stop_at = stopAt;
    const data = await api("/api/capture/schedule", {
      method: "POST",
      body: JSON.stringify(body),
    });
    applyRuntime(data.runtime, data.encode);
    $("#scheduleDialog").close();
    notice(
      data.runtime.scheduled_stop_at
        ? `已预约 ${formatScheduleTime(data.runtime.scheduled_start_at)} 开拍，${formatScheduleTime(data.runtime.scheduled_stop_at)} 停止`
        : `已预约 ${formatScheduleTime(data.runtime.scheduled_start_at)} 开拍，需手动停止`
    );
  } catch (error) {
    notice(error.message, true);
  }
};
$("#cancelScheduleButton").onclick = async () => {
  try {
    const data = await api("/api/capture/schedule", { method: "DELETE" });
    applyRuntime(data.runtime, data.encode);
    notice("已取消定时拍摄");
  } catch (error) {
    notice(error.message, true);
  }
};
$("#pauseButton").onclick = () => {
  const resume = $("#pauseButton").dataset.action === "resume";
  action(`/api/capture/${resume ? "resume" : "pause"}`, resume ? "拍摄已继续" : "拍摄已暂停");
};
$("#stopButton").onclick = () => {
  if (confirm("确认停止当前拍摄？")) action("/api/capture/stop", "拍摄已停止");
};
$("#clearRestartButton").onclick = async () => {
  if (
    !confirm(
      "将删除当前项目已拍照片、预览和导出视频，然后重新开始拍摄。此操作不可恢复，确认继续？"
    )
  ) {
    return;
  }
  const data = await action("/api/capture/clear-restart", "已清空并重新开始拍摄");
  if (!data) return;
  const deleted = data.deleted || {};
  const total =
    Number(deleted.frames || 0) +
    Number(deleted.thumbnails || 0) +
    Number(deleted.previews || 0) +
    Number(deleted.exports || 0);
  if (total > 0) notice(`已删除 ${total} 个文件，并重新开始拍摄`);
  if ($("#galleryDialog").open) $("#galleryDialog").close();
  if ($("#photoDialog").open) closeFullPhoto();
  $("#preview").classList.remove("ready");
  $("#viewerEmpty").classList.remove("hidden");
};
$("#openGalleryButton").onclick = async () => {
  $("#galleryDialog").showModal();
  await loadPhotos(true);
};
$("#closeGallery").onclick = () => $("#galleryDialog").close();
$("#loadMorePhotos").onclick = () => loadPhotos(false);
$("#applyGalleryFilter").onclick = applyTimeFilter;
$("#resetGalleryFilter").onclick = resetTimeFilter;
$("#shiftEarlier").onclick = () => shiftTimeWindow(-1);
$("#shiftLater").onclick = () => shiftTimeWindow(1);
$("#jumpFirstTime").onclick = () => jumpStartTime("first");
$("#jumpLatestTime").onclick = () => jumpStartTime("latest");
$("#gallerySpan").onchange = () => {
  updateShiftButtons();
};
$("#galleryStart").onchange = updateShiftButtons;
document.querySelectorAll('input[name="galleryFilterMode"]').forEach((input) => {
  input.onchange = updateFilterModeUI;
});
$("#closePhoto").onclick = $("#closePhotoAction").onclick = closeFullPhoto;
$("#deletePhotoButton").onclick = removeCurrentPhoto;
$("#prevPhotoButton").onclick = () => stepFullPhoto(-1);
$("#nextPhotoButton").onclick = () => stepFullPhoto(1);
document.addEventListener("keydown", (event) => {
  if (!$("#photoDialog").open) return;
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    stepFullPhoto(-1);
  }
  if (event.key === "ArrowRight") {
    event.preventDefault();
    stepFullPhoto(1);
  }
});
$("#selectAllPhotos").onchange = () => {
  const checked = $("#selectAllPhotos").checked;
  $("#photoGrid").querySelectorAll(".photo-card").forEach((card) => {
    const box = card.querySelector(".photo-check");
    if (box) box.checked = checked;
    togglePhotoSelection(card.dataset.photoId, checked);
  });
};
$("#clearSelectionButton").onclick = () => {
  state.gallery.selected.clear();
  $("#photoGrid").querySelectorAll(".photo-card").forEach((card) => {
    card.classList.remove("selected");
    const box = card.querySelector(".photo-check");
    if (box) box.checked = false;
  });
  updateSelectionBar();
};
$("#deleteSelectedButton").onclick = deleteSelectedPhotos;
$("#watermarkType").onchange = updateWatermarkFields;
$("#watermarkText").oninput = () => {
  $("#watermarkTextCount").textContent = String($("#watermarkText").value.length);
};
$("#exportButton").onclick = async () => {
  const watermarkType = $("#watermarkType").value;
  const exportFps = Number($("#exportFps").value);
  const text = $("#watermarkText").value.trim();
  if (watermarkType === "text" && !text) {
    notice("请输入水印文本", true);
    $("#watermarkText").focus();
    return;
  }
  try {
    const data = await api("/api/export", {
      method: "POST",
      body: JSON.stringify({
        watermark_type: watermarkType,
        timestamp_format: $("#timestampFormat").value,
        position: $("#watermarkPosition").value,
        text,
        fps: exportFps,
      }),
    });
    renderExport(data);
    const labels = {
      none: "无水印",
      timestamp: "时间戳水印",
      text: "文本水印",
    };
    const positionLabels = {
      top_left: "左上角",
      top_right: "右上角",
      bottom_left: "左下角",
      bottom_right: "右下角",
      center: "正中央",
      top_center: "中央上方",
      bottom_center: "中央下方",
      top: "中央上方",
      bottom: "中央下方",
    };
    const positionLabel = positionLabels[$("#watermarkPosition").value] || "中央下方";
    notice(
      watermarkType === "none"
        ? `视频导出任务已开始（${exportFps} fps，无水印）`
        : `视频导出任务已开始（${exportFps} fps，${labels[watermarkType]}，${positionLabel}）`
    );
  } catch (error) {
    notice(error.message, true);
  }
};
$("#projectForm").onsubmit = async (event) => {
  event.preventDefault();
  const formEl = event.currentTarget;
  const form = new FormData(formEl);
  const body = {
    preset_id: state.selectedPreset,
    project_id: form.get("project_id"),
    name: form.get("name"),
    storage_root: form.get("storage_root"),
    interval_sec: Number(form.get("interval_sec")),
    resolution: form.get("resolution").split("x").map(Number),
  };
  try {
    await api("/api/projects", { method: "POST", body: JSON.stringify(body) });
    $("#projectDialog").close();
    formEl.reset();
    notice("项目已创建并切换");
    await loadBootstrap();
  } catch (error) {
    notice(error.message, true);
  }
};

setInterval(() => {
  $("#clock").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  if (state.bootstrap?.runtime) updateScheduleHint(state.bootstrap.runtime);
}, 1000);
updateFilterModeUI();
updateWatermarkFields();
loadBootstrap();
startMainPreviewLoop();
setInterval(pollStatus, 3000);
