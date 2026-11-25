/** Russian formatting for counts, sizes, languages and statuses. */

const BYTE_UNITS = ["Б", "КБ", "МБ", "ГБ"] as const;
const LANGUAGE_NAMES: Record<string, string> = {
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
const STATUS_NAMES: Record<string, string> = {
  building: "сборка",
  ready: "готово",
  ready_local: "локально",
  empty: "нет видео",
  failed: "ошибка",
  running: "идёт",
  generating: "генерация",
  postprocessing: "постобработка",
  done: "готово",
  dry_run: "пробный",
};

export function formatCount(value: number): string {
  return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
}

export function formatBytes(bytes: number): string {
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
  const suffix = BYTE_UNITS[unit] ?? BYTE_UNITS[0];
  return `${value.toFixed(digits).replace(".", ",")} ${suffix}`;
}

export function languageName(code: string): string {
  return LANGUAGE_NAMES[code] ?? String(code).toUpperCase();
}

export function statusLabel(status: string): string {
  return STATUS_NAMES[status] ?? status;
}
