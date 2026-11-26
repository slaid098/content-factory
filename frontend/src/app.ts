/** Partner delivery page: stock counters, archive build, queries and archives. */

import "./styles.css";

import { createArchivesView } from "./archives";
import { byId, el, setMessage, setText, toggle } from "./dom";
import { createJobsView, type DeliveryAddress } from "./jobs";
import { formatDateTime } from "./lib/datetime";
import { errorMessage } from "./lib/errors";
import { formatCount, languageName } from "./lib/format";
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
}

interface GeneralRow {
  language: string;
  stock: number;
  target: number;
}

interface ProfileRow {
  id: string;
  stock: number;
  target: number;
}

type DeliverySource = "general" | "profile";

const ui = {
  stockPanel: byId("stock-panel"),
  stockLoading: byId("stock-loading"),
  stockMessage: byId("stock-message"),
  stockUpdated: byId("stock-updated"),
  generalLanguage: byId("general-language"),
  generalValue: byId("general-value"),
  generalBar: byId("general-bar"),
  generalBarFill: byId("general-bar-fill"),
  generalHint: byId("general-hint"),
  profilesTotal: byId("profiles-total"),
  profilesStatHint: byId("profiles-stat-hint"),
  profilesStock: byId("profiles-stock"),
  profilesStockMessage: byId("profiles-stock-message"),
  languageField: byId("language-field"),
  language: byId<HTMLSelectElement>("language"),
  profileField: byId("profile-field"),
  profileSelect: byId<HTMLSelectElement>("delivery-profile"),
  sourceGeneral: byId<HTMLButtonElement>("source-general"),
  sourceProfile: byId<HTMLButtonElement>("source-profile"),
  form: byId<HTMLFormElement>("delivery-form"),
  count: byId<HTMLInputElement>("count"),
  countHint: byId("count-hint"),
  deliver: byId<HTMLButtonElement>("deliver"),
  available: byId("available"),
  deliveryStatus: byId("delivery-status"),
  refresh: byId<HTMLButtonElement>("refresh"),
  queriesLanguage: byId<HTMLSelectElement>("queries-language"),
  settingsMessage: byId("settings-message"),
};

const archives = createArchivesView({
  list: byId("archives"),
  empty: byId("archives-empty"),
  message: byId("archives-message"),
  summary: byId("archives-summary"),
  loading: byId("archives-loading"),
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

const profilesView = createProfilesView();
const settingsView = createSettingsView({ onLimits: applyRuntimeLimits });

const tabs = createTabs({
  tabs: [byId("tab-delivery"), byId("tab-queries"), byId("tab-profiles"), byId("tab-settings")],
  panels: [
    byId("panel-delivery"),
    byId("panel-queries"),
    byId("panel-profiles"),
    byId("panel-settings"),
  ],
  onChange: onTabChange,
});

const state = {
  languages: [] as LanguageRow[],
  general: [] as GeneralRow[],
  profiles: [] as ProfileRow[],
  language: null as string | null,
  profile: null as string | null,
  source: "general" as DeliverySource,
  maxCount: MAX_COUNT_FALLBACK,
  busy: false,
  refreshing: false,
  queriesLoaded: false,
};

async function loadStock(): Promise<void> {
  const previous = state.language;
  ui.stockPanel.setAttribute("aria-busy", "true");
  toggle(ui.stockLoading, true);
  try {
    const data = asObject(await requestJson("/api/v1/stock"));
    state.languages = rows(data.languages).map((item) => ({
      language: String(item.language),
      stock: Number(item.stock) || 0,
      target: Number(item.target) || 0,
    }));
    state.general = rows(data.general).map((item) => ({
      language: String(item.language),
      stock: Number(item.stock) || 0,
      target: Number(item.target) || 0,
    }));
    if (!state.languages.some((row) => row.language === state.language)) {
      state.language = state.languages[0]?.language ?? null;
    }
    renderGeneral();
    syncLanguageSelects();
    setMessage(ui.stockMessage, "");
    setText(ui.stockUpdated, `обновлено ${formatDateTime(String(data.generated_at))}`);
    if (state.language !== previous && state.queriesLoaded && state.language !== null) {
      void queries.load(state.language);
    }
  } catch (error) {
    setMessage(ui.stockMessage, errorMessage(error));
  } finally {
    ui.stockPanel.setAttribute("aria-busy", "false");
    toggle(ui.stockLoading, false);
  }
}

async function loadProfiles(): Promise<void> {
  try {
    const data = asObject(await requestJson("/api/v1/content/profiles"));
    state.profiles = rows(data.profiles).map((item) => ({
      id: String(item.id),
      stock: Number(item.stock) || 0,
      target: Number(item.target) || 0,
    }));
    if (!state.profiles.some((row) => row.id === state.profile)) {
      state.profile = state.profiles[0]?.id ?? null;
    }
    renderProfiles();
    syncProfileSelect();
    setMessage(ui.profilesStockMessage, "");
  } catch (error) {
    setMessage(ui.profilesStockMessage, errorMessage(error));
  }
}

async function loadSettings(): Promise<void> {
  try {
    const data = asObject(await requestJson("/api/v1/content/settings"));
    applyRuntimeLimits(data);
    setMessage(ui.settingsMessage, "");
  } catch (error) {
    setMessage(ui.settingsMessage, errorMessage(error));
  }
  updateAvailability();
}

function applyRuntimeLimits(settings: Record<string, unknown>): void {
  const delivery = asObject(settings.delivery);
  const raw = Number(delivery.max_count);
  state.maxCount = Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : MAX_COUNT_FALLBACK;
  updateAvailability();
}

function renderGeneral(): void {
  const row = generalRow();
  const stock = row?.stock ?? 0;
  const target = row?.target ?? 0;
  setText(ui.generalLanguage, state.language === null ? "—" : languageName(state.language));
  setText(ui.generalValue, `${formatCount(stock)} / ${formatCount(target)}`);
  setBar(ui.generalBar, ui.generalBarFill, stock, target, "Общетематичные");
  setText(
    ui.generalHint,
    stock === 0 ? "Видео без профиля пока нет." : `Готово к выдаче: ${formatCount(stock)}.`,
  );
  updateAvailability();
}

function renderProfiles(): void {
  const total = state.profiles.reduce((sum, row) => sum + row.stock, 0);
  setText(ui.profilesTotal, formatCount(total));
  setText(
    ui.profilesStatHint,
    state.profiles.length === 0
      ? "Профилей пока нет."
      : `Профилей: ${formatCount(state.profiles.length)} · видео: ${formatCount(total)}.`,
  );
  const items = state.profiles.map((row) => {
    const item = el("li", "mini-row");
    item.append(el("span", "mini-name", row.id));
    item.append(el("span", "mini-value", `${formatCount(row.stock)} / ${formatCount(row.target)}`));
    const bar = el("div", "bar mini-bar");
    const fill = el("span", "bar-fill");
    bar.append(fill);
    setBar(bar, fill, row.stock, row.target, row.id);
    item.append(bar);
    return item;
  });
  ui.profilesStock.replaceChildren(...items);
  toggle(ui.profilesStock, items.length !== 0);
  updateAvailability();
}

function setBar(
  bar: HTMLElement,
  fill: HTMLElement,
  stock: number,
  target: number,
  label: string,
): void {
  const share = target > 0 ? (stock / target) * 100 : 0;
  fill.style.width = `${Math.min(100, Math.max(0, share))}%`;
  bar.setAttribute("aria-label", `${label}: ${stock} из ${target}`);
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", String(Math.max(target, stock, 1)));
  bar.setAttribute("aria-valuenow", String(stock));
}

function syncLanguageSelects(): void {
  fillLanguageSelect(ui.language);
  fillLanguageSelect(ui.queriesLanguage);
  updateAvailability();
}

function fillLanguageSelect(select: HTMLSelectElement): void {
  if (state.languages.length === 0) {
    const placeholder = el("option", "", "Нет доступных языков");
    placeholder.disabled = true;
    placeholder.selected = true;
    select.replaceChildren(placeholder);
    select.disabled = true;
    return;
  }
  const options = state.languages.map((row) => {
    const option = el("option", "", languageName(row.language));
    option.value = row.language;
    return option;
  });
  select.replaceChildren(...options);
  select.disabled = false;
  if (state.language !== null) {
    select.value = state.language;
  }
}

function syncProfileSelect(): void {
  if (state.profiles.length === 0) {
    const placeholder = el("option", "", "Нет доступных профилей");
    placeholder.disabled = true;
    placeholder.selected = true;
    ui.profileSelect.replaceChildren(placeholder);
    ui.profileSelect.disabled = true;
    return;
  }
  const options = state.profiles.map((row) => {
    const option = el("option", "", row.id);
    option.value = row.id;
    return option;
  });
  ui.profileSelect.replaceChildren(...options);
  ui.profileSelect.disabled = false;
  if (state.profile !== null) {
    ui.profileSelect.value = state.profile;
  }
}

function generalRow(): GeneralRow | null {
  return state.general.find((row) => row.language === state.language) ?? null;
}

function selectedProfile(): ProfileRow | null {
  return state.profiles.find((row) => row.id === state.profile) ?? null;
}

function sourceStock(): number {
  if (state.source === "profile") {
    return selectedProfile()?.stock ?? 0;
  }
  return generalRow()?.stock ?? 0;
}

function updateAvailability(): void {
  const stock = sourceStock();
  const limit = Math.max(1, Math.min(state.maxCount, stock));
  ui.count.max = String(limit);
  const current = Number.parseInt(ui.count.value, 10);
  if (!Number.isFinite(current) || current < 1) {
    ui.count.value = "1";
  } else if (current > limit) {
    ui.count.value = String(limit);
  }

  if (stock === 0) {
    setText(ui.available, "видео пока нет");
    setText(ui.countHint, "Видео этого источника пока нет.");
  } else {
    setText(ui.available, `доступно ${formatCount(stock)}`);
    const suffix = state.maxCount < stock ? " (лимит из настроек)" : "";
    setText(ui.countHint, `Не больше ${formatCount(limit)} за раз${suffix}.`);
  }

  const disabled = state.busy || stock === 0;
  ui.count.disabled = disabled;
  ui.deliver.disabled = disabled;
  ui.deliver.title =
    state.source === "profile" && state.profiles.length === 0
      ? "Нет доступных профилей"
      : stock === 0
        ? "В стоке выбранного источника нет видео"
        : "";
}

function setBusy(busy: boolean): void {
  state.busy = busy;
  ui.form.setAttribute("aria-busy", String(busy));
  ui.deliver.classList.toggle("is-busy", busy);
  updateAvailability();
}

function setStatus(text: string, isError: boolean): void {
  ui.deliveryStatus.textContent = text;
  ui.deliveryStatus.classList.toggle("is-error", isError);
}

function setSource(source: DeliverySource): void {
  state.source = source;
  const general = source === "general";
  ui.sourceGeneral.setAttribute("aria-pressed", String(general));
  ui.sourceProfile.setAttribute("aria-pressed", String(!general));
  ui.sourceGeneral.classList.toggle("is-active", general);
  ui.sourceProfile.classList.toggle("is-active", !general);
  toggle(ui.languageField, general);
  toggle(ui.profileField, !general);
  updateAvailability();
}

function selectLanguage(code: string): void {
  if (!code || code === state.language) {
    syncLanguageSelects();
    return;
  }
  state.language = code;
  renderGeneral();
  syncLanguageSelects();
  if (state.queriesLoaded) {
    void queries.load(code);
  }
}

function selectProfile(id: string): void {
  if (!id) {
    syncProfileSelect();
    return;
  }
  state.profile = id;
  syncProfileSelect();
  updateAvailability();
}

function deliveryAddress(): DeliveryAddress | null {
  if (state.source === "profile") {
    return state.profile === null ? null : { profile: state.profile };
  }
  return state.language === null ? null : { language: state.language };
}

async function submitDelivery(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  const stock = sourceStock();
  if (state.busy || stock === 0) {
    return;
  }
  const address = deliveryAddress();
  if (address === null) {
    return;
  }
  const count = clampCount(ui.count.value, stock);
  ui.count.value = String(count);
  setBusy(true);
  setStatus(`Собираем ${formatCount(count)} видео. Это может занять несколько минут.`, false);
  try {
    const job = await jobs.start({ address, count, requestId: createRequestId() });
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

function clampCount(raw: string, stock: number): number {
  const parsed = Number.parseInt(raw, 10);
  const requested = Number.isFinite(parsed) ? Math.max(1, parsed) : 1;
  return Math.min(state.maxCount, stock, requested);
}

async function refreshAfterJob(job: Record<string, unknown>): Promise<void> {
  const status = String(job.status);
  setStatus(
    status === "ready"
      ? "Архив готов — скачайте его по ссылке."
      : status === "ready_local"
        ? "Архив готов локально — скачайте его с сервера."
        : "",
    false,
  );
  await loadStock();
  await Promise.allSettled([
    loadProfiles(),
    archives.load(status === "ready" || status === "ready_local" ? String(job.request_id) : null),
    loadSettings(),
  ]);
  if (state.queriesLoaded && state.language !== null) {
    await queries.load(state.language);
  }
}

function onTabChange(panelId: string): void {
  if (panelId === "panel-queries" && !state.queriesLoaded) {
    state.queriesLoaded = true;
    if (state.language !== null) {
      void queries.load(state.language);
    }
  }
  if (panelId === "panel-profiles") {
    void profilesView.load();
  }
  if (panelId === "panel-settings") {
    void settingsView.load();
  }
}

async function refreshAll(): Promise<void> {
  if (state.refreshing) {
    return;
  }
  state.refreshing = true;
  try {
    await loadStock();
    await Promise.allSettled([loadProfiles(), archives.load(), loadSettings()]);
    if (state.queriesLoaded && state.language !== null) {
      await queries.load(state.language);
    }
    if (!byId("panel-profiles").hidden) {
      await profilesView.load();
    }
    if (!byId("panel-settings").hidden) {
      await settingsView.load(true);
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
  ui.profileSelect.addEventListener("change", () => {
    selectProfile(ui.profileSelect.value);
  });
  ui.sourceGeneral.addEventListener("click", () => {
    setSource("general");
  });
  ui.sourceProfile.addEventListener("click", () => {
    setSource("profile");
  });
  ui.form.addEventListener("submit", (event) => {
    void submitDelivery(event);
  });
  ui.refresh.addEventListener("click", () => {
    void refreshAll();
  });
  ui.count.addEventListener("change", updateAvailability);
}

async function init(): Promise<void> {
  bindEvents();
  setSource("general");
  tabs.show("panel-delivery");
  await loadStock();
  await Promise.allSettled([loadProfiles(), archives.load(), loadSettings()]);
  window.setInterval(() => {
    void refreshAll();
  }, REFRESH_MS);
}

function rows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => isObject(item))
    : [];
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asObject(value: unknown): Record<string, unknown> {
  return isObject(value) ? value : {};
}

void init();
