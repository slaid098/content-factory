import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MESSAGES } from "../../src/lib/http";
import {
  bodyOf,
  type FetchHandler,
  flush,
  jsonResponse,
  type MockFetch,
  mockFetch,
  node,
  resetPage,
} from "./helpers";

const NOW = new Date("2026-11-26T10:00:00Z");

interface ApiData {
  stock?: unknown;
  topics?: unknown;
  archives?: unknown;
  settings?: unknown;
  queries?: unknown;
  profiles?: unknown;
  delivery?: unknown;
}

function stockRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    language: "ru",
    stock: 4,
    target: 10,
    titled: 3,
    untitled: 1,
    video_bytes: 2 * 1024 * 1024,
    ...overrides,
  };
}

const defaultSettings = {
  languages: [{ code: "ru", enabled: true, stock_target: 10 }],
  run: { batch: 5, interval: "6h", max_duration: "45m" },
  search: { max_queries: 20 },
  filter: { max: 100 },
  download: { workers: 4 },
  delivery: { max_count: 50 },
};

const defaultStock = {
  generated_at: "2026-11-26T10:00:00Z",
  languages: [
    stockRow(),
    stockRow({ language: "en", stock: 0, target: 5, titled: 0, untitled: 0 }),
  ],
};

const defaultTopics = { topics: [{ query: "заправка", count: 2 }], unclassified: 1 };

function handler(data: ApiData = {}): FetchHandler {
  return (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/v1/stock") {
      return jsonResponse(data.stock ?? defaultStock);
    }
    if (url.startsWith("/api/v1/content/topics")) {
      return jsonResponse(data.topics ?? defaultTopics);
    }
    if (url === "/api/v1/content/archives") {
      return jsonResponse(data.archives ?? []);
    }
    if (url === "/api/v1/content/settings") {
      return jsonResponse(data.settings ?? defaultSettings);
    }
    if (url.startsWith("/api/v1/content/queries") && method === "GET") {
      return jsonResponse(
        data.queries ?? {
          queries: [{ query: "заправка", count: 2, in_file: true }],
          unclassified: 1,
          total: 2,
        },
      );
    }
    if (url === "/api/v1/content/queries" && method === "POST") {
      return jsonResponse({ created: true });
    }
    if (url.startsWith("/api/v1/content/profiles")) {
      return jsonResponse(data.profiles ?? { profiles: [] });
    }
    if (url === "/api/v1/content/deliveries/link") {
      return jsonResponse(
        data.delivery ?? {
          request_id: "req-1",
          language: "ru",
          status: "building",
          expires_at: "2026-11-26T11:00:00Z",
        },
      );
    }
    throw new Error(`unexpected ${method} ${url}`);
  };
}

async function boot(data: ApiData = {}): Promise<MockFetch> {
  return bootWith(handler(data));
}

async function bootWith(fetchHandler: FetchHandler): Promise<MockFetch> {
  const fetched = mockFetch(fetchHandler);
  vi.resetModules();
  await import("../../src/app");
  await flush();
  await flush();
  return fetched;
}

function languageSelect(): HTMLSelectElement {
  return node<HTMLSelectElement>("language");
}

function countInput(): HTMLInputElement {
  return node<HTMLInputElement>("count");
}

function submitDelivery(): void {
  node<HTMLFormElement>("delivery-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  );
}

beforeEach(() => {
  resetPage();
  vi.useFakeTimers({ now: NOW });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("app", () => {
  it("renders stock, topics, language selects and limits on boot", async () => {
    await boot();

    expect(node("stock-list").children.length).toBe(2);
    expect(node("stock-list").textContent).toContain("Русский");
    expect(node("stock-list").textContent).toContain("4 из 10");
    expect(node("stock-list").textContent).toContain("3 с названием · 1 без · 2,0 МБ");
    expect(node("stock-updated").textContent).toMatch(
      /^обновлено \d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}$/,
    );

    expect(languageSelect().disabled).toBe(false);
    expect(languageSelect().value).toBe("ru");
    expect(languageSelect().options[0]?.textContent).toBe("Русский — 3");
    expect(node<HTMLSelectElement>("queries-language").options[0]?.textContent).toBe("Русский");

    expect(node("available").textContent).toBe("доступно 3");
    expect(node("count-hint").textContent).toBe("Не больше 3 за раз.");
    expect(countInput().max).toBe("3");
    expect(countInput().value).toBe("1");
    expect(countInput().disabled).toBe(false);
    expect(node<HTMLButtonElement>("deliver").disabled).toBe(false);

    expect(node("topic-list").children.length).toBe(2);
    expect(node("topic-list").textContent).toContain("Все темы");
    expect(node("topic-list").textContent).toContain("4");
    expect(node("topics-language").textContent).toBe("Русский");
    expect(node("unclassified").hidden).toBe(false);
    expect(node("unclassified").textContent).toBe("Без темы: 1 — они входят в «Все темы».");
  });

  it("handles an empty language list", async () => {
    const fetched = await boot({
      stock: { generated_at: "2026-11-26T10:00:00Z", languages: [] },
    });

    expect(node("stock-list").hidden).toBe(true);
    expect(node("stock-empty").hidden).toBe(false);
    expect(languageSelect().disabled).toBe(true);
    expect(languageSelect().options[0]?.textContent).toBe("Нет доступных языков");
    expect(node("available").textContent).toBe("видео пока нет");
    expect(node("count-hint").textContent).toBe(
      "Видео этого языка пока нет — выберите другой язык.",
    );
    expect(node<HTMLButtonElement>("deliver").disabled).toBe(true);
    expect(fetched.calls().some((call) => call.url.startsWith("/api/v1/content/topics"))).toBe(
      false,
    );
  });

  it("shows the zero stock and untitled states", async () => {
    await boot({
      stock: {
        generated_at: "2026-11-26T10:00:00Z",
        languages: [stockRow({ stock: 0, titled: 0, untitled: 0 })],
      },
    });

    expect(node("available").textContent).toBe("видео пока нет");
    expect(node<HTMLButtonElement>("deliver").disabled).toBe(true);
    expect(node<HTMLButtonElement>("deliver").title).toBe("В стоке выбранного языка нет видео");

    resetPage();
    vi.resetModules();
    await boot({
      stock: {
        generated_at: "2026-11-26T10:00:00Z",
        languages: [stockRow({ titled: 0 })],
      },
    });

    expect(node("available").textContent).toBe("ждём обработку названий");
    expect(node<HTMLButtonElement>("deliver").disabled).toBe(true);
    expect(node<HTMLButtonElement>("deliver").title).toBe("Ждём обработку названий видео");
    expect(countInput().disabled).toBe(true);
  });

  it("caps the delivery count by settings and explains the limit", async () => {
    await boot({
      stock: {
        generated_at: "2026-11-26T10:00:00Z",
        languages: [stockRow({ titled: 150, stock: 150 })],
      },
      settings: { ...defaultSettings, delivery: { max_count: 100 } },
    });

    expect(node("available").textContent).toBe("доступно 150");
    expect(node("count-hint").textContent).toBe("Не больше 100 за раз (лимит из настроек).");
    expect(countInput().max).toBe("100");
  });

  it("falls back to one hundred when settings have no delivery limit", async () => {
    const settings = { ...defaultSettings, delivery: {} };
    await boot({
      stock: {
        generated_at: "2026-11-26T10:00:00Z",
        languages: [stockRow({ titled: 150, stock: 150 })],
      },
      settings,
    });

    expect(node("count-hint").textContent).toBe("Не больше 100 за раз (лимит из настроек).");
  });

  it("shows a stock error without hiding the rest of the page", async () => {
    await bootWith((input, init) => {
      if (String(input) === "/api/v1/stock") {
        throw new TypeError("offline");
      }
      return handler()(input, init);
    });

    expect(node("stock-message").hidden).toBe(false);
    expect(node("stock-message").textContent).toBe(MESSAGES.offline);
    expect(node("stock-empty").hidden).toBe(true);
  });

  it("switches languages and loads topics and queries for the new one", async () => {
    const fetched = await boot();
    const select = languageSelect();
    select.value = "en";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await flush();

    expect(node("topics-language").textContent).toBe("Английский");
    expect(fetched.calls().some((call) => call.url.includes("topics?language=en"))).toBe(true);
    expect(fetched.calls().some((call) => call.url.includes("/content/queries"))).toBe(false);

    node<HTMLButtonElement>("tab-queries").click();
    await flush();

    expect(fetched.calls().some((call) => call.url.includes("queries?language=en"))).toBe(true);
  });

  it("ignores selecting the same language", async () => {
    const fetched = await boot();
    const before = fetched.calls().filter((call) => call.url.includes("topics")).length;

    node<HTMLSelectElement>("queries-language").value = "ru";
    node<HTMLSelectElement>("queries-language").dispatchEvent(
      new Event("change", { bubbles: true }),
    );
    await flush();

    expect(fetched.calls().filter((call) => call.url.includes("topics")).length).toBe(before);
  });

  it("submits a delivery request with a UUID and a clamped count", async () => {
    const fetched = await boot();
    countInput().value = "99";

    submitDelivery();
    await flush();

    const post = fetched.calls().find((call) => call.url === "/api/v1/content/deliveries/link");
    expect(post?.init?.method).toBe("POST");
    expect(bodyOf(post?.init)).toMatchObject({ language: "ru", count: 3 });
    expect(String(bodyOf(post?.init).request_id)).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(countInput().value).toBe("3");
    expect(node("delivery-status").textContent).toBe(
      "Сборка идёт. Статус обновляется автоматически.",
    );
    expect(node("delivery-form").getAttribute("aria-busy")).toBe("false");
  });

  it("keeps a random UUID fallback for insecure contexts", async () => {
    const getRandomValues = vi.fn((target: Uint8Array) => {
      for (let index = 0; index < target.length; index += 1) {
        target[index] = index;
      }
      return target;
    });
    vi.stubGlobal("crypto", { getRandomValues });
    const fetched = await boot();

    submitDelivery();
    await flush();

    const post = fetched.calls().find((call) => call.url === "/api/v1/content/deliveries/link");
    expect(bodyOf(post?.init).request_id).toBe("00010203-0405-4607-8809-0a0b0c0d0e0f");
    expect(getRandomValues).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["empty", MESSAGES.empty],
    ["failed", "Сборка не удалась. Подробности в карточке."],
    ["ready", "Архив готов — скачайте его по ссылке."],
    ["ready_local", "Архив готов — скачайте его по ссылке."],
  ])("reports a %s delivery result", async (status, message) => {
    await boot({
      delivery: {
        request_id: "req-1",
        language: "ru",
        status,
        expires_at: "2026-11-26T11:00:00Z",
      },
    });

    submitDelivery();
    await flush();

    expect(node("delivery-status").textContent).toBe(message);
  });

  it("shows a delivery request error", async () => {
    await bootWith((input, init) => {
      if (String(input) === "/api/v1/content/deliveries/link") {
        return jsonResponse({ detail: "busy" }, 409);
      }
      return handler()(input, init);
    });

    submitDelivery();
    await flush();

    expect(node("delivery-status").textContent).toBe(MESSAGES.busy);
    expect(node("delivery-status").classList.contains("is-error")).toBe(true);
  });

  it("does not submit without stock or while the page is busy", async () => {
    const fetched = await boot({
      stock: {
        generated_at: "2026-11-26T10:00:00Z",
        languages: [stockRow({ stock: 0, titled: 0, untitled: 0 })],
      },
    });

    submitDelivery();
    await flush();

    expect(fetched.calls().some((call) => call.url === "/api/v1/content/deliveries/link")).toBe(
      false,
    );
  });

  it("refreshes stock and archives and shows a language that replaced the old one", async () => {
    let call = 0;
    const fetched = await bootWith((input, init) => {
      if (String(input) === "/api/v1/stock") {
        call += 1;
        return jsonResponse(
          call === 1
            ? defaultStock
            : {
                generated_at: "2026-11-26T10:00:00Z",
                languages: [stockRow({ language: "de", stock: 7, titled: 7 })],
              },
        );
      }
      return handler()(input, init);
    });

    expect(languageSelect().value).toBe("ru");
    node<HTMLButtonElement>("refresh").click();
    await flush();

    expect(languageSelect().value).toBe("de");
    expect(languageSelect().options[0]?.textContent).toBe("Немецкий — 7");
    expect(node("topics-language").textContent).toBe("Немецкий");
    expect(fetched.calls().filter((callItem) => callItem.url === "/api/v1/stock").length).toBe(2);
  });

  it("loads profiles when the tab opens and refreshes them every minute", async () => {
    const fetched = await boot();

    node<HTMLButtonElement>("tab-profiles").click();
    await flush();

    expect(fetched.calls().filter((call) => call.url === "/api/v1/content/profiles").length).toBe(
      1,
    );
    expect(node("profiles-empty").hidden).toBe(false);

    await vi.advanceTimersByTimeAsync(60_000);

    expect(
      fetched.calls().filter((call) => call.url === "/api/v1/content/profiles").length,
    ).toBeGreaterThanOrEqual(2);
  });

  it("retries settings when the tab opens after a failed load", async () => {
    let settingsCalls = 0;
    const fetched = await bootWith((input, init) => {
      if (String(input) === "/api/v1/content/settings") {
        settingsCalls += 1;
        return settingsCalls === 1
          ? jsonResponse({ detail: "unavailable" }, 500)
          : jsonResponse(defaultSettings);
      }
      return handler()(input, init);
    });

    expect(node("settings-message").textContent).toBe("unavailable");
    node<HTMLButtonElement>("tab-settings").click();
    await flush();

    expect(settingsCalls).toBe(2);
    expect(node("settings-message").hidden).toBe(true);
    expect(fetched.calls().some((call) => call.url === "/api/v1/content/settings")).toBe(true);
  });

  it("refreshes the archives and the stock after a job finishes", async () => {
    let stockCalls = 0;
    const fetched = await bootWith((input, init) => {
      if (String(input) === "/api/v1/stock") {
        stockCalls += 1;
      }
      if (String(input) === "/api/v1/content/deliveries/link") {
        return jsonResponse({
          request_id: "req-1",
          language: "ru",
          status: "ready",
          url: "https://filebin.example/req-1.zip",
          expires_at: "2026-11-26T11:00:00Z",
        });
      }
      if (String(input) === "/api/v1/content/archives") {
        return jsonResponse([
          {
            request_id: "req-1",
            language: "ru",
            storage: "local",
            size: 10,
            created_at: "2026-11-26T10:00:00Z",
            expires_at: "2026-11-26T11:00:00Z",
          },
        ]);
      }
      return handler()(input, init);
    });
    await flush();

    submitDelivery();
    await waitUntil(
      () =>
        node("archives").querySelector(".archive-card")?.classList.contains("is-fresh") ?? false,
    );

    expect(node("delivery-status").textContent).toBe("Архив готов — скачайте его по ссылке.");
    expect(stockCalls).toBeGreaterThanOrEqual(2);
    expect(fetched.calls().filter((call) => call.url === "/api/v1/content/archives")).toHaveLength(
      2,
    );
    expect(fetched.calls().some((call) => call.url.includes("topics"))).toBe(true);
  });

  it("polls a building job and refreshes when it becomes ready", async () => {
    let polls = 0;
    const fetched = await bootWith((input, init) => {
      const url = String(input);
      if (url === "/api/v1/content/deliveries/link") {
        return jsonResponse({
          request_id: "req-1",
          language: "ru",
          status: "building",
          expires_at: "2026-11-26T11:00:00Z",
        });
      }
      if (url === "/api/v1/content/deliveries/ru/req-1") {
        polls += 1;
        return jsonResponse({
          request_id: "req-1",
          language: "ru",
          status: "ready_local",
          error: "filebin down",
          expires_at: "2026-11-26T11:00:00Z",
        });
      }
      return handler()(input, init);
    });

    submitDelivery();
    await flush();
    await vi.advanceTimersByTimeAsync(3000);

    expect(polls).toBe(1);
    expect(node("delivery-status").textContent).toBe(
      "Архив готов локально — скачайте его с сервера.",
    );
    expect(fetched.calls().some((call) => call.url === "/api/v1/stock")).toBe(true);
  });

  it("does not refresh twice while a refresh is already running", async () => {
    const pending = deferred<Response>();
    let stockCalls = 0;
    await bootWith((input, init) => {
      if (String(input) === "/api/v1/stock") {
        stockCalls += 1;
        if (stockCalls === 2) {
          return pending.promise;
        }
      }
      return handler()(input, init);
    });

    node<HTMLButtonElement>("refresh").click();
    node<HTMLButtonElement>("refresh").click();
    await flush();

    expect(stockCalls).toBe(2);

    pending.resolve(jsonResponse(defaultStock));
    await flush();
  });

  it("handles keyboard tab navigation and loads the queries panel", async () => {
    const fetched = await boot();
    const deliveryTab = node<HTMLButtonElement>("tab-delivery");
    deliveryTab.focus();

    deliveryTab.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    await flush();

    expect(node("panel-queries").hidden).toBe(false);
    expect(fetched.calls().some((call) => call.url.includes("queries?language=ru"))).toBe(true);
  });
});

async function waitUntil(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    await flush();
    if (predicate()) {
      return;
    }
  }
  throw new Error("condition was not met");
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
