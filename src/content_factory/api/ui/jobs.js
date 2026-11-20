/** Asynchronous archive build: POST the request, poll its state, show the link. */

import { ApiError, DELIVERY_TIMEOUT_MS, MESSAGES, requestJson } from "./api.js";
import { setMessage, toggle } from "./dom.js";
import { formatRemaining, statusLabel } from "./format.js";

const LINK_URL = "/api/v1/content/deliveries/link";
const POLL_MS = 3000;
const POLL_LIMIT_MS = 30 * 60 * 1000;
const TERMINAL = new Set(["ready", "ready_local", "empty", "failed"]);

/**
 * Build the delivery job card once and keep its state and countdown fresh.
 * @param {{
 *   container: HTMLElement,
 *   title: HTMLElement,
 *   state: HTMLElement,
 *   message: HTMLElement,
 *   timer: HTMLElement,
 *   link: HTMLAnchorElement,
 *   check: HTMLButtonElement,
 * }} elements
 * @param {{onFinished: (job: Record<string, unknown>) => void}} options
 */
export function createJobsView(elements, options) {
  /** @type {Record<string, unknown> | null} */
  let job = null;
  let startedAt = 0;
  let pollTimer = 0;
  let polling = false;
  let notified = false;

  /**
   * Start one background build and poll it until a terminal status.
   * @param {string} language
   * @param {number} count
   * @param {string} requestId
   * @returns {Promise<Record<string, unknown>>}
   */
  async function start(language, count, requestId) {
    const data = await requestJson(
      LINK_URL,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language, count, request_id: requestId }),
      },
      DELIVERY_TIMEOUT_MS,
    );
    job = asJob(data);
    startedAt = Date.now();
    notified = false;
    show(job);
    if (TERMINAL.has(String(job.status))) {
      complete(job);
    } else {
      beginPolling();
    }
    return job;
  }

  function beginPolling() {
    stopPolling();
    polling = true;
    elements.check.hidden = true;
    schedule();
  }

  function schedule() {
    stopPolling();
    polling = true;
    pollTimer = window.setTimeout(() => {
      void poll();
    }, POLL_MS);
  }

  function stopPolling() {
    polling = false;
    if (pollTimer !== 0) {
      window.clearTimeout(pollTimer);
      pollTimer = 0;
    }
  }

  async function poll() {
    if (!polling || job === null) {
      return;
    }
    if (Date.now() - startedAt > POLL_LIMIT_MS) {
      stopPolling();
      elements.state.textContent = "ошибка";
      elements.message.classList.add("is-error");
      setMessage(
        elements.message,
        "Сборка идёт дольше 30 минут. Опрос остановлен — нажмите «Проверить».",
      );
      elements.check.hidden = false;
      return;
    }
    try {
      const data = await requestJson(jobUrl(job));
      job = asJob(data);
      show(job);
      if (TERMINAL.has(String(job.status))) {
        complete(job);
      } else {
        schedule();
      }
    } catch (error) {
      stopPolling();
      if (error instanceof ApiError && error.status === 404) {
        elements.state.textContent = "ошибка";
        elements.message.classList.add("is-error");
        setMessage(elements.message, MESSAGES.jobGone);
        toggle(elements.link, false);
        return;
      }
      elements.message.classList.add("is-error");
      setMessage(elements.message, error.message);
      elements.check.hidden = false;
    }
  }

  async function checkNow() {
    if (job === null) {
      return;
    }
    elements.check.disabled = true;
    elements.message.classList.remove("is-error");
    setMessage(elements.message, "Проверяем статус…");
    try {
      const data = await requestJson(jobUrl(job));
      job = asJob(data);
      show(job);
      if (TERMINAL.has(String(job.status))) {
        complete(job);
      } else {
        setMessage(elements.message, "Сборка всё ещё идёт. Нажмите «Проверить» позже.");
        elements.check.hidden = false;
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        elements.state.textContent = "ошибка";
        elements.message.classList.add("is-error");
        setMessage(elements.message, MESSAGES.jobGone);
        toggle(elements.link, false);
      } else {
        elements.message.classList.add("is-error");
        setMessage(elements.message, error.message);
        elements.check.hidden = false;
      }
    } finally {
      elements.check.disabled = false;
    }
  }

  /**
   * @param {Record<string, unknown>} current
   */
  function complete(current) {
    stopPolling();
    if (!notified) {
      notified = true;
      options.onFinished(current);
    }
  }

  /**
   * @param {Record<string, unknown>} current
   */
  function show(current) {
    const status = String(current.status);
    elements.container.hidden = false;
    elements.container.classList.remove("is-expired");
    elements.container.classList.toggle("is-error", status === "failed");
    elements.container.classList.toggle("is-ready", status === "ready" || status === "ready_local");
    elements.title.textContent = `delivery-${String(current.request_id)}.zip`;
    elements.state.textContent = statusLabel(status);
    elements.state.className = `badge ${badgeClass(status)}`;
    elements.message.classList.remove("is-error");
    elements.link.removeAttribute("href");
    elements.link.removeAttribute("download");
    toggle(elements.link, false);
    toggle(elements.timer, false);
    if (TERMINAL.has(status)) {
      elements.check.hidden = true;
    }

    if (status === "building") {
      setMessage(elements.message, "Собираем архив. Это может занять несколько минут.");
      return;
    }
    if (status === "empty") {
      setMessage(elements.message, MESSAGES.empty);
      return;
    }
    if (status === "failed") {
      const detail = current.error ? `: ${String(current.error)}` : "";
      setMessage(elements.message, `Сборка не удалась${detail}`);
      return;
    }
    if (status === "ready" && typeof current.url === "string" && current.url) {
      elements.link.href = current.url;
      elements.link.textContent = "Скачать архив";
      setMessage(elements.message, "Архив залит на Litterbox. Ссылка живёт 24 часа.");
      toggle(elements.link, true);
      toggle(elements.timer, true);
      updateTimer();
      return;
    }
    elements.link.href = localArchiveUrl(current);
    elements.link.download = `delivery-${String(current.request_id)}.zip`;
    elements.link.textContent = "Скачать локальный архив";
    const detail = current.error ? ` ${String(current.error)}` : "";
    setMessage(
      elements.message,
      `Внешняя ссылка недоступна. Архив лежит на сервере.${detail}`,
    );
    toggle(elements.link, true);
    toggle(elements.timer, true);
    updateTimer();
  }

  function updateTimer() {
    if (job === null || !TERMINAL.has(String(job.status))) {
      toggle(elements.timer, false);
      return;
    }
    const status = String(job.status);
    if (status === "empty" || status === "failed") {
      toggle(elements.timer, false);
      return;
    }
    const remaining = formatRemaining(String(job.expires_at));
    elements.timer.textContent = `ссылка живёт ещё ${remaining}`;
    toggle(elements.timer, true);
    const expired = remaining === "истёк";
    elements.container.classList.toggle("is-expired", expired);
    if (expired) {
      elements.timer.textContent = "срок ссылки истёк";
      toggle(elements.link, false);
      elements.link.removeAttribute("href");
    }
  }

  window.setInterval(updateTimer, 1000);
  elements.check.addEventListener("click", () => {
    void checkNow();
  });

  return { start };
}

/**
 * @param {string} status
 * @returns {string}
 */
function badgeClass(status) {
  if (status === "failed" || status === "empty") {
    return "warn";
  }
  if (status === "ready" || status === "ready_local") {
    return "accent";
  }
  return "";
}

/**
 * @param {Record<string, unknown>} current
 * @returns {string}
 */
function jobUrl(current) {
  const language = encodeURIComponent(String(current.language));
  const requestId = encodeURIComponent(String(current.request_id));
  return `/api/v1/content/deliveries/${language}/${requestId}`;
}

/**
 * @param {Record<string, unknown>} current
 * @returns {string}
 */
function localArchiveUrl(current) {
  const language = encodeURIComponent(String(current.language));
  const requestId = encodeURIComponent(String(current.request_id));
  return `/api/v1/content/archives/${language}/${requestId}.zip`;
}

/**
 * @param {unknown} data
 * @returns {Record<string, unknown>}
 */
function asJob(data) {
  if (!data || typeof data !== "object") {
    throw new ApiError(MESSAGES.badResponse, 0);
  }
  return /** @type {Record<string, unknown>} */ (data);
}
