/** Runtime settings: read, preview the diff, then apply with a backup. */

import { el, setMessage, toggle } from "./dom";
import { errorMessage } from "./lib/errors";
import { languageName } from "./lib/format";
import { ApiError, MESSAGES, requestJson } from "./lib/http";

const SETTINGS_URL = "/api/v1/content/settings";
const PREVIEW_URL = "/api/v1/content/settings/preview";

export interface SettingsElements {
  form: HTMLFormElement;
  languages: HTMLElement;
  batch: HTMLInputElement;
  interval: HTMLInputElement;
  maxDuration: HTMLInputElement;
  maxQueries: HTMLInputElement;
  filterMax: HTMLInputElement;
  workers: HTMLInputElement;
  deliveryMax: HTMLInputElement;
  preview: HTMLButtonElement;
  apply: HTMLButtonElement;
  status: HTMLElement;
  diff: HTMLElement;
  diffText: HTMLElement;
  backup: HTMLElement;
  message: HTMLElement;
  refresh: HTMLButtonElement;
}

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

/**
 * Build the settings form and its preview/apply flow.
 */
export function createSettingsView(
  elements: SettingsElements,
  options: SettingsViewOptions,
): SettingsView {
  let rows: LanguageRow[] = [];
  let loaded = false;
  let busy = false;
  let previewValid = false;
  let previewPayload: string | null = null;

  /**
   * Load settings once, or again when ``force`` is set.
   */
  async function load(force = false): Promise<Record<string, unknown> | null> {
    if (busy) {
      return null;
    }
    if (loaded && !force) {
      return null;
    }
    elements.refresh.disabled = true;
    elements.refresh.classList.add("is-busy");
    elements.form.setAttribute("aria-busy", "true");
    setMessage(elements.message, "");
    setStatus("Загружаем настройки…", false);
    try {
      const response = await requestJson(SETTINGS_URL);
      const settings = asSettings(response);
      fillForm(settings);
      resetDiff();
      setStatus("", false);
      loaded = true;
      options.onLimits(settings);
      return settings;
    } catch (error) {
      loaded = false;
      setMessage(elements.message, errorMessage(error));
      setStatus("Настройки недоступны.", true);
      return null;
    } finally {
      elements.refresh.disabled = busy;
      elements.refresh.classList.remove("is-busy");
      elements.form.setAttribute("aria-busy", String(busy));
    }
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
    setMessage(elements.message, "");
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
          elements.backup.textContent = `Бэкап: ${backup}`;
          toggle(elements.backup, true);
        } else {
          toggle(elements.backup, false);
        }
        options.onLimits(settings);
      } else {
        setStatus("Изменений нет. Файл не изменён.", false);
        toggle(elements.backup, false);
      }
    } catch (error) {
      setStatus("Применить не удалось.", true);
      setMessage(elements.message, errorMessage(error));
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
    setMessage(elements.message, "");
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
      setMessage(elements.message, errorMessage(error));
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
      const toggle = el("label", "settings-language-toggle");
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
      toggle.append(enabled, el("span", "settings-language-code", languageName(code)));
      const targetField = el("label", "field");
      targetField.append(el("span", "field-label", "Цель"), target);
      row.append(toggle, targetField);
      return row;
    });
    elements.languages.replaceChildren(...cards);

    const run = asObject(settings.run);
    const search = asObject(settings.search);
    const filter = asObject(settings.filter);
    const download = asObject(settings.download);
    const delivery = asObject(settings.delivery);
    elements.batch.value = String(Number(run.batch) || 1);
    elements.interval.value = String(run.interval ?? "");
    elements.maxDuration.value = String(run.max_duration ?? "");
    elements.maxQueries.value = String(Number(search.max_queries) || 1);
    elements.filterMax.value = String(Number(filter.max) || 1);
    elements.workers.value = String(Number(download.workers) || 1);
    elements.deliveryMax.value = String(Number(delivery.max_count) || 1);
  }

  function buildPayload(): Record<string, unknown> {
    return {
      languages: rows.map((row) => ({
        code: row.code,
        enabled: row.enabled.checked,
        stock_target: readInt(row.target, `Целевой остаток (${languageName(row.code)})`, 0, 100000),
      })),
      run: {
        batch: readInt(elements.batch, "run.batch", 1, 1000),
        interval: readText(elements.interval, "run.interval"),
        max_duration: readText(elements.maxDuration, "run.max_duration"),
      },
      search: { max_queries: readInt(elements.maxQueries, "search.max_queries", 1, 500) },
      filter: { max: readInt(elements.filterMax, "filter.max", 1, 10000) },
      download: { workers: readInt(elements.workers, "download.workers", 1, 8) },
      delivery: { max_count: readInt(elements.deliveryMax, "delivery.max_count", 1, 300) },
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
    elements.apply.disabled = true;
  }

  function setBusy(value: boolean, action: "preview" | "apply" | null = null): void {
    busy = value;
    elements.preview.disabled = value;
    elements.refresh.disabled = value;
    elements.apply.disabled = value || !previewValid;
    elements.preview.classList.toggle("is-busy", value && action === "preview");
    elements.apply.classList.toggle("is-busy", value && action === "apply");
    elements.form.setAttribute("aria-busy", String(value));
  }

  function showDiff(diff: string): void {
    elements.diffText.textContent = diff;
    toggle(elements.diff, true);
  }

  function hideDiff(): void {
    elements.diffText.textContent = "";
    toggle(elements.diff, false);
  }

  function setStatus(text: string, isError: boolean): void {
    elements.status.textContent = text;
    elements.status.classList.toggle("is-error", isError);
  }

  elements.preview.addEventListener("click", () => {
    void preview();
  });
  elements.form.addEventListener("submit", (event) => {
    void apply(event);
  });
  elements.form.addEventListener("input", () => {
    if (busy || !previewValid) {
      return;
    }
    previewValid = false;
    previewPayload = null;
    elements.apply.disabled = true;
    setStatus("Настройки изменены. Нажмите «Проверить» заново.", false);
  });
  elements.refresh.addEventListener("click", () => {
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
