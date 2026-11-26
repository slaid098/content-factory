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
    photo_stock: 14,
    target: 10,
    refill_at: 3,
    photo_target: 30,
    photo_refill_at: 5,
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
    loading: node("profiles-loading"),
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

function mediaButton(): HTMLButtonElement {
  const found = card().querySelector<HTMLButtonElement>(".profile-media");
  if (!found) {
    throw new Error("profile preview button missing");
  }
  return found;
}

function dialog(): HTMLElement {
  const found = document.body.querySelector<HTMLElement>(".profile-dialog");
  if (!found) {
    throw new Error("profile dialog missing");
  }
  return found;
}

function dialogTitle(): string {
  return dialog().querySelector(".profile-dialog-title")?.textContent ?? "";
}

function targetInput(): HTMLInputElement {
  return card().querySelectorAll<HTMLInputElement>("input[type=number]")[0] as HTMLInputElement;
}

function refillInput(): HTMLInputElement {
  return card().querySelectorAll<HTMLInputElement>("input[type=number]")[1] as HTMLInputElement;
}

function photoTargetInput(): HTMLInputElement {
  return card().querySelectorAll<HTMLInputElement>("input[type=number]")[2] as HTMLInputElement;
}

function photoRefillInput(): HTMLInputElement {
  return card().querySelectorAll<HTMLInputElement>("input[type=number]")[3] as HTMLInputElement;
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
  it("renders a profile card with counters, settings and the last run", async () => {
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
    expect(card().textContent).toContain("Shorts 4 / 10");
    expect(card().textContent).toContain("Посты 14 / 30");
    expect(card().textContent).toContain("Добор");
    expect(card().textContent).toContain("ролики и посты");
    expect(card().querySelector(".profile-switch-label")?.textContent).toBe("Добор");
    expect(card().querySelector(".profile-switch-note")?.textContent).toBe("ролики и посты");
    expect(
      Array.from(card().querySelectorAll(".profile-group-title"), (title) => title.textContent),
    ).toEqual(["Ролики", "Посты"]);
    expect(targetInput().value).toBe("12");
    expect(targetInput().placeholder).toBe("10");
    expect(refillInput().value).toBe("");
    expect(refillInput().placeholder).toBe("3 (глобально)");
    expect(photoTargetInput().value).toBe("30");
    expect(photoRefillInput().value).toBe("5");
    expect(photoRefillInput().disabled).toBe(false);
    expect(switchInput().checked).toBe(false);
    expect(switchInput().getAttribute("role")).toBe("switch");
    expect(switchInput().getAttribute("aria-checked")).toBe("false");
    expect(card().textContent).toContain("готово");
    expect(card().textContent).toContain("партия: 5 готово");
    expect(card().textContent).toContain("1 с ошибкой");
    expect(card().textContent).toContain("завершена");
    expect(card().querySelector<HTMLImageElement>(".profile-preview")?.src).toContain(
      "/api/v1/content/profiles/dating/profile_1/preview.png",
    );
  });

  it("builds a compact, uncropped thumbnail from the profile dimensions", async () => {
    mockFetch(() => jsonResponse({ profiles: [profile({ width: 544, height: 960 })] }));
    const view = createView();

    await view.load();

    const image = card().querySelector<HTMLImageElement>(".profile-preview");
    expect(image).not.toBeNull();
    expect(image?.getAttribute("width")).toBe("544");
    expect(image?.getAttribute("height")).toBe("960");
    expect(image?.getAttribute("loading")).toBe("lazy");
    expect(image?.getAttribute("decoding")).toBe("async");
    expect(image?.alt).toBe("Превью профиля dating/profile_1");
    expect(mediaButton().tagName).toBe("BUTTON");
    expect(mediaButton().getAttribute("aria-label")).toBe(
      "Открыть превью профиля dating/profile_1",
    );
    expect(mediaButton().getAttribute("aria-haspopup")).toBe("dialog");
    expect(mediaButton().style.getPropertyValue("--preview-ratio")).toBe(String(544 / 960));
  });

  it("builds one slot for square, portrait and wide previews", async () => {
    mockFetch(() =>
      jsonResponse({
        profiles: [
          profile({ id: "dating/square", width: 1080, height: 1080 }),
          profile({ id: "dating/portrait", width: 544, height: 960 }),
          profile({ id: "dating/wide", width: 1280, height: 720 }),
        ],
      }),
    );
    const view = createView();

    await view.load();

    const ratios = Array.from(
      node("profiles-list").querySelectorAll<HTMLElement>(".profile-media"),
    ).map((media) => media.style.getPropertyValue("--preview-ratio"));
    expect(ratios).toEqual(["1", String(544 / 960), String(1280 / 720)]);
  });

  it("shows the Shorts progress and the photo counter without a goal", async () => {
    mockFetch(() =>
      jsonResponse({
        profiles: [
          profile({ stock: 4, target: 10, photo_stock: 14, photo_target: 0, photo_refill_at: 0 }),
        ],
      }),
    );
    const view = createView();

    await view.load();

    const bar = card().querySelector<HTMLElement>(".profile-progress");
    expect(bar?.getAttribute("role")).toBe("progressbar");
    expect(bar?.getAttribute("aria-valuenow")).toBe("4");
    expect(bar?.getAttribute("aria-valuemax")).toBe("10");
    expect(bar?.getAttribute("aria-valuetext")).toBe("4 из 10");
    const fill = card().querySelector<HTMLElement>(".profile-progress-fill");
    expect(fill?.style.width).toBe("40%");
    expect(card().textContent).toContain("Shorts 4 / 10");
    expect(card().textContent).toContain("Посты 14");
    expect(card().textContent).toContain("добор постов выключен");
    expect(card().querySelector<HTMLElement>(".profile-progress-posts")?.hidden).toBe(true);
    expect(photoRefillInput().disabled).toBe(true);
  });

  it("clamps a stock above the target to one hundred percent", async () => {
    mockFetch(() => jsonResponse({ profiles: [profile({ stock: 12, target: 10 })] }));
    const view = createView();

    await view.load();

    const fill = card().querySelector<HTMLElement>(".profile-progress-fill");
    expect(fill?.style.width).toBe("100%");
    expect(card().textContent).toContain("Shorts 12 / 10");
  });

  it("shows the photo goal with its value and progress", async () => {
    mockFetch(() =>
      jsonResponse({
        profiles: [profile({ photo_stock: 14, photo_target: 30, photo_refill_at: 5 })],
      }),
    );
    const view = createView();

    await view.load();

    expect(card().textContent).toContain("Посты 14 / 30");
    expect(card().querySelector<HTMLElement>(".profile-counter-note")?.hidden).toBe(true);
    const bar = card().querySelector<HTMLElement>(".profile-progress-posts");
    expect(bar?.hidden).toBe(false);
    expect(bar?.getAttribute("aria-valuenow")).toBe("14");
    expect(bar?.getAttribute("aria-valuemax")).toBe("30");
    expect(bar?.getAttribute("aria-valuetext")).toBe("14 из 30");
    const fill = bar?.querySelector<HTMLElement>(".profile-progress-fill");
    expect(Number.parseFloat(fill?.style.width ?? "0")).toBeCloseTo((14 / 30) * 100, 5);
    expect(photoRefillInput().disabled).toBe(false);
  });

  it("clamps the photo progress when the stock exceeds the goal", async () => {
    mockFetch(() => jsonResponse({ profiles: [profile({ photo_stock: 40, photo_target: 30 })] }));
    const view = createView();

    await view.load();

    const bar = card().querySelector<HTMLElement>(".profile-progress-posts");
    const fill = bar?.querySelector<HTMLElement>(".profile-progress-fill");
    expect(bar?.getAttribute("aria-valuenow")).toBe("30");
    expect(fill?.style.width).toBe("100%");
    expect(card().textContent).toContain("Посты 40 / 30");
  });

  it("shows zero counters when the target and the photo stock are zero", async () => {
    mockFetch(() =>
      jsonResponse({
        profiles: [
          profile({ stock: 0, target: 0, photo_stock: 0, photo_target: 0, photo_refill_at: 0 }),
        ],
      }),
    );
    const view = createView();

    await view.load();

    expect(card().textContent).toContain("Shorts 0 / 0");
    expect(card().textContent).toContain("Посты 0");
    const bar = card().querySelector<HTMLElement>(".profile-progress");
    expect(bar?.getAttribute("aria-valuenow")).toBe("0");
    expect(bar?.getAttribute("aria-valuemax")).toBe("0");
    const fill = card().querySelector<HTMLElement>(".profile-progress-fill");
    expect(fill?.style.width).toBe("0%");
    expect(card().querySelector<HTMLElement>(".profile-progress-posts")?.hidden).toBe(true);
    expect(photoRefillInput().disabled).toBe(true);
  });

  it("defaults a missing photo stock, goal and threshold to zero", async () => {
    const row = profile();
    delete row.photo_stock;
    delete row.photo_target;
    delete row.photo_refill_at;
    mockFetch(() => jsonResponse({ profiles: [row] }));
    const view = createView();

    await view.load();

    expect(card().textContent).toContain("Посты 0");
    expect(card().textContent).toContain("добор постов выключен");
    expect(photoTargetInput().value).toBe("0");
    expect(photoRefillInput().value).toBe("0");
    expect(photoRefillInput().disabled).toBe(true);
  });

  it("keeps the previous photo stock when a PATCH response omits it", async () => {
    const updated = profile({ refill_enabled: true });
    delete updated.photo_stock;
    mockFetch((_input, init) =>
      init?.method === "PATCH"
        ? jsonResponse(updated)
        : jsonResponse({ profiles: [profile({ photo_stock: 14 })] }),
    );
    const view = createView();
    await view.load();

    switchInput().click();
    await flush();

    expect(card().textContent).toContain("Посты 14 / 30");
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

  it("marks the list busy while loading", async () => {
    const pending = deferred<Response>();
    mockFetch(() => pending.promise);
    const view = createView();

    const loading = view.load();

    expect(node("profiles-list").getAttribute("aria-busy")).toBe("true");
    expect(node<HTMLButtonElement>("profiles-refresh").disabled).toBe(true);
    expect(node("profiles-loading").hidden).toBe(false);
    expect(node("profiles-loading").textContent).toBe("Загружаем профили…");

    pending.resolve(jsonResponse({ profiles: [profile()] }));
    await loading;

    expect(node("profiles-list").hasAttribute("aria-busy")).toBe(false);
    expect(node("profiles-loading").hidden).toBe(true);
    expect(node<HTMLButtonElement>("profiles-refresh").disabled).toBe(false);
  });

  it("keeps the preview slot stable when the image fails", async () => {
    mockFetch(() => jsonResponse({ profiles: [profile({ width: 544, height: 960 })] }));
    const view = createView();
    await view.load();

    const media = mediaButton();
    const image = media.querySelector<HTMLImageElement>(".profile-preview");
    const fallback = media.querySelector<HTMLElement>(".profile-preview-fallback");
    const slotStyle = media.getAttribute("style");
    expect(fallback?.hidden).toBe(true);

    image?.dispatchEvent(new Event("error"));

    expect(image?.hidden).toBe(true);
    expect(fallback?.hidden).toBe(false);
    expect(fallback?.textContent).toBe("Нет превью");
    expect(fallback?.getAttribute("role")).toBe("img");
    expect(fallback?.title).toBe("Превью появится после первого ролика или поста.");
    expect(fallback?.getAttribute("aria-label")).toBe(
      "Превью появится после первого ролика или поста.",
    );
    expect(mediaButton()).toBe(media);
    expect(media.getAttribute("style")).toBe(slotStyle);
    expect(media.style.getPropertyValue("--preview-ratio")).toBe(String(544 / 960));
    expect(image?.getAttribute("width")).toBe("544");
    expect(image?.getAttribute("height")).toBe("960");
  });

  it("opens one shared dialog and restores focus on Escape", async () => {
    mockFetch(() => jsonResponse({ profiles: [profile()] }));
    const view = createView();
    await view.load();

    const button = mediaButton();
    button.focus();
    button.click();

    expect(document.body.querySelectorAll(".profile-dialog")).toHaveLength(1);
    expect(dialog().hidden).toBe(false);
    expect(dialog().getAttribute("role")).toBe("dialog");
    expect(dialog().getAttribute("aria-modal")).toBe("true");
    expect(dialogTitle()).toBe("dating/profile_1 · 1080×1920");
    const labelledBy = dialog().getAttribute("aria-labelledby") ?? "";
    expect(dialog().querySelector(`#${labelledBy}`)?.textContent).toBe(
      "dating/profile_1 · 1080×1920",
    );
    const image = dialog().querySelector<HTMLImageElement>(".profile-dialog-image");
    expect(image?.getAttribute("src")).toContain(
      "/api/v1/content/profiles/dating/profile_1/preview.png",
    );
    const close = dialog().querySelector<HTMLButtonElement>(".profile-dialog-close");
    expect(close?.getAttribute("aria-label")).toBe("Закрыть превью");
    expect(document.activeElement).toBe(close);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));

    expect(dialog().hidden).toBe(true);
    expect(document.activeElement).toBe(button);
  });

  it("keeps focus inside the dialog and restores it to the thumbnail", async () => {
    mockFetch(() => jsonResponse({ profiles: [profile()] }));
    const view = createView();
    await view.load();

    const trigger = mediaButton();
    trigger.focus();
    trigger.click();

    const close = dialog().querySelector<HTMLButtonElement>(".profile-dialog-close");
    expect(close).not.toBeNull();
    expect(document.activeElement).toBe(close);

    dialog().focus();
    expect(document.activeElement).toBe(close);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", cancelable: true }));
    expect(document.activeElement).toBe(close);

    node<HTMLButtonElement>("profiles-refresh").focus();
    expect(document.activeElement).toBe(close);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(dialog().hidden).toBe(true);
    expect(document.activeElement).toBe(trigger);
  });

  it("reuses the same dialog for every card", async () => {
    mockFetch(() =>
      jsonResponse({
        profiles: [
          profile(),
          profile({ id: "dating/profile_2", name: "profile_2", width: 1080, height: 1080 }),
        ],
      }),
    );
    const view = createView();
    await view.load();

    const buttons = node("profiles-list").querySelectorAll<HTMLButtonElement>(".profile-media");
    expect(document.body.querySelectorAll(".profile-dialog")).toHaveLength(1);

    buttons[0]?.click();
    expect(dialogTitle()).toContain("dating/profile_1");

    dialog().querySelector<HTMLButtonElement>(".profile-dialog-close")?.click();
    buttons[1]?.click();

    expect(dialogTitle()).toBe("dating/profile_2 · 1080×1080");
  });

  it("closes on the backdrop and the close button but keeps clicks inside the panel", async () => {
    mockFetch(() => jsonResponse({ profiles: [profile()] }));
    const view = createView();
    await view.load();

    mediaButton().click();
    dialog().querySelector<HTMLElement>(".profile-dialog-panel")?.click();
    expect(dialog().hidden).toBe(false);

    dialog().click();
    expect(dialog().hidden).toBe(true);

    mediaButton().click();
    dialog().querySelector<HTMLButtonElement>(".profile-dialog-close")?.click();
    expect(dialog().hidden).toBe(true);
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
    expect(switchInput().getAttribute("aria-checked")).toBe("true");
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

  it("saves the photo goal and updates the post counter", async () => {
    const fetched = mockFetch((_input, init) =>
      init?.method === "PATCH"
        ? jsonResponse(profile({ photo_target: 40, photo_refill_at: 5 }))
        : jsonResponse({ profiles: [profile({ photo_target: 30, photo_refill_at: 5 })] }),
    );
    const view = createView();
    await view.load();

    photoTargetInput().value = "40";
    photoTargetInput().dispatchEvent(new Event("change", { bubbles: true }));

    expect(card().getAttribute("aria-busy")).toBe("true");
    expect(photoTargetInput().disabled).toBe(true);
    expect(photoRefillInput().disabled).toBe(true);
    await flush();

    const patch = fetched.calls().find((call) => call.init?.method === "PATCH");
    expect(bodyOf(patch?.init)).toEqual({ photo_target: 40 });
    expect(card().textContent).toContain("Посты 14 / 40");
    expect(photoTargetInput().value).toBe("40");
    expect(photoRefillInput().disabled).toBe(false);
    expect(card().getAttribute("aria-busy")).toBe("false");
  });

  it("saves the photo threshold when photo refill is on", async () => {
    const fetched = mockFetch((_input, init) =>
      init?.method === "PATCH"
        ? jsonResponse(profile({ photo_target: 30, photo_refill_at: 6 }))
        : jsonResponse({ profiles: [profile({ photo_target: 30, photo_refill_at: 5 })] }),
    );
    const view = createView();
    await view.load();

    photoRefillInput().value = "6";
    photoRefillInput().dispatchEvent(new Event("change", { bubbles: true }));
    await flush();

    const patch = fetched.calls().find((call) => call.init?.method === "PATCH");
    expect(bodyOf(patch?.init)).toEqual({ photo_refill_at: 6 });
    expect(photoRefillInput().value).toBe("6");
  });

  it("turns photo refill off with a zero pair and disables the threshold", async () => {
    const fetched = mockFetch((_input, init) =>
      init?.method === "PATCH"
        ? jsonResponse(profile({ photo_target: 0, photo_refill_at: 0 }))
        : jsonResponse({ profiles: [profile({ photo_target: 30, photo_refill_at: 5 })] }),
    );
    const view = createView();
    await view.load();
    expect(photoRefillInput().disabled).toBe(false);

    photoTargetInput().value = "0";
    photoTargetInput().dispatchEvent(new Event("change", { bubbles: true }));

    expect(photoRefillInput().value).toBe("0");
    expect(photoRefillInput().disabled).toBe(true);
    await flush();

    const patch = fetched.calls().find((call) => call.init?.method === "PATCH");
    expect(bodyOf(patch?.init)).toEqual({ photo_target: 0, photo_refill_at: 0 });
    expect(card().textContent).toContain("Посты 14");
    expect(card().textContent).not.toContain("Посты 14 /");
    expect(card().textContent).toContain("добор постов выключен");
    expect(card().querySelector<HTMLElement>(".profile-progress-posts")?.hidden).toBe(true);
    expect(photoTargetInput().value).toBe("0");
    expect(photoRefillInput().value).toBe("0");
    expect(photoRefillInput().disabled).toBe(true);
  });

  it("enables the photo threshold when the goal returns above zero", async () => {
    const fetched = mockFetch((_input, init) =>
      init?.method === "PATCH"
        ? jsonResponse(profile({ photo_target: 12, photo_refill_at: 0 }))
        : jsonResponse({ profiles: [profile({ photo_target: 0, photo_refill_at: 0 })] }),
    );
    const view = createView();
    await view.load();
    expect(photoRefillInput().disabled).toBe(true);

    photoTargetInput().value = "12";
    photoTargetInput().dispatchEvent(new Event("change", { bubbles: true }));

    expect(photoRefillInput().disabled).toBe(true);
    await flush();

    const patch = fetched.calls().find((call) => call.init?.method === "PATCH");
    expect(bodyOf(patch?.init)).toEqual({ photo_target: 12 });
    expect(card().textContent).toContain("Посты 14 / 12");
    expect(photoRefillInput().disabled).toBe(false);
  });

  it("rejects an empty or out-of-range photo goal without a request", async () => {
    const fetched = mockFetch(() => jsonResponse({ profiles: [profile()] }));
    const view = createView();
    await view.load();

    photoTargetInput().value = "1.5";
    photoTargetInput().dispatchEvent(new Event("change", { bubbles: true }));
    await flush();

    expect(card().textContent).toContain("Введите целое число от 0 до 1000.");
    expect(photoTargetInput().value).toBe("30");

    photoTargetInput().value = "";
    photoTargetInput().dispatchEvent(new Event("change", { bubbles: true }));
    await flush();

    expect(photoTargetInput().value).toBe("30");
    expect(fetched.calls().filter((call) => call.init?.method === "PATCH")).toHaveLength(0);
  });

  it("shows a 422 on photo save and restores the threshold", async () => {
    mockFetch((_input, init) =>
      init?.method === "PATCH"
        ? jsonResponse({ detail: "photo_refill_at должен быть меньше photo_target: 10 >= 10" }, 422)
        : jsonResponse({ profiles: [profile({ photo_target: 10, photo_refill_at: 5 })] }),
    );
    const view = createView();
    await view.load();

    photoRefillInput().value = "10";
    photoRefillInput().dispatchEvent(new Event("change", { bubbles: true }));
    await flush();

    expect(card().textContent).toContain("Не сохранено:");
    expect(card().textContent).toContain("photo_refill_at должен быть меньше photo_target");
    expect(photoRefillInput().value).toBe("5");
    expect(photoRefillInput().disabled).toBe(false);
    expect(card().getAttribute("aria-busy")).toBe("false");
  });

  it("reports a 500 on photo save and restores the goal", async () => {
    mockFetch((_input, init) =>
      init?.method === "PATCH"
        ? jsonResponse({ detail: "profile settings write failed" }, 500)
        : jsonResponse({ profiles: [profile()] }),
    );
    const view = createView();
    await view.load();

    photoTargetInput().value = "70";
    photoTargetInput().dispatchEvent(new Event("change", { bubbles: true }));
    await flush();

    expect(card().textContent).toContain("Не сохранено: profile settings write failed");
    expect(photoTargetInput().value).toBe("30");
    expect(card().getAttribute("aria-busy")).toBe("false");
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
    expect(photoTargetInput().disabled).toBe(true);
    expect(photoRefillInput().disabled).toBe(true);
    expect(card().getAttribute("aria-busy")).toBe("true");

    await view.load();
    expect(listCalls).toBe(1);

    pending.resolve(jsonResponse(profile({ refill_enabled: true })));
    await flush();

    expect(node<HTMLButtonElement>("profiles-refresh").disabled).toBe(false);
    expect(switchInput().disabled).toBe(false);
    expect(photoTargetInput().disabled).toBe(false);
    expect(photoRefillInput().disabled).toBe(false);
    expect(card().getAttribute("aria-busy")).toBe("false");
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

    expect(card().textContent).toContain("Shorts 7 / 10");
    expect(card().textContent).not.toContain("Shorts 99");
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
