/** The only place for Intl date formatting and countdowns. */

const DATES = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return DATES.format(date);
}

/**
 * Recompute the time left from the API value, never from a stored string.
 */
export function formatRemaining(expiresAt: string, now = Date.now()): string {
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

function pad(value: number): string {
  return String(value).padStart(2, "0");
}
