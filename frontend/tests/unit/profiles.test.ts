import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createProfilesView } from "../../src/profiles";
import { bodyOf, flush, jsonResponse, mockFetch, node, resetPage } from "./helpers";

function profile(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "dating/profile_1",
    category: "dating",
    name: "profile_1",
    language: "ru",
    niche: "dating",
    width: 1080,
    height: 1920,
    audio: true,
    stock: 4,
    target: 10,
    refill_at: 3,
    refill_enabled: false,
    target_override: 12,
    refill_at_override: null,
    preview_url: "/api/v1/content/profiles/dating/profile_1/preview.png",
    last_run: {
      status: "done",
      finished_at: "2026-11-26T10:00:00Z",
      generated: 5,
      failed: 1,
      error: "",
    },
    ...overrides,
  };
}

function createView() {
  return createProfilesView({
    list: node("profiles-list"),
    message: node("profiles-message"),
    empty: node("profiles-empty"),
    refresh: node<HTMLButtonElement>("profiles-refresh"),
  });
}

function card(): HTMLElement {
  const found = node("profiles-list").querySelector<HTMLElement>(".profile-card");
  if (!found) {
    throw new Error("profile card missing");
  }
  return found;
}

function targetInput(): HTMLInputElement {
  return card().querySelectorAll<HTMLInputElement>("input[type=number]")[0] as HTMLInputElement;
}

function refillInput(): HTMLInputElement {
  return card().querySelectorAll<HTMLInputElement>("input[type=number]")[1] as HTMLInputElement;
}

function switchInput(): HTMLInputElement {
  return card().querySelector<HTMLInputElement>("input[type=checkbox]") as HTMLInputElement;
}

beforeEach(() => {
  resetPage();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createProfilesView", () => {
  it("renders a profile card with facts, overrides and the last run", async () => {
    mockFetch(() => jsonResponse({ profiles: [profile()] }));
    const view = createView();

    await view.load();

    expect(node("profiles-list").hidden).toBe(false);
    expect(node("profiles-empty").hidden).toBe(true);
    expect(card().textContent).toContain("dating/profile_1");
    expect(card().textContent).toContain("Русский");
    expect(card().textContent).toContain("dating");
    expect(card().textContent).toContain("1080×1920");
    expect(card().textContent).toContain("аудио");
    expect(card().textContent).toContain("Сток 4 / цель 10");
    expect(card().textContent).toContain("порог 3");
    expect(targetInput().value).toBe("12");
    expect(targetInput().placeholder).toBe("10");
    expect(refillInput().value).toBe("");
    expect(refillInput().placeholder).toBe("3 (глобально)");
    expect(switchInput().checked).toBe(false);
    expect(card().textContent).toContain("готово");
    expect(card().textContent).toContain("партия: 5 готово");
    expect(card().textContent).toContain("1 с ошибкой");
    expect(card().textContent).toContain("завершена");
    expect(card().querySelector<HTMLImageElement>("img")?.src).toContain(
      "/api/v1/content/profiles/dating/profile_1/preview.png",
    );
  });

  it("uses neutral fallbacks for missing optional data", async () => {
    mockFetch(() =>
      jsonResponse({
        profiles: [
          profile({
            niche: "",
            audio: false,
            last_run: { status: "custom", finished_at: null, generated: 0, failed: 0, error: "" },
          }),
        ],
      }),
    );
    const view = createView();

    await view.load();

    expect(card().textContent).toContain("без ниши");
    expect(card().textContent).toContain("без аудио");
    expect(card().textContent).toContain("custom");
    expect(card().textContent).toContain("ещё идёт");
  });

  it("hides the last run when there is none", async () => {
    mockFetch(() => jsonResponse({ profiles: [profile({ last_run: null })] }));
    const view = createView();

    await view.load();

    expect(card().querySelector<HTMLElement>(".profile-last-run")?.hidden).toBe(true);
  });

  it("truncates a long last-run error", async () => {
    mockFetch(() =>
      jsonResponse({
        profiles: [
          profile({
            last_run: {
              status: "failed",
              finished_at: "2026-11-26T10:00:00Z",
              generated: 0,
              failed: 1,
              error: "x".repeat(210),
            },
          }),
        ],
      }),
    );
    const view = createView();

    await view.load();

    const error = card().querySelector(".profile-error")?.textContent ?? "";
    expect(error.length).toBe(200);
    expect(error.endsWith("…")).toBe(true);
  });

  it("shows the empty state", async () => {
    mockFetch(() => jsonResponse({ profiles: [] }));
    const view = createView();

    await view.load();

    expect(node("profiles-list").hidden).toBe(true);
    expect(node("profiles-empty").hidden).toBe(false);
    expect(node("profiles-empty").textContent).toBe("Профилей пока нет.");
  });

  it("shows a list error", async () => {
    mockFetch(() => jsonResponse({ detail: "nope" }, 500));
    const view = createView();

    await view.load();

    expect(node("profiles-message").textContent).toBe("nope");
  });

  it("falls back to the placeholder when the preview image fails", async () => {
    mockFetch(() => jsonResponse({ profiles: [profile()] }));
    const view = createView();
    await view.load();

    const image = card().querySelector<HTMLImageElement>("img");
    image?.dispatchEvent(new Event("error"));

    expect(image?.hidden).toBe(true);
    expect(card().querySelector<HTMLElement>(".profile-preview-fallback")?.hidden).toBe(false);
  });

  it("saves the refill switch with a PATCH and updates the card", async () => {
    const fetched = mockFetch((_input, init) =>
      init?.method === "PATCH"
        ? jsonResponse(profile({ refill_enabled: true }))
        : jsonResponse({ profiles: [profile()] }),
    );
    const view = createView();
    await view.load();

    switchInput().click();
    await flush();

    const patch = fetched.calls().find((call) => call.init?.method === "PATCH");
    expect(patch?.url).toBe("/api/v1/content/profiles/dating/profile_1");
    expect(bodyOf(patch?.init)).toEqual({ refill_enabled: true });
    expect(card().textContent).toContain("Сохранено.");
    expect(card().getAttribute("aria-busy")).toBe("false");
    expect(node<HTMLButtonElement>("profiles-refresh").disabled).toBe(false);
  });

  it("saves a numeric target and an empty target as null", async () => {
    const fetched = mockFetch((_input, init) => {
      if (init?.method === "PATCH") {
        return jsonResponse(profile({ target_override: 15 }));
      }
      return jsonResponse({ profiles: [profile()] });
    });
    const view = createView();
    await view.load();

    targetInput().value = "15";
    targetInput().dispatchEvent(new Event("change", { bubbles: true }));
    await flush();

    const firstPatch = fetched.calls().find((call) => call.init?.method === "PATCH");
    expect(bodyOf(firstPatch?.init)).toEqual({ target: 15 });

    targetInput().value = "";
    targetInput().dispatchEvent(new Event("change", { bubbles: true }));
    await flush();

    const patches = fetched.calls().filter((call) => call.init?.method === "PATCH");
    expect(bodyOf(patches[1]?.init)).toEqual({ target: null });
  });

  it("saves the refill threshold including zero", async () => {
    const fetched = mockFetch((_input, init) =>
      init?.method === "PATCH"
        ? jsonResponse(profile({ refill_at_override: 0 }))
        : jsonResponse({ profiles: [profile()] }),
    );
    const view = createView();
    await view.load();

    refillInput().value = "0";
    refillInput().dispatchEvent(new Event("change", { bubbles: true }));
    await flush();

    const patch = fetched.calls().find((call) => call.init?.method === "PATCH");
    expect(bodyOf(patch?.init)).toEqual({ refill_at: 0 });
  });

  it("rejects a non-integer or out-of-range number without a request", async () => {
    const fetched = mockFetch(() => jsonResponse({ profiles: [profile()] }));
    const view = createView();
    await view.load();

    targetInput().value = "1.5";
    targetInput().dispatchEvent(new Event("change", { bubbles: true }));
    await flush();

    expect(card().textContent).toContain("Введите целое число от 1 до 1000.");
    expect(targetInput().value).toBe("12");
    expect(fetched.calls().filter((call) => call.init?.method === "PATCH")).toHaveLength(0);
  });

  it("reports a missing profile on PATCH", async () => {
    mockFetch((_input, init) =>
      init?.method === "PATCH"
        ? jsonResponse({ detail: "gone" }, 404)
        : jsonResponse({ profiles: [profile()] }),
    );
    const view = createView();
    await view.load();

    switchInput().click();
    await flush();

    expect(card().textContent).toContain("Профиль не найден");
    expect(card().querySelector(".profile-status")?.classList.contains("is-error")).toBe(true);
  });

  it("reports a PATCH failure with the server detail", async () => {
    mockFetch((_input, init) =>
      init?.method === "PATCH"
        ? jsonResponse({ detail: "conflict" }, 409)
        : jsonResponse({ profiles: [profile()] }),
    );
    const view = createView();
    await view.load();

    switchInput().click();
    await flush();

    expect(card().textContent).toContain("Не сохранено:");
    expect(card().textContent).toContain(
      "Сборка уже идёт. Дождитесь её завершения и попробуйте снова.",
    );
  });

  it("locks the card and the refresh during a pending PATCH", async () => {
    const pending = deferred<Response>();
    let listCalls = 0;
    mockFetch((_input, init) => {
      if (init?.method === "PATCH") {
        return pending.promise;
      }
      listCalls += 1;
      return jsonResponse({ profiles: [profile()] });
    });
    const view = createView();
    await view.load();

    switchInput().click();
    expect(node<HTMLButtonElement>("profiles-refresh").disabled).toBe(true);
    expect(switchInput().disabled).toBe(true);

    await view.load();
    expect(listCalls).toBe(1);

    pending.resolve(jsonResponse(profile({ refill_enabled: true })));
    await flush();

    expect(node<HTMLButtonElement>("profiles-refresh").disabled).toBe(false);
    expect(switchInput().disabled).toBe(false);
  });

  it("ignores a list response that started before a PATCH", async () => {
    const staleList = deferred<Response>();
    let listCalls = 0;
    mockFetch((_input, init) => {
      if (init?.method === "PATCH") {
        return jsonResponse(profile({ stock: 7 }));
      }
      listCalls += 1;
      return listCalls === 1 ? jsonResponse({ profiles: [profile()] }) : staleList.promise;
    });
    const view = createView();
    await view.load();

    const loading = view.load();
    switchInput().click();
    await flush();
    staleList.resolve(jsonResponse({ profiles: [profile({ stock: 99 })] }));
    await loading;

    expect(card().textContent).toContain("Сток 7 / цель 10");
    expect(card().textContent).not.toContain("Сток 99");
  });

  it("reloads from the refresh button", async () => {
    const fetched = mockFetch(() => jsonResponse({ profiles: [profile()] }));
    const view = createView();
    await view.load();

    node<HTMLButtonElement>("profiles-refresh").click();
    await flush();

    expect(fetched.calls().filter((call) => call.init?.method !== "PATCH")).toHaveLength(2);
  });
});

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
