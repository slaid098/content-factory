/** Archive table: source, live countdowns and direct download links. */

import { el, setMessage, toggle } from "./dom";
import { formatDateTime, formatRemaining } from "./lib/datetime";
import { errorMessage } from "./lib/errors";
import { formatBytes, formatCount, languageName } from "./lib/format";
import { requestJson } from "./lib/http";

const TICK_MS = 1000;
const EXPIRY_REFRESH_MS = 60000;

export interface ArchivesElements {
  list: HTMLElement;
  empty: HTMLElement;
  message: HTMLElement;
  summary: HTMLElement;
  loading?: HTMLElement;
}

export interface ArchivesView {
  load: (highlightId?: string | null) => Promise<void>;
}

/**
 * Build the archive table once and keep its countdowns running.
 */
export function createArchivesView(elements: ArchivesElements): ArchivesView {
  let archives: Record<string, unknown>[] = [];
  let freshId: string | null = null;
  let lastExpiryRefresh = 0;

  /**
   * Reload the list; an optional request id marks the archive built now.
   */
  async function load(highlightId: string | null = null): Promise<void> {
    elements.list.setAttribute("aria-busy", "true");
    if (elements.loading) {
      toggle(elements.loading, true);
    }
    try {
      const data = await requestJson("/api/v1/content/archives");
      archives = Array.isArray(data) ? (data as Record<string, unknown>[]) : [];
      freshId = highlightId;
      setMessage(elements.message, "");
      render();
    } catch (error) {
      setMessage(elements.message, errorMessage(error));
    } finally {
      elements.list.setAttribute("aria-busy", "false");
      if (elements.loading) {
        toggle(elements.loading, false);
      }
    }
  }

  function render(): void {
    const rows = archives.map((item) => buildRow(item, item.request_id === freshId));
    elements.list.replaceChildren(...rows);
    toggle(elements.list, rows.length !== 0);
    toggle(elements.empty, rows.length === 0);
    renderSummary();
    tick();
  }

  function renderSummary(): void {
    const local = archives.filter((item) => item.storage === "local");
    const bytes = local.reduce((sum, item) => sum + (Number(item.size) || 0), 0);
    elements.summary.textContent =
      local.length === 0 ? "0" : `${formatCount(local.length)} · ${formatBytes(bytes)}`;
    elements.summary.title = "локальные архивы; удалённые в Filebin диск не занимают";
  }

  function buildRow(item: Record<string, unknown>, fresh: boolean): HTMLTableRowElement {
    const row = el("tr", fresh ? "archive-row is-fresh" : "archive-row");

    const source = el("td", "archive-source");
    const profile = typeof item.profile === "string" && item.profile ? item.profile : null;
    source.append(
      el("span", "archive-source-name", profile ?? "Общетематичные"),
      el("span", "badge", languageName(String(item.language))),
      el("span", "badge", item.storage === "remote" ? "Litterbox" : "сервер"),
    );
    if (fresh) {
      source.append(el("span", "badge accent", "только что"));
    }

    const files = el(
      "td",
      "archive-cell",
      typeof item.files === "number" ? formatCount(item.files) : "—",
    );
    const size = el("td", "archive-cell", formatBytes(Number(item.size)));
    const created = el("td", "archive-cell", formatDateTime(String(item.created_at)));

    const expires = el("td", "archive-cell");
    const countdown = el("span", "countdown");
    countdown.dataset.expires = String(item.expires_at);
    countdown.textContent = formatRemaining(String(item.expires_at));
    expires.append(countdown);

    const download = el("td", "archive-download");
    const link = el("a", "button secondary", "Скачать");
    link.href = archiveUrl(item);
    if (item.storage === "remote") {
      link.rel = "noopener";
    } else {
      link.download = `delivery-${String(item.request_id)}.zip`;
    }
    download.append(link);

    row.append(source, files, size, created, expires, download);
    return row;
  }

  function tick(): void {
    const now = Date.now();
    let expired = false;
    for (const node of elements.list.querySelectorAll<HTMLElement>("[data-expires]")) {
      const remaining = formatRemaining(node.dataset.expires ?? "", now);
      node.textContent = remaining;
      if (remaining === "истёк") {
        expired = true;
        const row = node.closest(".archive-row");
        row?.classList.add("is-expired");
        const link = row?.querySelector<HTMLAnchorElement>("a.button");
        if (link) {
          link.removeAttribute("href");
          link.setAttribute("aria-disabled", "true");
          link.textContent = "Истёк";
        }
      }
    }
    if (expired && now - lastExpiryRefresh > EXPIRY_REFRESH_MS) {
      lastExpiryRefresh = now;
      void load();
    }
  }

  function archiveUrl(item: Record<string, unknown>): string {
    if (item.storage === "remote" && typeof item.url === "string" && item.url) {
      return item.url;
    }
    const language = encodeURIComponent(String(item.language));
    const requestId = encodeURIComponent(String(item.request_id));
    return `/api/v1/content/archives/${language}/${requestId}.zip`;
  }

  window.setInterval(tick, TICK_MS);

  return { load };
}
