/** Same-origin API access: one place for statuses and Russian messages. */

export const MESSAGES = {
  unauthorized: "Нет доступа к API. Откройте страницу с разрешённого адреса.",
  empty: "В стоке этого языка нет видео.",
  busy: "Сборка уже идёт. Дождитесь её завершения и попробуйте снова.",
  archiveGone: "Архив уже удалён.",
  jobGone: "Задача не найдена или её срок истёк.",
  offline: "Сервер недоступен. Проверьте соединение и попробуйте ещё раз.",
  timeout: "Сервер не ответил вовремя. Попробуйте ещё раз.",
  disabled: "Выдача выключена на сервере.",
  invalid: "Сервер отклонил данные. Проверьте поля формы.",
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
 * Request a JSON body from a same-origin API route.
 * @param {string} url
 * @param {RequestInit} [options]
 * @param {number} [timeoutMs]
 * @returns {Promise<unknown>}
 */
export async function requestJson(url, options = {}, timeoutMs = READ_TIMEOUT_MS) {
  const response = await request(url, options, timeoutMs);
  try {
    return await response.json();
  } catch {
    throw new ApiError(MESSAGES.badResponse, response.status);
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
        Accept: "application/json",
        ...options.headers,
      },
    });
    if (!response.ok) {
      throw await statusError(response);
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
 * Map an unsuccessful response to a message the page can show as is.
 * @param {Response} response
 * @returns {Promise<ApiError>}
 */
async function statusError(response) {
  if (response.status === 401) {
    return new ApiError(MESSAGES.unauthorized, 401);
  }
  if (response.status === 503) {
    return new ApiError(MESSAGES.disabled, 503);
  }
  if (response.status === 404) {
    return new ApiError(MESSAGES.archiveGone, 404);
  }
  if (response.status === 409) {
    return new ApiError(MESSAGES.busy, 409);
  }
  const detail = await errorDetail(response);
  if (response.status === 422) {
    return new ApiError(detail || MESSAGES.invalid, 422);
  }
  return new ApiError(detail || `Сервер вернул ошибку (код ${response.status}).`, response.status);
}

/**
 * Read a human-readable ``detail`` from an error JSON body.
 * @param {Response} response
 * @returns {Promise<string>}
 */
async function errorDetail(response) {
  try {
    const data = await response.json();
    if (data && typeof data.detail === "string") {
      return data.detail;
    }
  } catch {
    return "";
  }
  return "";
}
