/** Russian formatting for counts, sizes, dates and the archive countdown. */

const COUNTS = new Intl.NumberFormat("ru-RU");
const DATES = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const BYTE_UNITS = ["Б", "КБ", "МБ", "ГБ"];
const LANGUAGE_NAMES = {
  de: "Немецкий",
  en: "Английский",
  es: "Испанский",
  fr: "Французский",
  it: "Итальянский",
  kk: "Казахский",
  pt: "Португальский",
  ru: "Русский",
  tr: "Турецкий",
  uk: "Украинский",
};

/**
 * @param {number} value
 * @returns {string}
 */
export function formatCount(value) {
  return COUNTS.format(value);
}

/**
 * @param {number} bytes
 * @returns {string}
 */
export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "—";
  }
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 ? 0 : 1;
  return `${value.toFixed(digits).replace(".", ",")} ${BYTE_UNITS[unit]}`;
}

/**
 * @param {string} iso
 * @returns {string}
 */
export function formatDateTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return DATES.format(date);
}

/**
 * Recompute the time left from the API value, never from a stored string.
 * @param {string} expiresAt
 * @param {number} [now]
 * @returns {string}
 */
export function formatRemaining(expiresAt, now = Date.now()) {
  const expires = Date.parse(expiresAt);
  if (Number.isNaN(expires)) {
    return "время неизвестно";
  }
  const seconds = Math.floor((expires - now) / 1000);
  if (seconds <= 0) {
    return "истёк";
  }
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  if (hours > 0) {
    return `${hours} ч ${pad(minutes)} мин`;
  }
  return `${minutes}:${pad(rest)}`;
}

/**
 * @param {string} code
 * @returns {string}
 */
export function languageName(code) {
  return LANGUAGE_NAMES[code] ?? String(code).toUpperCase();
}

/**
 * @param {number} value
 * @returns {string}
 */
function pad(value) {
  return String(value).padStart(2, "0");
}
