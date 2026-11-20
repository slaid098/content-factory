/** Partner delivery page: stock, topics, archive build and archive list. */

import {
  DELIVERY_TIMEOUT_MS,
  MESSAGES,
  readBlob,
  request,
  requestJson,
} from "./api.js";
import { createArchivesView } from "./archives.js";
import { el, saveBlob } from "./dom.js";
import { formatCount, formatDateTime, languageName } from "./format.js";

const REFRESH_MS = 60000;
const MAX_COUNT = 10;

const ui = {
  stockList: byId("stock-list"),
  stockMessage: byId("stock-message"),
  stockEmpty: byId("stock-empty"),
  stockUpdated: byId("stock-updated"),
  language: byId("language"),
  topicList: byId("topic-list"),
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
  lastArchive: byId("last-archive"),
});

const state = {
  /** @type {Array<{language: string, stock: number, target: number}>} */
  languages: [],
  /** @type {string | null} */
  language: null,
  /** @type {Array<{query: string, count: number}>} */
  topics: [],
  unclassified: 0,
  /** @type {string | null} */
  selectedQuery: null,
  busy: false,
  refreshing: false,
};

function byId(id) {
  const node = document.getElementById(id);
  if (!node) {
    throw new Error(`Не найден элемент #${id}`);
  }
  return node;
}

async function loadStock() {
  try {
    const data = await requestJson("/api/v1/stock");
    const rows = Array.isArray(data.languages) ? data.languages : [];
    state.languages = rows;
    if (!rows.some((row) => row.language === state.language)) {
      state.language = rows.length > 0 ? rows[0].language : null;
      state.topics = [];
      state.unclassified = 0;
      state.selectedQuery = null;
    }
    renderStock(rows);
    renderLanguages();
    hideMessage(ui.stockMessage);
    ui.stockUpdated.textContent = `обновлено ${formatDateTime(String(data.generated_at))}`;
  } catch (error) {
    showMessage(ui.stockMessage, error.message);
  }
}

/** @param {Array<{language: string, stock: number, target: number}>} rows */
function renderStock(rows) {
  const items = rows.map((row) => {
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

    item.append(head, bar);
    return item;
  });

  ui.stockList.replaceChildren(...items);
  ui.stockList.hidden = rows.length === 0;
  ui.stockEmpty.hidden = rows.some((row) => row.stock > 0);
}

function renderLanguages() {
  if (state.languages.length === 0) {
    const placeholder = el("option", "", "Нет доступных языков");
    placeholder.disabled = true;
    placeholder.selected = true;
    ui.language.replaceChildren(placeholder);
    ui.language.disabled = true;
    return;
  }
  const options = state.languages.map((row) => {
    const option = el("option", "", `${languageName(row.language)} — ${formatCount(row.stock)}`);
    option.value = row.language;
    option.selected = row.language === state.language;
    return option;
  });
  ui.language.replaceChildren(...options);
  ui.language.disabled = false;
}

async function loadTopics() {
  if (state.language === null) {
    renderTopics();
    return;
  }
  const language = state.language;
  try {
    const data = await requestJson(
      `/api/v1/content/topics?language=${encodeURIComponent(language)}`,
    );
    if (language !== state.language) {
      return;
    }
    state.topics = Array.isArray(data.topics) ? data.topics : [];
    state.unclassified = Number(data.unclassified) || 0;
    hideMessage(ui.topicsMessage);
    renderTopics();
  } catch (error) {
    showMessage(ui.topicsMessage, error.message);
    if (state.topics.length === 0) {
      renderTopics();
    }
  }
}

function renderTopics() {
  if (
    state.selectedQuery !== null &&
    !state.topics.some((topic) => topic.query === state.selectedQuery)
  ) {
    state.selectedQuery = null;
  }

  const total = stockTotal();
  const cards = [buildTopic("", "Все темы", total, state.selectedQuery === null)];
  for (const topic of state.topics) {
    cards.push(
      buildTopic(topic.query, topic.query, topic.count, state.selectedQuery === topic.query),
    );
  }
  ui.topicList.replaceChildren(...cards);

  ui.unclassified.hidden = state.unclassified === 0;
  ui.unclassified.textContent =
    `Без темы: ${formatCount(state.unclassified)} — они входят в «Все темы».`;
  updateAvailable();
}

/**
 * @param {string} query
 * @param {string} label
 * @param {number} count
 * @param {boolean} checked
 * @returns {HTMLElement}
 */
function buildTopic(query, label, count, checked) {
  const wrapper = el("label", "topic");
  const input = el("input");
  input.type = "radio";
  input.name = "topic";
  input.value = query;
  input.checked = checked;
  input.disabled = count === 0;
  input.addEventListener("change", () => {
    state.selectedQuery = input.value === "" ? null : input.value;
    updateAvailable();
  });

  const body = el("span", "topic-body");
  body.append(el("span", "topic-name", label), el("span", "topic-count", formatCount(count)));
  wrapper.append(input, body);
  return wrapper;
}

function stockTotal() {
  const row = state.languages.find((item) => item.language === state.language);
  return row ? row.stock : 0;
}

function availableTotal() {
  if (state.selectedQuery === null) {
    return stockTotal();
  }
  const topic = state.topics.find((item) => item.query === state.selectedQuery);
  return topic ? topic.count : 0;
}

function updateAvailable() {
  const total = availableTotal();
  const limit = Math.max(1, Math.min(MAX_COUNT, total));
  ui.count.max = String(limit);
  const current = Number.parseInt(ui.count.value, 10);
  if (!Number.isFinite(current) || current < 1) {
    ui.count.value = "1";
  } else if (current > limit) {
    ui.count.value = String(limit);
  }

  ui.available.textContent = total > 0 ? `доступно ${formatCount(total)}` : "видео пока нет";
  ui.countHint.textContent =
    total > 0
      ? `Не больше ${Math.min(MAX_COUNT, total)} за раз.`
      : "Выберите другую тему или дождитесь поступления видео.";
  ui.count.disabled = total === 0;
  ui.deliver.disabled = state.busy || total === 0;
  ui.deliver.title = total === 0 ? "В выбранной теме нет видео" : "";
}

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

async function submitDelivery(event) {
  event.preventDefault();
  const total = availableTotal();
  if (state.busy || state.language === null || total === 0) {
    return;
  }

  const parsed = Number.parseInt(ui.count.value, 10);
  const count = Math.min(MAX_COUNT, total, Number.isFinite(parsed) ? Math.max(1, parsed) : 1);
  ui.count.value = String(count);

  const requestId = createRequestId();
  const body = { language: state.language, count, request_id: requestId };
  if (state.selectedQuery !== null) {
    body.query = state.selectedQuery;
  }

  setBusy(true);
  setStatus(`Собираем ${count} видео. Это может занять несколько минут.`, false);
  try {
    const response = await request(
      "/api/v1/content/deliveries",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      DELIVERY_TIMEOUT_MS,
    );
    if (response.status === 204) {
      setStatus(MESSAGES.emptyTopic, true);
      await refreshCounts();
      return;
    }
    saveBlob(await readBlob(response), `delivery-${requestId}.zip`);
    setStatus(`Архив delivery-${requestId}.zip скачан.`, false);
    await loadStock();
    await Promise.allSettled([loadTopics(), archives.load(requestId)]);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function refreshCounts() {
  await loadStock();
  await Promise.allSettled([loadTopics()]);
}

async function refreshAll() {
  if (state.refreshing) {
    return;
  }
  state.refreshing = true;
  try {
    await loadStock();
    await Promise.allSettled([loadTopics(), archives.load()]);
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

/**
 * @param {HTMLElement} node
 * @param {string} text
 */
function showMessage(node, text) {
  node.textContent = text;
  node.hidden = false;
}

/** @param {HTMLElement} node */
function hideMessage(node) {
  node.textContent = "";
  node.hidden = true;
}

function bindEvents() {
  ui.language.addEventListener("change", () => {
    state.language = ui.language.value;
    state.selectedQuery = null;
    state.topics = [];
    state.unclassified = 0;
    renderTopics();
    void loadTopics();
  });
  ui.form.addEventListener("submit", (event) => {
    void submitDelivery(event).catch((error) => {
      setStatus(error.message, true);
    });
  });
  ui.refresh.addEventListener("click", () => {
    void refreshAll();
  });
}

async function init() {
  bindEvents();
  await loadStock();
  await loadTopics();
  await archives.load();
  window.setInterval(() => {
    void refreshAll();
  }, REFRESH_MS);
}

void init();
