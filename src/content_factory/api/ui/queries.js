/** Search queries of one language: counters, filtering and append. */

import { requestJson } from "./api.js";
import { el, setMessage, toggle } from "./dom.js";
import { formatCount } from "./format.js";

const QUERIES_URL = "/api/v1/content/queries";

/**
 * Build the queries panel.
 * @param {{
 *   language: HTMLSelectElement,
 *   search: HTMLInputElement,
 *   summary: HTMLElement,
 *   message: HTMLElement,
 *   list: HTMLElement,
 *   empty: HTMLElement,
 *   form: HTMLFormElement,
 *   input: HTMLInputElement,
 *   add: HTMLButtonElement,
 *   status: HTMLElement,
 *   refresh: HTMLButtonElement,
 * }} elements
 */
export function createQueriesView(elements) {
  /** @type {{queries: Array<{query: string, count: number, in_file: boolean}>, unclassified: number, total: number} | null} */
  let data = null;
  let currentLanguage = "";
  let adding = false;
  let requestSeq = 0;

  /**
   * Load the query list of one language.
   * @param {string} language
   */
  async function load(language) {
    if (!language) {
      return;
    }
    currentLanguage = language;
    const seq = ++requestSeq;
    setMessage(elements.message, "");
    elements.summary.textContent = "Загружаем запросы…";
    try {
      const response = await requestJson(
        `${QUERIES_URL}?language=${encodeURIComponent(language)}`,
      );
      if (seq !== requestSeq) {
        return;
      }
      data = normalize(response);
      render();
    } catch (error) {
      if (seq !== requestSeq) {
        return;
      }
      data = null;
      setMessage(elements.message, error.message);
      elements.summary.textContent = "";
      elements.list.replaceChildren();
      toggle(elements.list, false);
      toggle(elements.empty, true);
      elements.empty.textContent = "Список запросов недоступен.";
    }
  }

  function render() {
    if (data === null) {
      return;
    }
    const needle = elements.search.value.trim().toLowerCase();
    const visible = needle
      ? data.queries.filter((item) => item.query.toLowerCase().includes(needle))
      : data.queries;

    const rows = visible.map((item) => {
      const row = el("li", item.count === 0 ? "query-item is-zero" : "query-item");
      row.append(el("span", "query-name", item.query));
      const tags = el("span", "query-tags");
      tags.append(el("span", "badge", `${formatCount(item.count)} видео`));
      if (!item.in_file) {
        tags.append(el("span", "badge warn", "нет в файле"));
      }
      row.append(tags);
      return row;
    });

    elements.list.replaceChildren(...rows);
    toggle(elements.list, rows.length !== 0);
    toggle(elements.empty, rows.length === 0);
    elements.empty.textContent =
      data.queries.length === 0 ? "Запросов пока нет." : "Ничего не найдено.";

    const inFile = data.queries.filter((item) => item.in_file).length;
    const extra = data.queries.length - inFile;
    elements.summary.textContent =
      `Запросов: ${formatCount(data.queries.length)}, в файле: ${formatCount(inFile)}, ` +
      `только в базе: ${formatCount(extra)}. Видео по темам: ${formatCount(data.total)}, ` +
      `без темы: ${formatCount(data.unclassified)}.`;
  }

  /**
   * @param {SubmitEvent} event
   */
  async function add(event) {
    event.preventDefault();
    const query = elements.input.value.trim();
    if (!query) {
      setStatus("Введите поисковый запрос.", true);
      return;
    }
    const language = elements.language.value || currentLanguage;
    if (!language || adding) {
      return;
    }
    adding = true;
    elements.add.disabled = true;
    elements.form.setAttribute("aria-busy", "true");
    setStatus("Добавляем запрос…", false);
    try {
      const response = await requestJson(QUERIES_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language, query }),
      });
      const created = Boolean(response && response.created);
      setStatus(
        created ? "Запрос добавлен в файл." : "Такой запрос уже есть в файле.",
        false,
      );
      elements.input.value = "";
      await load(language);
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      adding = false;
      elements.add.disabled = false;
      elements.form.removeAttribute("aria-busy");
    }
  }

  /**
   * @param {string} text
   * @param {boolean} isError
   */
  function setStatus(text, isError) {
    elements.status.textContent = text;
    elements.status.classList.toggle("is-error", isError);
  }

  elements.search.addEventListener("input", render);
  elements.refresh.addEventListener("click", () => {
    void load(elements.language.value || currentLanguage);
  });
  elements.form.addEventListener("submit", (event) => {
    void add(event);
  });

  return { load };
}

/**
 * @param {unknown} response
 * @returns {{queries: Array<{query: string, count: number, in_file: boolean}>, unclassified: number, total: number}}
 */
function normalize(response) {
  const body = /** @type {Record<string, unknown>} */ (
    response && typeof response === "object" ? response : {}
  );
  const rows = Array.isArray(body.queries) ? body.queries : [];
  const queries = rows
    .filter((row) => row && typeof row === "object")
    .map((row) => {
      const item = /** @type {Record<string, unknown>} */ (row);
      return {
        query: String(item.query ?? ""),
        count: Number(item.count) || 0,
        in_file: Boolean(item.in_file),
      };
    })
    .filter((item) => item.query !== "");
  return {
    queries,
    unclassified: Number(body.unclassified) || 0,
    total: Number(body.total) || 0,
  };
}
