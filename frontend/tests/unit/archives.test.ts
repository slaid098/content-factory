import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createArchivesView } from "../../src/archives";
import { jsonResponse, mockFetch, node, resetPage } from "./helpers";

const NOW = new Date("2026-11-26T10:00:00Z");

function createView() {
  return createArchivesView({
    list: node("archives"),
    empty: node("archives-empty"),
    message: node("archives-message"),
    summary: node("archives-summary"),
  });
}

function archive(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    request_id: "a1",
    language: "ru",
    storage: "local",
    size: 2048,
    files: 2,
    created_at: "2026-11-26T09:00:00Z",
    expires_at: "2026-11-26T11:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  resetPage();
  vi.useFakeTimers({ now: NOW });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("createArchivesView", () => {
  it("renders local and remote archives with a local-only summary", async () => {
    mockFetch(() =>
      jsonResponse([
        archive(),
        archive({
          request_id: "b2",
          language: "en",
          storage: "remote",
          size: 100,
          files: 1,
          url: "https://filebin.example/archive.zip",
          created_at: "2026-11-26T08:00:00Z",
          expires_at: "2026-11-26T12:00:00Z",
        }),
      ]),
    );
    const view = createView();

    await view.load();

    const list = node("archives");
    expect(list.hidden).toBe(false);
    expect(list.children.length).toBe(2);
    expect(node("archives-summary").textContent).toBe("1 · 2,0 КБ");
    expect(node("archives-summary").title).toBe(
      "локальные архивы; удалённые в Filebin диск не занимают",
    );

    const localLink = list.querySelector<HTMLAnchorElement>("a.button");
    expect(localLink?.getAttribute("href")).toBe("/api/v1/content/archives/ru/a1.zip");
    expect(localLink?.download).toBe("delivery-a1.zip");

    const remoteCard = list.children[1];
    const remoteLink = remoteCard?.querySelector<HTMLAnchorElement>("a.button");
    expect(remoteLink?.getAttribute("href")).toBe("https://filebin.example/archive.zip");
    expect(remoteLink?.rel).toBe("noopener");
    expect(remoteLink?.hasAttribute("download")).toBe(false);
    expect(remoteCard?.textContent).toContain("Litterbox");
  });

  it("marks the freshly built archive and shows its badge", async () => {
    mockFetch(() => jsonResponse([archive()]));
    const view = createView();

    await view.load("a1");

    const card = node("archives").querySelector(".archive-card");
    expect(card?.classList.contains("is-fresh")).toBe(true);
    expect(card?.textContent).toContain("только что");
  });

  it("shows the empty state and a zero summary without archives", async () => {
    mockFetch(() => jsonResponse([]));
    const view = createView();

    await view.load();

    expect(node("archives").hidden).toBe(true);
    expect(node("archives-empty").hidden).toBe(false);
    expect(node("archives-summary").textContent).toBe("0");
  });

  it("shows the API error in the message slot", async () => {
    mockFetch(() => jsonResponse({ detail: "нет доступа" }, 401));
    const view = createView();

    await view.load();

    const message = node("archives-message");
    expect(message.hidden).toBe(false);
    expect(message.textContent).toBe("Нет доступа к API. Откройте страницу с разрешённого адреса.");
  });

  it("expires a card in place and refreshes the list once a minute", async () => {
    const fetched = mockFetch(() =>
      jsonResponse([archive({ expires_at: "2026-11-26T09:00:00Z" })]),
    );
    const view = createView();

    await view.load();
    await vi.advanceTimersByTimeAsync(1000);

    const card = node("archives").querySelector(".archive-card");
    const link = card?.querySelector<HTMLAnchorElement>("a.button");
    expect(card?.classList.contains("is-expired")).toBe(true);
    expect(link?.hasAttribute("href")).toBe(false);
    expect(link?.getAttribute("aria-disabled")).toBe("true");
    expect(link?.textContent).toBe("Истёк");
    expect(card?.querySelector(".countdown")?.textContent).toBe("истёк");
    expect(fetched.mock.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("keeps an already expired archive from refreshing more than once a minute", async () => {
    const fetched = mockFetch(() =>
      jsonResponse([archive({ expires_at: "2026-11-26T09:00:00Z" })]),
    );
    const view = createView();

    await view.load();
    const callsAfterLoad = fetched.mock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(30_000);

    expect(fetched.mock.mock.calls.length).toBe(callsAfterLoad);
  });
});
