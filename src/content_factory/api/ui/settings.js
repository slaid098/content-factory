/** Runtime settings: read, preview the diff, then apply with a backup. */

import { ApiError, MESSAGES, requestJson } from "./api.js";
import { el, setMessage, toggle } from "./dom.js";
import { languageName } from "./format.js";

const SETTINGS_URL = "/api/v1/content/settings";
const PREVIEW_URL = "/api/v1/content/settings/preview";

/**
 * Build the settings form and its preview/apply flow.
 * @param {{
 *   form: HTMLFormElement,
 *   languages: HTMLElement,
 *   batch: HTMLInputElement,
 *   interval: HTMLInputElement,
 *   maxDuration: HTMLInputElement,
 *   maxQueries: HTMLInputElement,
 *   filterMax: HTMLInputElement,
 *   workers: HTMLInputElement,
 *   deliveryMax: HTMLInputElement,
 *   preview: HTMLButtonElement,
 *   apply: HTMLButtonElement,
 *   status: HTMLElement,
 *   diff: HTMLElement,
 *   diffText: HTMLElement,
 *   backup: HTMLElement,
 *   message: HTMLElement,
 *   refresh: HTMLButtonElement,
 * }} elements
 * @param {{onLimits: (settings: Record<string, unknown>) => void}} options
 */
export function createSettingsView(elements, options) {
  /** @type {Array<{code: string, enabled: HTMLInputElement, target: HTMLInputElement}>} */
  let rows = [];
  let loaded = false;
  let busy = false;
  let previewValid = false;

  /**
   * Load settings once, or again when ``force`` is set.
   * @param {boolean} [force]
   * @returns {Promise<Record<string, unknown> | null>}
   */
  async function load(force = false) {
    if (busy) {
      return null;
    }
    if (loaded && !force) {
      return null;
    }
    elements.refresh.disabled = true;
    setMessage(elements.message, "");
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
      setMessage(elements.message, error.message);
      setStatus("Настройки недоступны.", true);
      return null;
    } finally {
      elements.refresh.disabled = busy;
    }
  }

  /**
   * @param {SubmitEvent} event
   */
  async function apply(event) {
    event.preventDefault();
    if (!previewValid || busy) {
      if (!previewValid) {
        setStatus("Сначала нажмите «Проверить».", true);
      }
      return;
    }
    let payload;
    try {
      payload = buildPayload();
    } catch (error) {
      setStatus(error.message, true);
      return;
    }
    setBusy(true);
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
      setMessage(elements.message, error.message);
    } finally {
      setBusy(false);
    }
  }

  async function preview() {
    if (busy) {
      return;
    }
    let payload;
    try {
      payload = buildPayload();
    } catch (error) {
      setStatus(error.message, true);
      resetDiff();
      return;
    }
    setBusy(true);
    setMessage(elements.message, "");
    setStatus("Проверяем изменения…", false);
    try {
      const response = await requestJson(PREVIEW_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = asObject(response);
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
      setMessage(elements.message, error.message);
    } finally {
      setBusy(false);
    }
  }

  /**
   * @param {Record<string, unknown>} settings
   */
  function fillForm(settings) {
    const languages = Array.isArray(settings.languages) ? settings.languages : [];
    rows = [];
    const cards = languages.map((item) => {
      const source = asObject(item);
      const code = String(source.code ?? "");
      const row = el("label", "settings-language");
      const enabled = /** @type {HTMLInputElement} */ (el("input"));
      enabled.type = "checkbox";
      enabled.className = "settings-enabled";
      enabled.checked = Boolean(source.enabled);
      const target = /** @type {HTMLInputElement} */ (el("input"));
      target.type = "number";
      target.inputMode = "numeric";
      target.min = "0";
      target.max = "100000";
      target.step = "1";
      target.className = "settings-target";
      target.value = String(Number(source.stock_target) || 0);
      target.setAttribute("aria-label", `Целевой остаток: ${languageName(code)}`);
      rows.push({ code, enabled, target });
      row.append(
        enabled,
        el("span", "settings-language-code", languageName(code)),
        el("span", "hint", "цель"),
        target,
      );
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

  /**
   * @returns {Record<string, unknown>}
   */
  function buildPayload() {
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

  function resetDiff() {
    previewValid = false;
    hideDiff();
    elements.apply.disabled = true;
  }

  /**
   * @param {boolean} value
   */
  function setBusy(value) {
    busy = value;
    elements.preview.disabled = value;
    elements.refresh.disabled = value;
    elements.apply.disabled = value || !previewValid;
    elements.form.setAttribute("aria-busy", String(value));
  }

  /**
   * @param {string} diff
   */
  function showDiff(diff) {
    elements.diffText.textContent = diff;
    toggle(elements.diff, true);
  }

  function hideDiff() {
    elements.diffText.textContent = "";
    toggle(elements.diff, false);
  }

  /**
   * @param {string} text
   * @param {boolean} isError
   */
  function setStatus(text, isError) {
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
    elements.apply.disabled = true;
    setStatus("Настройки изменены. Нажмите «Проверить» заново.", false);
  });
  elements.refresh.addEventListener("click", () => {
    void load(true);
  });

  return { load };
}

/**
 * @param {HTMLInputElement} input
 * @param {string} label
 * @param {number} min
 * @param {number} max
 * @returns {number}
 */
function readInt(input, label, min, max) {
  const value = Number(input.value);
  if (!Number.isInteger(value)) {
    throw new Error(`Поле «${label}» должно быть целым числом.`);
  }
  if (value < min || value > max) {
    throw new Error(`Поле «${label}» должно быть от ${min} до ${max}.`);
  }
  return value;
}

/**
 * @param {HTMLInputElement} input
 * @param {string} label
 * @returns {string}
 */
function readText(input, label) {
  const value = input.value.trim();
  if (!value) {
    throw new Error(`Поле «${label}» не может быть пустым.`);
  }
  return value;
}

/**
 * @param {unknown} value
 * @returns {Record<string, unknown>}
 */
function asObject(value) {
  return value && typeof value === "object" ? /** @type {Record<string, unknown>} */ (value) : {};
}

/**
 * @param {unknown} value
 * @returns {Record<string, unknown>}
 */
function asSettings(value) {
  const body = asObject(value);
  if (!Array.isArray(body.languages)) {
    throw new ApiError(MESSAGES.badResponse, 0);
  }
  return body;
}
