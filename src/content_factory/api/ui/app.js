/** Partner delivery page: stock, async archive build, queries and settings. */

import { MESSAGES, requestJson } from "./api.js";
import { createArchivesView } from "./archives.js";
import { byId, el, setMessage, setText, toggle } from "./dom.js";
import { formatBytes, formatCount, formatDateTime, languageName } from "./format.js";
import { createJobsView } from "./jobs.js";
import { createQueriesView } from "./queries.js";
import { createSettingsView } from "./settings.js";
import { createTabs } from "./tabs.js";

const REFRESH_MS = 60000;
const MAX_COUNT_FALLBACK = 100;

const ui = {
  stockList: byId("stock-list"),
  stockMessage: byId("stock-message"),
  stockEmpty: byId("stock-empty"),
  stockUpdated: byId("stock-updated"),
  language: byId("language"),
  queriesLanguage: byId("queries-language"),
  topicList: byId("topic-list"),
  topicsLanguage: byId("topics-language"),
  topicsMessage: byId("topics-message"),
  unclassified: byId("unclassified"),
  form: byId("delivery-form"),
  count: byId("count"),
  countHint: byId("count-hint"),
  deliver: byId("deliver"),
  available: byId("available"),
  deliveryStatus: byId("delivery-status"),
  refresh: byId("refresh"),
};

const archives = createArchivesView({
  list: byId("archives"),
  empty: byId("archives-empty"),
  message: byId("archives-message"),
  summary: byId("archives-summary"),
});

const jobs = createJobsView(
  {
    container: byId("delivery-job"),
    title: byId("job-title"),
    state: byId("job-state"),
    message: byId("job-message"),
    timer: byId("job-timer"),
    link: /** @type {HTMLAnchorElement} */ (byId("job-link")),
    check: /** @type {HTMLButtonElement} */ (byId("job-check")),
  },
  { onFinished: (job) => void refreshAfterJob(job) },
);

const queries = createQueriesView({
  language: /** @type {HTMLSelectElement} */ (ui.queriesLanguage),
  search: /** @type {HTMLInputElement} */ (byId("queries-search")),
  summary: byId("queries-summary"),
  message: byId("queries-message"),
  list: byId("queries-list"),
  empty: byId("queries-empty"),
  form: /** @type {HTMLFormElement} */ (byId("query-form")),
  input: /** @type {HTMLInputElement} */ (byId("new-query")),
  add: /** @type {HTMLButtonElement} */ (byId("query-add")),
  status: byId("query-status"),
  refresh: /** @type {HTMLButtonElement} */ (byId("queries-refresh")),
});

const settings = createSettingsView(
  {
    form: /** @type {HTMLFormElement} */ (byId("settings-form")),
    languages: byId("settings-languages"),
    batch: /** @type {HTMLInputElement} */ (byId("settings-batch")),
    interval: /** @type {HTMLInputElement} */ (byId("settings-interval")),
    maxDuration: /** @type {HTMLInputElement} */ (byId("settings-max-duration")),
    maxQueries: /** @type {HTMLInputElement} */ (byId("settings-max-queries")),
    filterMax: /** @type {HTMLInputElement} */ (byId("settings-filter-max")),
    workers: /** @type {HTMLInputElement} */ (byId("settings-workers")),
    deliveryMax: /** @type {HTMLInputElement} */ (byId("settings-delivery-max")),
    preview: /** @type {HTMLButtonElement} */ (byId("settings-preview")),
    apply: /** @type {HTMLButtonElement} */ (byId("settings-apply")),
    status: byId("settings-status"),
    diff: byId("settings-diff"),
    diffText: byId("settings-diff-text"),
    backup: byId("settings-backup"),
    message: byId("settings-message"),
    refresh: /** @type {HTMLButtonElement} */ (byId("settings-refresh")),
  },
  { onLimits: applyLimits },
);

const tabs = createTabs({
  tabs: [byId("tab-delivery"), byId("tab-queries"), byId("tab-settings")],
  panels: [byId("panel-delivery"), byId("panel-queries"), byId("panel-settings")],
  onChange: onTabChange,
});

const state = {
  /** @type {Array<{language: string, stock: number, target: number, titled: number, untitled: number, videoBytes: number}>} */
  languages: [],
  /** @type {string | null} */
  language: null,
  /** @type {Array<{query: string, count: number}>} */
  topics: [],
  unclassified: 0,
  maxCount: MAX_COUNT_FALLBACK,
  busy: false,
  refreshing: false,
  queriesLoaded: false,
};

/**
 * @param {Record<string, unknown>} job
 */
async function refreshAfterJob(job) {
  const ready = job.status === "ready" || job.status === "ready_local";
  setStatus(
    job.status === "ready"
      ? "Архив готов — скачайте его по ссылке."
      : job.status === "ready_local"
        ? "Архив готов локально — скачайте его с сервера."
        : "",
    false,
  );
  await loadStock();
  await Promise.allSettled([
    loadTopics(),
    archives.load(ready ? String(job.request_id) : null),
  ]);
  if (state.queriesLoaded && state.language !== null) {
    await queries.load(state.language);
  }
}

async function loadStock() {
  const previous = state.language;
  try {
    const data = await requestJson("/api/v1/stock");
    const rows = Array.isArray(data.languages) ? data.languages : [];
    state.languages = rows.map((row) => ({
      language: String(row.language),
      stock: Number(row.stock) || 0,
      target: Number(row.target) || 0,
      titled: Number(row.titled) || 0,
      untitled: Number(row.untitled) || 0,
      videoBytes: Number(row.video_bytes) || 0,
    }));
    if (!state.languages.some((row) => row.language === state.language)) {
      state.language = state.languages.length > 0 ? state.languages[0].language : null;
    }
    renderStock();
    syncLanguageSelects();
    setMessage(ui.stockMessage, "");
    setText(ui.stockUpdated, `обновлено ${formatDateTime(String(data.generated_at))}`);
    if (state.language !== previous) {
      state.topics = [];
      state.unclassified = 0;
      renderTopics();
    }
  } catch (error) {
    setMessage(ui.stockMessage, error.message);
  }
  return state.language !== previous;
}

function renderStock() {
  const items = state.languages.map((row) => {
    const item = el("li", "stock-item");
    const head = el("div", "stock-head");
    head.append(
      el("span", "stock-name", languageName(row.language)),
      el("span", "stock-value", `${formatCount(row.stock)} из ${formatCount(row.target)}`),
    );

    const bar = el("div", "bar");
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-label", `${languageName(row.language)}: ${row.stock} из ${row.target}`);
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", String(row.target));
    bar.setAttribute("aria-valuenow", String(row.stock));
    const fill = el("span", "bar-fill");
    const share = row.target > 0 ? (row.stock / row.target) * 100 : 0;
    fill.style.width = `${Math.min(100, Math.max(0, share))}%`;
    bar.append(fill);

    const meta = el(
      "p",
      "stock-meta",
      `${formatCount(row.titled)} с названием · ${formatCount(row.untitled)} без · ${formatBytes(row.videoBytes)}`,
    );

    item.append(head, bar, meta);
    return item;
  });

  ui.stockList.replaceChildren(...items);
  toggle(ui.stockList, items.length !== 0);
  toggle(ui.stockEmpty, !state.languages.some((row) => row.stock > 0));
}

function syncLanguageSelects() {
  fillLanguageSelect(ui.language, true);
  fillLanguageSelect(ui.queriesLanguage, false);
  updateAvailable();
}

/**
 * @param {HTMLSelectElement} select
 * @param {boolean} withStock
 */
function fillLanguageSelect(select, withStock) {
  if (state.languages.length === 0) {
    const placeholder = el("option", "", "Нет доступных языков");
    placeholder.disabled = true;
    placeholder.selected = true;
    select.replaceChildren(placeholder);
    select.disabled = true;
    return;
  }
  const options = state.languages.map((row) => {
    const label = withStock
      ? `${languageName(row.language)} — ${formatCount(row.titled)}`
      : languageName(row.language);
    const option = el("option", "", label);
    option.value = row.language;
    return option;
  });
  select.replaceChildren(...options);
  select.disabled = false;
  if (state.language !== null) {
    select.value = state.language;
  }
}

async function loadTopics() {
  const language = state.language;
  if (language === null) {
    state.topics = [];
    state.unclassified = 0;
    renderTopics();
    return;
  }
  try {
    const data = await requestJson(
      `/api/v1/content/topics?language=${encodeURIComponent(language)}`,
    );
    if (language !== state.language) {
      return;
    }
    state.topics = (Array.isArray(data.topics) ? data.topics : []).map((row) => ({
      query: String(row.query),
      count: Number(row.count) || 0,
    }));
    state.unclassified = Number(data.unclassified) || 0;
    setMessage(ui.topicsMessage, "");
  } catch (error) {
    if (language === state.language) {
      setMessage(ui.topicsMessage, error.message);
    }
  }
  renderTopics();
}

function renderTopics() {
  const total = stockTotal();
  const cards = [buildTopic("Все темы", total)];
  for (const topic of state.topics) {
    cards.push(buildTopic(topic.query, topic.count));
  }
  ui.topicList.replaceChildren(...cards);
  setText(ui.topicsLanguage, state.language === null ? "" : languageName(state.language));
  toggle(ui.unclassified, state.unclassified !== 0);
  ui.unclassified.textContent = state.unclassified
    ? `Без темы: ${formatCount(state.unclassified)} — они входят в «Все темы».`
    : "";
  updateAvailable();
}

/**
 * @param {string} label
 * @param {number} count
 * @returns {HTMLElement}
 */
function buildTopic(label, count) {
  const row = el("li", "topic");
  row.append(
    el("span", "topic-name", label),
    el("span", "topic-count", formatCount(count)),
  );
  return row;
}

function stockTotal() {
  const row = state.languages.find((item) => item.language === state.language);
  return row ? row.titled : 0;
}

function updateAvailable() {
  const total = stockTotal();
  const row = state.languages.find((item) => item.language === state.language);
  const stock = row ? row.stock : 0;
  const limit = Math.max(1, Math.min(state.maxCount, total));
  ui.count.max = String(limit);
  const current = Number.parseInt(ui.count.value, 10);
  if (!Number.isFinite(current) || current < 1) {
    ui.count.value = "1";
  } else if (current > limit) {
    ui.count.value = String(limit);
  }

  if (stock === 0) {
    ui.available.textContent = "видео пока нет";
    ui.countHint.textContent = "Видео этого языка пока нет — выберите другой язык.";
  } else if (total === 0) {
    ui.available.textContent = "ждём обработку названий";
    ui.countHint.textContent =
      "Видео уже скачаны, но названий пока нет — они появятся здесь после обработки.";
  } else {
    ui.available.textContent = `доступно ${formatCount(total)}`;
    const suffix = state.maxCount < total ? " (лимит из настроек)" : "";
    ui.countHint.textContent = `Не больше ${formatCount(limit)} за раз${suffix}.`;
  }
  ui.count.disabled = state.busy || total === 0;
  ui.deliver.disabled = state.busy || total === 0;
  ui.deliver.title =
    stock === 0
      ? "В стоке выбранного языка нет видео"
      : total === 0
        ? "Ждём обработку названий видео"
        : "";
}

/**
 * @param {boolean} busy
 */
function setBusy(busy) {
  state.busy = busy;
  ui.form.setAttribute("aria-busy", String(busy));
  ui.deliver.classList.toggle("is-busy", busy);
  updateAvailable();
}

/**
 * @param {string} text
 * @param {boolean} isError
 */
function setStatus(text, isError) {
  ui.deliveryStatus.textContent = text;
  ui.deliveryStatus.classList.toggle("is-error", isError);
}

/**
 * @param {string} code
 */
function selectLanguage(code) {
  if (!code || code === state.language) {
    syncLanguageSelects();
    return;
  }
  state.language = code;
  state.topics = [];
  state.unclassified = 0;
  syncLanguageSelects();
  renderTopics();
  void loadTopics();
  if (state.queriesLoaded) {
    void queries.load(code);
  }
}

/**
 * @param {SubmitEvent} event
 */
async function submitDelivery(event) {
  event.preventDefault();
  const total = stockTotal();
  if (state.busy || state.language === null || total === 0) {
    return;
  }
  const count = clampCount(ui.count.value, total);
  ui.count.value = String(count);
  const requestId = createRequestId();
  setBusy(true);
  setStatus(`Собираем ${formatCount(count)} видео. Это может занять несколько минут.`, false);
  try {
    const job = await jobs.start(state.language, count, requestId);
    if (job.status === "building") {
      setStatus("Сборка идёт. Статус обновляется автоматически.", false);
    } else if (job.status === "empty") {
      setStatus(MESSAGES.empty, true);
    } else if (job.status === "failed") {
      setStatus("Сборка не удалась. Подробности в карточке.", true);
    } else {
      setStatus("Архив готов — скачайте его по ссылке.", false);
    }
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    setBusy(false);
  }
}

/**
 * @param {string} raw
 * @param {number} total
 * @returns {number}
 */
function clampCount(raw, total) {
  const parsed = Number.parseInt(raw, 10);
  const requested = Number.isFinite(parsed) ? Math.max(1, parsed) : 1;
  return Math.min(state.maxCount, total, requested);
}

/**
 * @param {Record<string, unknown>} settings
 */
function applyLimits(settings) {
  // settings.delivery.max_count приходит из GET /api/v1/content/settings; без него — 100.
  const delivery = settings.delivery;
  const raw =
    delivery && typeof delivery === "object"
      ? Number(/** @type {Record<string, unknown>} */ (delivery).max_count)
      : Number.NaN;
  state.maxCount =
    Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : MAX_COUNT_FALLBACK;
  updateAvailable();
}

/**
 * @param {string} panelId
 */
function onTabChange(panelId) {
  if (panelId === "panel-queries" && !state.queriesLoaded && state.language !== null) {
    state.queriesLoaded = true;
    void queries.load(state.language);
  }
  if (panelId === "panel-settings") {
    void settings.load();
  }
}

async function refreshAll() {
  if (state.refreshing) {
    return;
  }
  state.refreshing = true;
  try {
    await loadStock();
    await Promise.allSettled([loadTopics(), archives.load()]);
    if (state.queriesLoaded && state.language !== null) {
      await queries.load(state.language);
    }
  } finally {
    state.refreshing = false;
  }
}

function createRequestId() {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // crypto.randomUUID недоступен в небезопасном контексте (HTTP в локальной сети).
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}

function bindEvents() {
  ui.language.addEventListener("change", () => {
    selectLanguage(ui.language.value);
  });
  ui.queriesLanguage.addEventListener("change", () => {
    selectLanguage(ui.queriesLanguage.value);
  });
  ui.form.addEventListener("submit", (event) => {
    void submitDelivery(event);
  });
  ui.refresh.addEventListener("click", () => {
    void refreshAll();
  });
  ui.count.addEventListener("change", updateAvailable);
}

async function init() {
  bindEvents();
  tabs.show("panel-delivery");
  await loadStock();
  await Promise.allSettled([loadTopics(), archives.load(), settings.load()]);
  window.setInterval(() => {
    void refreshAll();
  }, REFRESH_MS);
}

void init();
