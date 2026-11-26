import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createJobsView } from "../../src/jobs";
import { MESSAGES } from "../../src/lib/http";
import { jsonResponse, mockFetch, node, resetPage } from "./helpers";

const NOW = new Date("2026-11-26T10:00:00Z");

function createView(onFinished = vi.fn<(job: Record<string, unknown>) => void>()) {
  const view = createJobsView(
    {
      container: node("delivery-job"),
      title: node("job-title"),
      state: node("job-state"),
      message: node("job-message"),
      timer: node("job-timer"),
      link: node<HTMLAnchorElement>("job-link"),
      check: node<HTMLButtonElement>("job-check"),
    },
    { onFinished },
  );
  return { view, onFinished };
}

function job(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    request_id: "req-1",
    language: "ru",
    status: "ready",
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

describe("createJobsView", () => {
  it("starts a general build and shows an external ready link", async () => {
    mockFetch(() => jsonResponse(job({ url: "https://filebin.example/req-1.zip" })));
    const { view, onFinished } = createView();

    const result = await view.start({
      address: { language: "ru" },
      count: 2,
      requestId: "req-1",
    });

    expect(result.status).toBe("ready");
    expect(node("delivery-job").hidden).toBe(false);
    expect(node("delivery-job").classList.contains("is-ready")).toBe(true);
    expect(node("job-title").textContent).toBe("delivery-req-1.zip");
    expect(node("job-state").textContent).toBe("готово");
    expect(node("job-state").className).toBe("badge accent");
    expect(node("job-message").textContent).toBe("Архив залит на Litterbox. Ссылка живёт 24 часа.");
    const link = node<HTMLAnchorElement>("job-link");
    expect(link.hidden).toBe(false);
    expect(link.getAttribute("href")).toBe("https://filebin.example/req-1.zip");
    expect(link.textContent).toBe("Скачать архив");
    expect(node("job-timer").hidden).toBe(false);
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("false");
    expect(onFinished).toHaveBeenCalledWith(result);
    expect(mockFetchCalls()[0]?.url).toBe("/api/v1/content/deliveries/link");
    expect(JSON.parse(String(mockFetchCalls()[0]?.init?.body))).toEqual({
      language: "ru",
      count: 2,
      request_id: "req-1",
    });
  });

  it("starts a profile build with the profile address", async () => {
    mockFetch(() =>
      jsonResponse(job({ request_id: "req-2", profile: "dating/profile_1", status: "ready" })),
    );
    const { view } = createView();

    await view.start({
      address: { profile: "dating/profile_1" },
      count: 1,
      requestId: "req-2",
    });

    expect(node("job-title").textContent).toBe("delivery-req-2.zip");
    expect(JSON.parse(String(mockFetchCalls()[0]?.init?.body))).toEqual({
      profile: "dating/profile_1",
      count: 1,
      request_id: "req-2",
    });
  });

  it("shows a local archive when the external link is unavailable", async () => {
    mockFetch(() => jsonResponse(job({ status: "ready_local", error: "filebin down" })));
    const { view } = createView();

    await view.start({ address: { language: "ru" }, count: 2, requestId: "req-1" });

    const link = node<HTMLAnchorElement>("job-link");
    expect(link.hidden).toBe(false);
    expect(link.getAttribute("href")).toBe("/api/v1/content/archives/ru/req-1.zip");
    expect(link.download).toBe("delivery-req-1.zip");
    expect(link.textContent).toBe("Скачать локальный архив");
    expect(node("job-message").textContent).toBe(
      "Внешняя ссылка недоступна. Архив лежит на сервере. filebin down",
    );
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("false");
  });

  it("shows the empty and failed states with a warning badge", async () => {
    mockFetch(() => jsonResponse(job({ status: "empty" })));
    const { view } = createView();
    await view.start({ address: { language: "ru" }, count: 2, requestId: "req-1" });
    expect(node("job-state").className).toBe("badge warn");
    expect(node("job-message").textContent).toBe(MESSAGES.empty);
    expect(node("job-timer").hidden).toBe(true);
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("false");

    vi.unstubAllGlobals();
    mockFetch(() => jsonResponse(job({ status: "failed", error: "boom" })));
    const second = createView();
    await second.view.start({ address: { language: "ru" }, count: 2, requestId: "req-2" });
    expect(node("delivery-job").classList.contains("is-error")).toBe(true);
    expect(node("job-state").className).toBe("badge warn");
    expect(node("job-message").textContent).toBe("Сборка не удалась: boom");
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("false");
  });

  it("polls a building job until it becomes ready", async () => {
    mockFetch((_input, init) =>
      jsonResponse(
        init?.method === "POST"
          ? job({ status: "building" })
          : job({ url: "https://filebin.example/req-1.zip" }),
      ),
    );
    const { view, onFinished } = createView();

    await view.start({ address: { language: "ru" }, count: 1, requestId: "req-1" });
    expect(node("job-state").textContent).toBe("сборка");
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("true");
    expect(node("job-check").hidden).toBe(true);

    await vi.advanceTimersByTimeAsync(3000);

    expect(mockFetchCalls().length).toBe(2);
    expect(mockFetchCalls()[1]?.url).toBe("/api/v1/content/deliveries/ru/req-1");
    expect(node("job-state").textContent).toBe("готово");
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("false");
    expect(onFinished).toHaveBeenCalledTimes(1);
  });

  it("reports a missing job after a 404 poll", async () => {
    mockFetch((_input, init) =>
      jsonResponse(
        init?.method === "POST" ? job({ status: "building" }) : {},
        init?.method === "POST" ? 202 : 404,
      ),
    );
    const { view } = createView();

    await view.start({ address: { language: "ru" }, count: 1, requestId: "req-1" });
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("true");
    await vi.advanceTimersByTimeAsync(3000);

    expect(node("job-state").textContent).toBe("ошибка");
    expect(node("job-message").textContent).toBe(MESSAGES.jobGone);
    expect(node<HTMLAnchorElement>("job-link").hidden).toBe(true);
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("false");
  });

  it("clears the busy state when a poll fails", async () => {
    let call = 0;
    mockFetch(() => {
      call += 1;
      if (call === 1) {
        return jsonResponse(job({ status: "building" }));
      }
      throw new TypeError("offline");
    });
    const { view } = createView();

    await view.start({ address: { language: "ru" }, count: 1, requestId: "req-1" });
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("true");
    await vi.advanceTimersByTimeAsync(3000);

    expect(node("job-message").textContent).toBe(MESSAGES.offline);
    expect(node("job-check").hidden).toBe(false);
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("false");
  });

  it("stops polling after thirty minutes and offers a manual check", async () => {
    mockFetch(() => jsonResponse(job({ status: "building", expires_at: "2026-11-27T11:00:00Z" })));
    const { view } = createView();

    await view.start({ address: { language: "ru" }, count: 1, requestId: "req-1" });
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("true");
    vi.setSystemTime(NOW.getTime() + 30 * 60 * 1000 + 1);
    await vi.advanceTimersByTimeAsync(3000);

    expect(node("job-message").textContent).toBe(
      "Сборка идёт дольше 30 минут. Опрос остановлен — нажмите «Проверить».",
    );
    expect(node("job-check").hidden).toBe(false);
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("false");
  });

  it("checks the status on demand and reports a still running build", async () => {
    mockFetch(() => jsonResponse(job({ status: "building", expires_at: "2026-11-27T11:00:00Z" })));
    const { view } = createView();
    await view.start({ address: { language: "ru" }, count: 1, requestId: "req-1" });
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("true");

    vi.setSystemTime(NOW.getTime() + 30 * 60 * 1000 + 1);
    await vi.advanceTimersByTimeAsync(3000);
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("false");

    const check = node<HTMLButtonElement>("job-check");
    check.click();
    await vi.advanceTimersByTimeAsync(0);

    expect(node("job-message").textContent).toBe("Сборка всё ещё идёт. Нажмите «Проверить» позже.");
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("true");
    expect(check.disabled).toBe(false);
  });

  it("reports a missing job on a manual check", async () => {
    mockFetch((_input, init) =>
      jsonResponse(
        init?.method === "POST" ? job({ status: "building" }) : {},
        init?.method === "POST" ? 202 : 404,
      ),
    );
    const { view } = createView();
    await view.start({ address: { language: "ru" }, count: 1, requestId: "req-1" });
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("true");

    node<HTMLButtonElement>("job-check").click();
    await vi.advanceTimersByTimeAsync(0);

    expect(node("job-message").textContent).toBe(MESSAGES.jobGone);
    expect(node<HTMLAnchorElement>("job-link").hidden).toBe(true);
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("false");
  });

  it("reports a network failure on a manual check", async () => {
    let call = 0;
    mockFetch(() => {
      call += 1;
      if (call === 1) {
        return jsonResponse(job({ status: "building" }));
      }
      throw new TypeError("offline");
    });
    const { view } = createView();
    await view.start({ address: { language: "ru" }, count: 1, requestId: "req-1" });
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("true");

    node<HTMLButtonElement>("job-check").click();
    await vi.advanceTimersByTimeAsync(0);

    expect(node("job-message").textContent).toBe(MESSAGES.offline);
    expect(node("job-check").hidden).toBe(false);
    expect(node("delivery-job").getAttribute("aria-busy")).toBe("false");
  });

  it("expires the ready link and hides it", async () => {
    mockFetch(() => jsonResponse(job({ expires_at: "2026-11-26T09:00:00Z" })));
    const { view } = createView();

    await view.start({ address: { language: "ru" }, count: 1, requestId: "req-1" });

    expect(node("delivery-job").classList.contains("is-expired")).toBe(true);
    expect(node("job-timer").textContent).toBe("срок ссылки истёк");
    expect(node<HTMLAnchorElement>("job-link").hidden).toBe(true);
  });

  it("rejects a non-object build response", async () => {
    mockFetch(() => jsonResponse([]));
    const { view } = createView();

    await expect(
      view.start({ address: { language: "ru" }, count: 1, requestId: "req-1" }),
    ).rejects.toMatchObject({
      message: MESSAGES.badResponse,
      status: 0,
    });
  });

  it("does nothing on a manual check before any build", () => {
    const fetched = mockFetch(() => jsonResponse({}));

    node<HTMLButtonElement>("job-check").click();

    expect(fetched.mock).not.toHaveBeenCalled();
  });
});

function mockFetchCalls() {
  const fetchMock = vi.mocked(fetch);
  return fetchMock.mock.calls.map(([input, init]) => ({
    url: typeof input === "string" ? input : String(input),
    init,
  }));
}
