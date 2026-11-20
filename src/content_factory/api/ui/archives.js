/** Archive list: cards, live countdown and re-download without rebuilding. */

import { ApiError, readBlob, request, requestJson } from "./api.js";
import { el, saveBlob } from "./dom.js";
import {
  formatBytes,
  formatDateTime,
  formatRemaining,
  languageName,
} from "./format.js";

const TICK_MS = 1000;
const DOWNLOAD_TIMEOUT_MS = 120000;
const EXPIRY_REFRESH_MS = 60000;

/**
 * Build the archive list once and keep its countdowns running.
 * @param {{list: HTMLElement, empty: HTMLElement, message: HTMLElement, lastArchive: HTMLElement}} elements
 */
export function createArchivesView(elements) {
  /** @type {Array<Record<string, unknown>>} */
  let archives = [];
  /** @type {string | null} */
  let freshId = null;
  let lastExpiryRefresh = 0;

  /**
   * Reload the list; an optional request id marks the archive built now.
   * @param {string | null} [highlightId]
   */
  async function load(highlightId = null) {
    try {
      const data = await requestJson("/api/v1/content/archives");
      archives = Array.isArray(data) ? data : [];
      freshId = highlightId;
      setMessage("");
      render();
    } catch (error) {
      setMessage(error.message);
    }
  }

  function render() {
    const rows = archives.map((item) => {
      const row = el("li");
      row.append(buildCard(item, item.request_id === freshId));
      return row;
    });
    elements.list.replaceChildren(...rows);
    elements.list.hidden = rows.length === 0;
    elements.empty.hidden = rows.length !== 0;

    const fresh =
      freshId === null
        ? undefined
        : archives.find((item) => item.request_id === freshId);
    elements.lastArchive.replaceChildren();
    elements.lastArchive.hidden = !fresh;
    if (fresh) {
      elements.lastArchive.append(
        el("h3", "", "Архив готов"),
        buildCard(fresh, true),
      );
    }
    tick();
  }

  /**
   * @param {Record<string, unknown>} item
   * @param {boolean} fresh
   * @returns {HTMLElement}
   */
  function buildCard(item, fresh) {
    const requestId = String(item.request_id);
    const card = el("article", fresh ? "archive-card is-fresh" : "archive-card");
    const head = el("div", "archive-head");
    head.append(el("span", "archive-name", `delivery-${requestId}.zip`));

    const tags = el("span", "archive-tags");
    tags.append(el("span", "badge", languageName(String(item.language))));
    if (fresh) {
      tags.append(el("span", "badge accent", "только что"));
    }
    head.append(tags);

    const meta = el("p", "archive-meta");
    meta.append(
      el("span", "", formatBytes(Number(item.size))),
      el("span", "", "·"),
      el("span", "", `создан ${formatDateTime(String(item.created_at))}`),
    );

    const foot = el("div", "archive-foot");
    const timer = el("span", "timer");
    const countdown = el("span", "countdown");
    countdown.dataset.expires = String(item.expires_at);
    countdown.textContent = formatRemaining(String(item.expires_at));
    timer.append(el("span", "muted", "удаление через "), countdown);

    const link = el("a", "button secondary", "Скачать");
    link.href = archiveUrl(item);
    link.download = `delivery-${requestId}.zip`;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      void download(item, link);
    });

    foot.append(timer, link);
    card.append(head, meta, foot);
    return card;
  }

  /**
   * Download one archive through the same GET URL; it never rebuilds stock.
   * @param {Record<string, unknown>} item
   * @param {HTMLElement} trigger
   */
  async function download(item, trigger) {
    if (trigger.classList.contains("is-busy")) {
      return;
    }
    trigger.classList.add("is-busy");
    trigger.setAttribute("aria-busy", "true");
    try {
      const response = await request(
        archiveUrl(item),
        {},
        DOWNLOAD_TIMEOUT_MS,
      );
      saveBlob(
        await readBlob(response),
        `delivery-${String(item.request_id)}.zip`,
      );
      setMessage("");
    } catch (error) {
      setMessage(error.message);
      if (error instanceof ApiError && error.status === 404) {
        await load();
        setMessage(error.message);
      }
    } finally {
      trigger.classList.remove("is-busy");
      trigger.removeAttribute("aria-busy");
    }
  }

  function tick() {
    const now = Date.now();
    let expired = false;
    for (const node of document.querySelectorAll("[data-expires]")) {
      const remaining = formatRemaining(node.dataset.expires, now);
      node.textContent = remaining;
      if (remaining === "истёк") {
        expired = true;
        node.closest(".archive-card")?.classList.add("is-expired");
      }
    }
    if (expired && now - lastExpiryRefresh > EXPIRY_REFRESH_MS) {
      lastExpiryRefresh = now;
      void load();
    }
  }

  /**
   * @param {string} text
   */
  function setMessage(text) {
    elements.message.textContent = text;
    elements.message.hidden = !text;
  }

  /**
   * @param {Record<string, unknown>} item
   * @returns {string}
   */
  function archiveUrl(item) {
    const language = encodeURIComponent(String(item.language));
    const requestId = encodeURIComponent(String(item.request_id));
    return `/api/v1/content/archives/${language}/${requestId}.zip`;
  }

  window.setInterval(tick, TICK_MS);

  return { load };
}
