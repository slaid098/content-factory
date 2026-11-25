/** Partner delivery page: stock, async archive build, queries and settings. */

import "./styles.css";

import { createArchivesView } from "./archives";
import { byId, el, setMessage, setText, toggle } from "./dom";
import { createJobsView } from "./jobs";
import { formatDateTime } from "./lib/datetime";
import { errorMessage } from "./lib/errors";
import { formatBytes, formatCount, languageName } from "./lib/format";
import { MESSAGES, requestJson } from "./lib/http";
import { createProfilesView } from "./profiles";
import { createQueriesView } from "./queries";
import { createSettingsView } from "./settings";
import { createTabs } from "./tabs";

const REFRESH_MS = 60000;
const MAX_COUNT_FALLBACK = 100;

interface LanguageRow {
  language: string;
  stock: number;
  target: number;
  titled: number;
  untitled: number;
  videoBytes: number;
}

interface TopicRow {
  query: string;
  count: number;
}

const ui = {
  stockList: byId("stock-list"),
  stockMessage: byId("stock-message"),
  stockEmpty: byId("stock-empty"),
  stockUpdated: byId("stock-updated"),
  language: byId<HTMLSelectElement>("language"),
  queriesLanguage: byId<HTMLSelectElement>("queries-language"),
  topicList: byId("topic-list"),
  topicsLanguage: byId("topics-language"),
  topicsMessage: byId("topics-message"),
  unclassified: byId("unclassified"),
  form: byId<HTMLFormElement>("delivery-form"),
  count: byId<HTMLInputElement>("count"),
  countHint: byId("count-hint"),
  deliver: byId<HTMLButtonElement>("deliver"),
  available: byId("available"),
  deliveryStatus: byId("delivery-status"),
  refresh: byId<HTMLButtonElement>("refresh"),
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
    link: byId<HTMLAnchorElement>("job-link"),
    check: byId<HTMLButtonElement>("job-check"),
  },
  { onFinished: (job) => void refreshAfterJob(job) },
);

const profiles = createProfilesView({
  list: byId("profiles-list"),
  message: byId("profiles-message"),
  loading: byId("profiles-loading"),
  empty: byId("profiles-empty"),
  refresh: byId<HTMLButtonElement>("profiles-refresh"),
});

const queries = createQueriesView({
  language: ui.queriesLanguage,
  search: byId<HTMLInputElement>("queries-search"),
  summary: byId("queries-summary"),
  message: byId("queries-message"),
  list: byId("queries-list"),
  empty: byId("queries-empty"),
  form: byId<HTMLFormElement>("query-form"),
  input: byId<HTMLInputElement>("new-query"),
  add: byId<HTMLButtonElement>("query-add"),
  status: byId("query-status"),
  refresh: byId<HTMLButtonElement>("queries-refresh"),
});

const settings = createSettingsView(
  {
    form: byId<HTMLFormElement>("settings-form"),
    languages: byId("settings-languages"),
    batch: byId<HTMLInputElement>("settings-batch"),
    interval: byId<HTMLInputElement>("settings-interval"),
    maxDuration: byId<HTMLInputElement>("settings-max-duration"),
    maxQueries: byId<HTMLInputElement>("settings-max-queries"),
    filterMax: byId<HTMLInputElement>("settings-filter-max"),
    workers: byId<HTMLInputElement>("settings-workers"),
    deliveryMax: byId<HTMLInputElement>("settings-delivery-max"),
    preview: byId<HTMLButtonElement>("settings-preview"),
    apply: byId<HTMLButtonElement>("settings-apply"),
    status: byId("settings-status"),
    diff: byId("settings-diff"),
    diffText: byId("settings-diff-text"),
    backup: byId("settings-backup"),
    message: byId("settings-message"),
    refresh: byId<HTMLButtonElement>("settings-refresh"),
  },
  { onLimits: applyLimits },
);

const tabs = createTabs({
  tabs: [byId("tab-delivery"), byId("tab-queries"), byId("tab-settings"), byId("tab-profiles")],
  panels: [
    byId("panel-delivery"),
    byId("panel-queries"),
    byId("panel-settings"),
    byId("panel-profiles"),
  ],
  onChange: onTabChange,
});

const state = {
  languages: [] as LanguageRow[],
  language: null as string | null,
  topics: [] as TopicRow[],
  unclassified: 0,
  maxCount: MAX_COUNT_FALLBACK,
  busy: false,
  refreshing: false,
  queriesLoaded: false,
  profilesLoaded: false,
  activePanel: "panel-delivery",
};

async function refreshAfterJob(job: Record<string, unknown>): Promise<void> {
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
  await Promise.allSettled([loadTopics(), archives.load(ready ? String(job.request_id) : null)]);
  if (state.queriesLoaded && state.language !== null) {
    await queries.load(state.language);
  }
}

async function loadStock(): Promise<boolean> {
  const previous = state.language;
  try {
    const data = asObject(await requestJson("/api/v1/stock"));
    const rows = Array.isArray(data.languages) ? data.languages : [];
    state.languages = rows.map((row) => {
      const item = asObject(row);
      return {
        language: String(item.language),
        stock: Number(item.stock) || 0,
        target: Number(item.target) || 0,
        titled: Number(item.titled) || 0,
        untitled: Number(item.untitled) || 0,
        videoBytes: Number(item.video_bytes) || 0,
      };
    });
    if (!state.languages.some((row) => row.language === state.language)) {
      const first = state.languages[0];
      state.language = first ? first.language : null;
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
    setMessage(ui.stockMessage, errorMessage(error));
  }
  return state.language !== previous;
}

function renderStock(): void {
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

function syncLanguageSelects(): void {
  fillLanguageSelect(ui.language, true);
  fillLanguageSelect(ui.queriesLanguage, false);
  updateAvailable();
}

function fillLanguageSelect(select: HTMLSelectElement, withStock: boolean): void {
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

async function loadTopics(): Promise<void> {
  const language = state.language;
  if (language === null) {
    state.topics = [];
    state.unclassified = 0;
    renderTopics();
    return;
  }
  try {
    const data = asObject(
      await requestJson(`/api/v1/content/topics?language=${encodeURIComponent(language)}`),
    );
    if (language !== state.language) {
      return;
    }
    state.topics = (Array.isArray(data.topics) ? data.topics : []).map((row) => {
      const item = asObject(row);
      return {
        query: String(item.query),
        count: Number(item.count) || 0,
      };
    });
    state.unclassified = Number(data.unclassified) || 0;
    setMessage(ui.topicsMessage, "");
  } catch (error) {
    if (language === state.language) {
      setMessage(ui.topicsMessage, errorMessage(error));
    }
  }
  renderTopics();
}

function renderTopics(): void {
  const total = stockCount();
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

function buildTopic(label: string, count: number): HTMLElement {
  const row = el("li", "topic");
  row.append(el("span", "topic-name", label), el("span", "topic-count", formatCount(count)));
  return row;
}

function stockCount(): number {
  const row = state.languages.find((item) => item.language === state.language);
  return row ? row.stock : 0;
}

function stockTotal(): number {
  const row = state.languages.find((item) => item.language === state.language);
  return row ? row.titled : 0;
}

function updateAvailable(): void {
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

function setBusy(busy: boolean): void {
  state.busy = busy;
  ui.form.setAttribute("aria-busy", String(busy));
  ui.deliver.classList.toggle("is-busy", busy);
  updateAvailable();
}

function setStatus(text: string, isError: boolean): void {
  ui.deliveryStatus.textContent = text;
  ui.deliveryStatus.classList.toggle("is-error", isError);
}

function selectLanguage(code: string): void {
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

async function submitDelivery(event: SubmitEvent): Promise<void> {
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
    setStatus(errorMessage(error), true);
  } finally {
    setBusy(false);
  }
}

function clampCount(raw: string, total: number): number {
  const parsed = Number.parseInt(raw, 10);
  const requested = Number.isFinite(parsed) ? Math.max(1, parsed) : 1;
  return Math.min(state.maxCount, total, requested);
}

function applyLimits(settings: Record<string, unknown>): void {
  // settings.delivery.max_count приходит из GET /api/v1/content/settings; без него — 100.
  const delivery = settings.delivery;
  const raw =
    delivery && typeof delivery === "object" && !Array.isArray(delivery)
      ? Number((delivery as Record<string, unknown>).max_count)
      : Number.NaN;
  state.maxCount = Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : MAX_COUNT_FALLBACK;
  updateAvailable();
}

function onTabChange(panelId: string): void {
  state.activePanel = panelId;
  if (panelId === "panel-queries" && !state.queriesLoaded && state.language !== null) {
    state.queriesLoaded = true;
    void queries.load(state.language);
  }
  if (panelId === "panel-settings") {
    void settings.load();
  }
  if (panelId === "panel-profiles" && !state.profilesLoaded) {
    state.profilesLoaded = true;
    void profiles.load();
  }
}

async function refreshAll(): Promise<void> {
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

function createRequestId(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // crypto.randomUUID недоступен в небезопасном контексте (HTTP в локальной сети).
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}

function bindEvents(): void {
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

async function init(): Promise<void> {
  bindEvents();
  tabs.show("panel-delivery");
  await loadStock();
  await Promise.allSettled([loadTopics(), archives.load(), settings.load()]);
  window.setInterval(() => {
    void refreshAll();
    if (state.activePanel === "panel-profiles") {
      void profiles.load();
    }
  }, REFRESH_MS);
}

function asObject(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

void init();
