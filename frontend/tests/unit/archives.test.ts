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
    loading: node("archives-loading"),
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
    profile: null,
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
  it("renders general and profile archives with a local-only summary", async () => {
    mockFetch(() =>
      jsonResponse([
        archive(),
        archive({
          request_id: "b2",
          language: "en",
          profile: "dating/profile_1",
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

    const generalRow = list.children[0];
    expect(generalRow?.textContent).toContain("Общетематичные");
    expect(generalRow?.textContent).toContain("2");
    const localLink = generalRow?.querySelector<HTMLAnchorElement>("a.button");
    expect(localLink?.getAttribute("href")).toBe("/api/v1/content/archives/ru/a1.zip");
    expect(localLink?.download).toBe("delivery-a1.zip");

    const profileRow = list.children[1];
    expect(profileRow?.textContent).toContain("dating/profile_1");
    expect(profileRow?.textContent).toContain("Английский");
    const remoteLink = profileRow?.querySelector<HTMLAnchorElement>("a.button");
    expect(remoteLink?.getAttribute("href")).toBe("https://filebin.example/archive.zip");
    expect(remoteLink?.rel).toBe("noopener");
    expect(remoteLink?.hasAttribute("download")).toBe(false);
    expect(profileRow?.textContent).toContain("Litterbox");
  });

  it("marks the freshly built archive", async () => {
    mockFetch(() => jsonResponse([archive()]));
    const view = createView();

    await view.load("a1");

    const row = node("archives").querySelector(".archive-row");
    expect(row?.classList.contains("is-fresh")).toBe(true);
    expect(row?.textContent).toContain("только что");
  });

  it("shows the empty state and a zero summary without archives", async () => {
    mockFetch(() => jsonResponse([]));
    const view = createView();

    await view.load();

    expect(node("archives").hidden).toBe(true);
    expect(node("archives-empty").hidden).toBe(false);
    expect(node("archives-summary").textContent).toBe("0");
  });

  it("keeps the list busy while archives load", async () => {
    const pending = deferred<Response>();
    mockFetch(() => pending.promise);
    const view = createView();

    const loading = view.load();

    expect(node("archives").getAttribute("aria-busy")).toBe("true");
    expect(node("archives-loading").hidden).toBe(false);

    pending.resolve(jsonResponse([archive()]));
    await loading;

    expect(node("archives").getAttribute("aria-busy")).toBe("false");
    expect(node("archives-loading").hidden).toBe(true);
    expect(node("archives").children.length).toBe(1);
  });

  it("shows the API error in the message slot", async () => {
    mockFetch(() => jsonResponse({ detail: "нет доступа" }, 401));
    const view = createView();

    await view.load();

    const message = node("archives-message");
    expect(message.hidden).toBe(false);
    expect(message.textContent).toBe("Нет доступа к API. Откройте страницу с разрешённого адреса.");
  });

  it("expires a row in place and refreshes the list once a minute", async () => {
    const fetched = mockFetch(() =>
      jsonResponse([archive({ expires_at: "2026-11-26T09:00:00Z" })]),
    );
    const view = createView();

    await view.load();
    await vi.advanceTimersByTimeAsync(1000);

    const row = node("archives").querySelector(".archive-row");
    const link = row?.querySelector<HTMLAnchorElement>("a.button");
    expect(row?.classList.contains("is-expired")).toBe(true);
    expect(link?.hasAttribute("href")).toBe(false);
    expect(link?.getAttribute("aria-disabled")).toBe("true");
    expect(link?.textContent).toBe("Истёк");
    expect(row?.querySelector(".countdown")?.textContent).toBe("истёк");
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

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
