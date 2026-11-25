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
} as const;

export const READ_TIMEOUT_MS = 15000;
export const DELIVERY_TIMEOUT_MS = 300000;

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Request a JSON body from a same-origin API route.
 */
export async function requestJson(
  url: string,
  options: RequestInit = {},
  timeoutMs = READ_TIMEOUT_MS,
): Promise<unknown> {
  const response = await request(url, options, timeoutMs);
  try {
    const data: unknown = await response.json();
    return data;
  } catch {
    throw new ApiError(MESSAGES.badResponse, response.status);
  }
}

/**
 * Fetch a same-origin URL and turn every failure into an ApiError.
 */
export async function request(
  url: string,
  options: RequestInit = {},
  timeoutMs = READ_TIMEOUT_MS,
): Promise<Response> {
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
 */
async function statusError(response: Response): Promise<ApiError> {
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
 */
async function errorDetail(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json();
    if (isRecord(data) && typeof data.detail === "string") {
      return data.detail;
    }
  } catch {
    return "";
  }
  return "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
