/** Same-origin API access: one place for statuses and Russian messages. */

export const MESSAGES = {
  unauthorized: "Нет доступа к API. Откройте страницу с разрешённого адреса.",
  emptyTopic: "В выбранной теме нет видео.",
  archiveGone: "Архив уже удалён.",
  offline: "Сервер недоступен. Проверьте соединение и попробуйте ещё раз.",
  timeout: "Сервер не ответил вовремя. Попробуйте ещё раз.",
  disabled: "Выдача выключена на сервере.",
  badResponse: "Сервер вернул неожиданный ответ.",
};

export const READ_TIMEOUT_MS = 15000;
export const DELIVERY_TIMEOUT_MS = 300000;

export class ApiError extends Error {
  /**
   * @param {string} message
   * @param {number} status
   */
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * GET a JSON body.
 * @param {string} url
 * @param {number} [timeoutMs]
 * @returns {Promise<unknown>}
 */
export async function requestJson(url, timeoutMs = READ_TIMEOUT_MS) {
  const response = await request(url, {}, timeoutMs);
  try {
    return await response.json();
  } catch {
    throw new ApiError(MESSAGES.badResponse, response.status);
  }
}

/**
 * Read a response body as a blob, mapping a broken stream to the offline text.
 * @param {Response} response
 * @returns {Promise<Blob>}
 */
export async function readBlob(response) {
  try {
    return await response.blob();
  } catch {
    throw new ApiError(MESSAGES.offline, 0);
  }
}

/**
 * Fetch a same-origin URL and turn every failure into an ApiError.
 * @param {string} url
 * @param {RequestInit} [options]
 * @param {number} [timeoutMs]
 * @returns {Promise<Response>}
 */
export async function request(url, options = {}, timeoutMs = READ_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        Accept: "application/json, application/zip",
        ...options.headers,
      },
    });
    if (!response.ok) {
      throw statusError(response.status);
    }
    return response;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(MESSAGES.timeout, 0);
    }
    throw new ApiError(MESSAGES.offline, 0);
  } finally {
    window.clearTimeout(timer);
  }
}

/**
 * @param {number} status
 * @returns {ApiError}
 */
function statusError(status) {
  if (status === 401) {
    return new ApiError(MESSAGES.unauthorized, status);
  }
  if (status === 404) {
    return new ApiError(MESSAGES.archiveGone, status);
  }
  if (status === 503) {
    return new ApiError(MESSAGES.disabled, status);
  }
  return new ApiError(`Сервер вернул ошибку (код ${status}).`, status);
}
