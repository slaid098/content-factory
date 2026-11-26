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
  archives?: unknown;
  settings?: unknown;
  queries?: unknown;
  profiles?: unknown;
  delivery?: unknown;
}

function stockRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    language: "ru",
    stock: 12,
    target: 20,
    deficit: 8,
    titled: 10,
    untitled: 2,
    video_bytes: 2 * 1024 * 1024,
    ...overrides,
  };
}

function profileRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "dating/profile_1",
    category: "dating",
    name: "profile_1",
    language: "ru",
    language_label: "Русский",
    niche: "dating",
    title: "Знакомства",
    summary: "Профиль для знакомств",
    width: 544,
    height: 960,
    audio: true,
    outputs: ["video", "photo"],
    stock: 8,
    photo_stock: 14,
    target: 20,
    refill_at: 5,
    refill_enabled: false,
    target_override: null,
    refill_at_override: null,
    photo_target: 30,
    photo_refill_at: 5,
    preview_url: "/api/v1/content/profiles/dating/profile_1/preview.png",
    last_run: null,
    ...overrides,
  };
}

const defaultSettings = {
  languages: [{ code: "ru", enabled: true, stock_target: 20 }],
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
  general: [
    { language: "ru", stock: 4, target: 10 },
    { language: "en", stock: 0, target: 5 },
  ],
};

const defaultProfiles = {
  profiles: [
    profileRow(),
    profileRow({ id: "dating/profile_2", name: "profile_2", stock: 2, target: 5 }),
  ],
  languages: [
    { code: "ru", label: "Русский" },
    { code: "en", label: "Английский" },
  ],
  niches: ["dating"],
  total: 2,
};

function handler(data: ApiData = {}): FetchHandler {
  return (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/v1/stock") {
      return jsonResponse(data.stock ?? defaultStock);
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
    if (url === "/api/v1/content/profiles") {
      return jsonResponse(data.profiles ?? defaultProfiles);
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

function profileSelect(): HTMLSelectElement {
  return node<HTMLSelectElement>("delivery-profile");
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
  it("renders general stock and profile counters on boot", async () => {
    await boot();

    expect(node("general-language").textContent).toBe("Русский");
    expect(node("general-value").textContent).toBe("4 / 10");
    expect(node("general-bar").getAttribute("aria-valuenow")).toBe("4");
    expect(node("general-bar").getAttribute("aria-valuemax")).toBe("10");
    expect(node("general-bar-fill").style.width).toBe("40%");
    expect(node("general-hint").textContent).toBe("Готово к выдаче: 4.");

    expect(node("profiles-total").textContent).toBe("10");
    expect(node("profiles-stat-hint").textContent).toBe("Профилей: 2 · видео: 10.");
    expect(node("profiles-stock").children.length).toBe(2);
    expect(node("profiles-stock").textContent).toContain("dating/profile_1");
    expect(node("profiles-stock").textContent).toContain("8 / 20");

    expect(node("stock-updated").textContent).toMatch(
      /^обновлено \d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}$/,
    );
  });

  it("fills the delivery selects and caps the count by general stock", async () => {
    await boot();

    expect(languageSelect().disabled).toBe(false);
    expect(languageSelect().value).toBe("ru");
    expect(languageSelect().options[0]?.textContent).toBe("Русский");
    expect(node<HTMLSelectElement>("queries-language").options[0]?.textContent).toBe("Русский");
    expect(profileSelect().options.length).toBe(2);
    expect(profileSelect().options[0]?.textContent).toBe("dating/profile_1");

    expect(node("available").textContent).toBe("доступно 4");
    expect(node("count-hint").textContent).toBe("Не больше 4 за раз.");
    expect(countInput().max).toBe("4");
    expect(countInput().value).toBe("1");
    expect(countInput().disabled).toBe(false);
    expect(node<HTMLButtonElement>("deliver").disabled).toBe(false);
  });

  it("caps the delivery count by settings and explains the limit", async () => {
    await boot({
      stock: {
        generated_at: "2026-11-26T10:00:00Z",
        languages: [stockRow({ stock: 150, target: 200, titled: 150 })],
        general: [{ language: "ru", stock: 150, target: 200 }],
      },
      settings: { ...defaultSettings, delivery: { max_count: 100 } },
    });

    expect(node("available").textContent).toBe("доступно 150");
    expect(node("count-hint").textContent).toBe("Не больше 100 за раз (лимит из настроек).");
    expect(countInput().max).toBe("100");
  });

  it("handles an empty language list", async () => {
    const fetched = await boot({
      stock: { generated_at: "2026-11-26T10:00:00Z", languages: [], general: [] },
    });

    expect(node("general-language").textContent).toBe("—");
    expect(node("general-value").textContent).toBe("0 / 0");
    expect(languageSelect().disabled).toBe(true);
    expect(languageSelect().options[0]?.textContent).toBe("Нет доступных языков");
    expect(node("available").textContent).toBe("видео пока нет");
    expect(node("count-hint").textContent).toBe("Видео этого источника пока нет.");
    expect(node<HTMLButtonElement>("deliver").disabled).toBe(true);
    expect(fetched.calls().some((call) => call.url.includes("/content/queries"))).toBe(false);
  });

  it("shows the zero stock state and disables the build", async () => {
    await boot({
      stock: {
        generated_at: "2026-11-26T10:00:00Z",
        languages: [stockRow({ stock: 0, target: 10 })],
        general: [{ language: "ru", stock: 0, target: 10 }],
      },
    });

    expect(node("available").textContent).toBe("видео пока нет");
    expect(node("general-hint").textContent).toBe("Видео без профиля пока нет.");
    expect(node<HTMLButtonElement>("deliver").disabled).toBe(true);
    expect(node<HTMLButtonElement>("deliver").title).toBe("В стоке выбранного источника нет видео");
    expect(countInput().disabled).toBe(true);
  });

  it("switches to a profile source and submits a profile delivery", async () => {
    const fetched = await boot();

    node<HTMLButtonElement>("source-profile").click();

    expect(node("source-profile").getAttribute("aria-pressed")).toBe("true");
    expect(node("source-general").getAttribute("aria-pressed")).toBe("false");
    expect(node("source-profile").classList.contains("is-active")).toBe(true);
    expect(node("language-field").hidden).toBe(true);
    expect(node("profile-field").hidden).toBe(false);
    expect(profileSelect().value).toBe("dating/profile_1");
    expect(node("available").textContent).toBe("доступно 8");
    expect(countInput().max).toBe("8");

    countInput().value = "99";
    submitDelivery();
    await flush();

    const post = fetched.calls().find((call) => call.url === "/api/v1/content/deliveries/link");
    expect(post?.init?.method).toBe("POST");
    expect(bodyOf(post?.init)).toMatchObject({ profile: "dating/profile_1", count: 8 });
    expect(String(bodyOf(post?.init).request_id)).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(countInput().value).toBe("8");
    expect(node("delivery-status").textContent).toBe(
      "Сборка идёт. Статус обновляется автоматически.",
    );

    node<HTMLButtonElement>("source-general").click();

    expect(node("language-field").hidden).toBe(false);
    expect(node("profile-field").hidden).toBe(true);
    expect(node("available").textContent).toBe("доступно 4");
  });

  it("submits a general delivery request with a UUID and a clamped count", async () => {
    const fetched = await boot();
    countInput().value = "99";

    submitDelivery();
    await flush();

    const post = fetched.calls().find((call) => call.url === "/api/v1/content/deliveries/link");
    expect(post?.init?.method).toBe("POST");
    expect(bodyOf(post?.init)).toMatchObject({ language: "ru", count: 4 });
    expect(String(bodyOf(post?.init).request_id)).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(countInput().value).toBe("4");
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
        languages: [stockRow({ stock: 0, target: 10 })],
        general: [{ language: "ru", stock: 0, target: 10 }],
      },
    });

    submitDelivery();
    await flush();

    expect(fetched.calls().some((call) => call.url === "/api/v1/content/deliveries/link")).toBe(
      false,
    );
  });

  it("shows a stock error without hiding the counters", async () => {
    await bootWith((input, init) => {
      if (String(input) === "/api/v1/stock") {
        throw new TypeError("offline");
      }
      return handler()(input, init);
    });

    expect(node("stock-message").hidden).toBe(false);
    expect(node("stock-message").textContent).toBe(MESSAGES.offline);
    expect(node("profiles-total").textContent).toBe("10");
  });

  it("marks the stock section busy until the request resolves", async () => {
    const pending = deferred<Response>();
    mockFetch((input, init) => {
      if (String(input) === "/api/v1/stock") {
        return pending.promise;
      }
      return handler()(input, init);
    });
    vi.resetModules();
    const booting = import("../../src/app");
    await booting;

    expect(node("stock-panel").getAttribute("aria-busy")).toBe("true");
    expect(node("stock-loading").hidden).toBe(false);

    pending.resolve(jsonResponse(defaultStock));
    await flush();

    expect(node("stock-panel").getAttribute("aria-busy")).toBe("false");
    expect(node("stock-loading").hidden).toBe(true);
    expect(node("general-value").textContent).toBe("4 / 10");
  });

  it("refreshes stock, profiles, settings and archives", async () => {
    let call = 0;
    const fetched = await bootWith((input, init) => {
      if (String(input) === "/api/v1/stock") {
        call += 1;
        return jsonResponse(
          call === 1
            ? defaultStock
            : {
                generated_at: "2026-11-26T10:00:00Z",
                languages: [stockRow({ language: "de", stock: 7, target: 10, titled: 7 })],
                general: [{ language: "de", stock: 7, target: 10 }],
              },
        );
      }
      return handler()(input, init);
    });

    expect(languageSelect().value).toBe("ru");
    node<HTMLButtonElement>("refresh").click();
    await flush();

    expect(languageSelect().value).toBe("de");
    expect(languageSelect().options[0]?.textContent).toBe("Немецкий");
    expect(node("general-language").textContent).toBe("Немецкий");
    expect(node("general-value").textContent).toBe("7 / 10");
    expect(fetched.calls().filter((item) => item.url === "/api/v1/stock").length).toBe(2);
    expect(fetched.calls().filter((item) => item.url === "/api/v1/content/profiles").length).toBe(
      2,
    );
    expect(fetched.calls().filter((item) => item.url === "/api/v1/content/settings").length).toBe(
      2,
    );
    expect(fetched.calls().filter((item) => item.url === "/api/v1/content/archives").length).toBe(
      2,
    );
  });

  it("loads queries when the tab opens and switches their language", async () => {
    const fetched = await boot();

    node<HTMLButtonElement>("tab-queries").click();
    await flush();

    expect(fetched.calls().some((call) => call.url.includes("queries?language=ru"))).toBe(true);

    node<HTMLSelectElement>("queries-language").value = "en";
    node<HTMLSelectElement>("queries-language").dispatchEvent(
      new Event("change", { bubbles: true }),
    );
    await flush();

    expect(node("general-language").textContent).toBe("Английский");
    expect(fetched.calls().some((call) => call.url.includes("queries?language=en"))).toBe(true);
  });

  it("ignores selecting the same language", async () => {
    const fetched = await boot();
    node<HTMLButtonElement>("tab-queries").click();
    await flush();
    const before = fetched
      .calls()
      .filter((call) => call.url.includes("queries?language=ru")).length;

    node<HTMLSelectElement>("queries-language").value = "ru";
    node<HTMLSelectElement>("queries-language").dispatchEvent(
      new Event("change", { bubbles: true }),
    );
    await flush();

    expect(fetched.calls().filter((call) => call.url.includes("queries?language=ru")).length).toBe(
      before,
    );
  });

  it("shows a settings read error in the settings panel", async () => {
    await bootWith((input, init) => {
      if (String(input) === "/api/v1/content/settings") {
        return jsonResponse({ detail: "unavailable" }, 500);
      }
      return handler()(input, init);
    });

    expect(node("settings-message").hidden).toBe(false);
    expect(node("settings-message").textContent).toBe("unavailable");
    expect(node("count-hint").textContent).toBe("Не больше 4 за раз.");
  });

  it("refreshes the archives and the counters after a job finishes", async () => {
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
      () => node("archives").querySelector(".archive-row")?.classList.contains("is-fresh") ?? false,
    );

    expect(node("delivery-status").textContent).toBe("Архив готов — скачайте его по ссылке.");
    expect(stockCalls).toBeGreaterThanOrEqual(2);
    expect(fetched.calls().filter((call) => call.url === "/api/v1/content/archives")).toHaveLength(
      2,
    );
    expect(
      fetched.calls().filter((call) => call.url === "/api/v1/content/profiles").length,
    ).toBeGreaterThanOrEqual(2);
  });

  it("polls a building job and refreshes when it becomes ready", async () => {
    let polls = 0;
    await bootWith((input, init) => {
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
