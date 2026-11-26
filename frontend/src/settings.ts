/** Global settings: read-only generator panel and the editable runtime form. */

import { byId, el, setMessage, toggle } from "./dom";
import { errorMessage } from "./lib/errors";
import { languageName } from "./lib/format";
import { ApiError, MESSAGES, requestJson } from "./lib/http";

const SETTINGS_URL = "/api/v1/content/settings";
const GENERATOR_URL = "/api/v1/content/settings/generator";
const PREVIEW_URL = "/api/v1/content/settings/preview";

const GENERATOR_FIELDS: Array<[string, string]> = [
  ["rent_pool_size", "Пул аренды"],
  ["max_servers", "Максимум серверов"],
  ["videos_per_server", "Видео на сервер"],
  ["offer_refresh_seconds", "Обновление предложений, с"],
  ["server_ready_timeout", "Готовность сервера, с"],
  ["comfy_health_timeout", "Comfy health, с"],
  ["comfy_job_timeout", "Comfy задание, с"],
  ["comfy_health_interval", "Comfy интервал, с"],
  ["stale_instance_hours", "Устаревший инстанс, ч"],
  ["safety_enabled", "Проверка безопасности"],
  ["safety_frames", "Кадров проверки"],
  ["safety_threshold", "Порог безопасности"],
  ["safety_nude_threshold", "Порог наготы"],
  ["safety_frame_attempts", "Попыток кадра"],
  ["max_cost_per_hour", "Максимальная цена, $/ч"],
  ["model", "Модель"],
];

export interface SettingsViewOptions {
  onLimits: (settings: Record<string, unknown>) => void;
}

export interface SettingsView {
  load: (force?: boolean) => Promise<Record<string, unknown> | null>;
}

interface LanguageRow {
  code: string;
  enabled: HTMLInputElement;
  target: HTMLInputElement;
}

function settingsUi() {
  return {
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
    generator: byId("settings-generator"),
    generatorMessage: byId("settings-generator-message"),
  };
}

/**
 * Build the settings tab: the read-only generator facts and the runtime form.
 */
export function createSettingsView(options: SettingsViewOptions): SettingsView {
  const ui = settingsUi();
  let rows: LanguageRow[] = [];
  let loaded = false;
  let busy = false;
  let previewValid = false;
  let previewPayload: string | null = null;

  async function load(force = false): Promise<Record<string, unknown> | null> {
    if (busy) {
      return null;
    }
    if (loaded && !force) {
      return null;
    }
    ui.refresh.disabled = true;
    ui.refresh.classList.add("is-busy");
    ui.form.setAttribute("aria-busy", "true");
    setMessage(ui.message, "");
    setMessage(ui.generatorMessage, "");
    setStatus("Загружаем настройки…", false);
    try {
      const [settingsResult, generatorResult] = await Promise.allSettled([
        requestJson(SETTINGS_URL),
        requestJson(GENERATOR_URL),
      ]);
      if (generatorResult.status === "fulfilled") {
        renderGenerator(generatorResult.value);
      } else {
        setMessage(ui.generatorMessage, errorMessage(generatorResult.reason));
      }
      if (settingsResult.status === "rejected") {
        loaded = false;
        setMessage(ui.message, errorMessage(settingsResult.reason));
        setStatus("Настройки недоступны.", true);
        return null;
      }
      let settings: Record<string, unknown>;
      try {
        settings = asSettings(settingsResult.value);
      } catch (error) {
        loaded = false;
        setMessage(ui.message, errorMessage(error));
        setStatus("Настройки недоступны.", true);
        return null;
      }
      fillForm(settings);
      resetDiff();
      setStatus("", false);
      loaded = true;
      options.onLimits(settings);
      return settings;
    } finally {
      ui.refresh.disabled = busy;
      ui.refresh.classList.remove("is-busy");
      ui.form.setAttribute("aria-busy", String(busy));
    }
  }

  function renderGenerator(value: unknown): void {
    const generator = asObject(value);
    const nodes = GENERATOR_FIELDS.map(([key, label]) => {
      const row = el("div", "settings-row");
      row.append(el("dt", "", label), el("dd", "", formatValue(generator[key])));
      return row;
    });
    ui.generator.replaceChildren(...nodes);
  }

  async function apply(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (!previewValid || busy) {
      if (!previewValid) {
        setStatus("Сначала нажмите «Проверить».", true);
      }
      return;
    }
    let payload: Record<string, unknown>;
    let current: string;
    try {
      payload = buildPayload();
      current = JSON.stringify(payload);
    } catch (error) {
      resetDiff();
      setStatus(errorMessage(error), true);
      return;
    }
    if (current !== previewPayload) {
      resetDiff();
      setStatus("Настройки изменены. Нажмите «Проверить» заново.", false);
      return;
    }
    setBusy(true, "apply");
    setMessage(ui.message, "");
    setStatus("Применяем настройки…", false);
    try {
      const response = await requestJson(SETTINGS_URL, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = asObject(response);
      const settings = asSettings(body.settings);
      fillForm(settings);
      previewValid = false;
      previewPayload = null;
      const changed = Boolean(body.changed);
      if (changed) {
        const diff = typeof body.diff === "string" ? body.diff : "";
        showDiff(diff);
        setStatus("Настройки применены.", false);
        const backup = typeof body.backup === "string" ? body.backup : "";
        if (backup) {
          ui.backup.textContent = `Бэкап: ${backup}`;
          toggle(ui.backup, true);
        } else {
          toggle(ui.backup, false);
        }
        options.onLimits(settings);
      } else {
        resetDiff();
        setStatus("Изменений нет. Файл не изменён.", false);
        toggle(ui.backup, false);
      }
    } catch (error) {
      setStatus("Применить не удалось.", true);
      setMessage(ui.message, errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function preview(): Promise<void> {
    if (busy) {
      return;
    }
    let payload: Record<string, unknown>;
    try {
      payload = buildPayload();
    } catch (error) {
      setStatus(errorMessage(error), true);
      resetDiff();
      return;
    }
    setBusy(true, "preview");
    setMessage(ui.message, "");
    setStatus("Проверяем изменения…", false);
    const sent = JSON.stringify(payload);
    try {
      const response = await requestJson(PREVIEW_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: sent,
      });
      const body = asObject(response);
      const current = serializedForm();
      if (current === null || current !== sent) {
        resetDiff();
        setStatus("Настройки изменены. Нажмите «Проверить» заново.", false);
        return;
      }
      previewPayload = sent;
      const changed = Boolean(body.changed);
      const diff = typeof body.diff === "string" ? body.diff : "";
      if (changed && diff) {
        previewValid = true;
        showDiff(diff);
        setStatus("Есть изменения. Проверьте diff и нажмите «Применить».", false);
      } else {
        resetDiff();
        setStatus("Изменений нет.", false);
      }
    } catch (error) {
      resetDiff();
      setStatus("Проверка не удалась.", true);
      setMessage(ui.message, errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function fillForm(settings: Record<string, unknown>): void {
    const languages = Array.isArray(settings.languages) ? settings.languages : [];
    rows = [];
    const cards = languages.map((item) => {
      const source = asObject(item);
      const code = String(source.code ?? "");
      const row = el("div", "settings-language");
      const toggleLabel = el("label", "settings-language-toggle");
      const enabled = el("input");
      enabled.type = "checkbox";
      enabled.className = "settings-enabled";
      enabled.checked = Boolean(source.enabled);
      const target = el("input");
      target.type = "number";
      target.inputMode = "numeric";
      target.min = "0";
      target.max = "100000";
      target.step = "1";
      target.className = "settings-target";
      target.value = String(Number(source.stock_target) || 0);
      target.setAttribute("aria-label", `Целевой остаток: ${languageName(code)}`);
      rows.push({ code, enabled, target });
      toggleLabel.append(enabled, el("span", "settings-language-code", languageName(code)));
      const targetField = el("label", "field");
      targetField.append(el("span", "field-label", "Цель"), target);
      row.append(toggleLabel, targetField);
      return row;
    });
    ui.languages.replaceChildren(...cards);

    const run = asObject(settings.run);
    const search = asObject(settings.search);
    const filter = asObject(settings.filter);
    const download = asObject(settings.download);
    const delivery = asObject(settings.delivery);
    ui.batch.value = String(Number(run.batch) || 1);
    ui.interval.value = String(run.interval ?? "");
    ui.maxDuration.value = String(run.max_duration ?? "");
    ui.maxQueries.value = String(Number(search.max_queries) || 1);
    ui.filterMax.value = String(Number(filter.max) || 1);
    ui.workers.value = String(Number(download.workers) || 1);
    ui.deliveryMax.value = String(Number(delivery.max_count) || 1);
  }

  function buildPayload(): Record<string, unknown> {
    return {
      languages: rows.map((row) => ({
        code: row.code,
        enabled: row.enabled.checked,
        stock_target: readInt(row.target, `Целевой остаток (${languageName(row.code)})`, 0, 100000),
      })),
      run: {
        batch: readInt(ui.batch, "run.batch", 1, 1000),
        interval: readText(ui.interval, "run.interval"),
        max_duration: readText(ui.maxDuration, "run.max_duration"),
      },
      search: { max_queries: readInt(ui.maxQueries, "search.max_queries", 1, 500) },
      filter: { max: readInt(ui.filterMax, "filter.max", 1, 10000) },
      download: { workers: readInt(ui.workers, "download.workers", 1, 8) },
      delivery: { max_count: readInt(ui.deliveryMax, "delivery.max_count", 1, 300) },
    };
  }

  function serializedForm(): string | null {
    try {
      return JSON.stringify(buildPayload());
    } catch {
      return null;
    }
  }

  function resetDiff(): void {
    previewValid = false;
    previewPayload = null;
    hideDiff();
    ui.apply.disabled = true;
  }

  function setBusy(value: boolean, action: "preview" | "apply" | null = null): void {
    busy = value;
    ui.preview.disabled = value;
    ui.refresh.disabled = value;
    ui.apply.disabled = value || !previewValid;
    ui.preview.classList.toggle("is-busy", value && action === "preview");
    ui.apply.classList.toggle("is-busy", value && action === "apply");
    ui.form.setAttribute("aria-busy", String(value));
  }

  function showDiff(diff: string): void {
    ui.diffText.textContent = diff;
    toggle(ui.diff, true);
  }

  function hideDiff(): void {
    ui.diffText.textContent = "";
    toggle(ui.diff, false);
  }

  function setStatus(text: string, isError: boolean): void {
    ui.status.textContent = text;
    ui.status.classList.toggle("is-error", isError);
  }

  ui.preview.addEventListener("click", () => {
    void preview();
  });
  ui.form.addEventListener("submit", (event) => {
    void apply(event);
  });
  ui.form.addEventListener("input", () => {
    if (busy || !previewValid) {
      return;
    }
    previewValid = false;
    previewPayload = null;
    ui.apply.disabled = true;
    setStatus("Настройки изменены. Нажмите «Проверить» заново.", false);
  });
  ui.refresh.addEventListener("click", () => {
    void load(true);
  });

  return { load };
}

function readInt(input: HTMLInputElement, label: string, min: number, max: number): number {
  const value = Number(input.value);
  if (!Number.isInteger(value)) {
    throw new Error(`Поле «${label}» должно быть целым числом.`);
  }
  if (value < min || value > max) {
    throw new Error(`Поле «${label}» должно быть от ${min} до ${max}.`);
  }
  return value;
}

function readText(input: HTMLInputElement, label: string): string {
  const value = input.value.trim();
  if (!value) {
    throw new Error(`Поле «${label}» не может быть пустым.`);
  }
  return value;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "да" : "нет";
  }
  return String(value);
}

function asObject(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function asSettings(value: unknown): Record<string, unknown> {
  const body = asObject(value);
  if (!Array.isArray(body.languages)) {
    throw new ApiError(MESSAGES.badResponse, 0);
  }
  return body;
}
