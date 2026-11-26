import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createProfilesView } from "../../src/profiles";
import {
  bodyOf,
  type FetchHandler,
  flush,
  jsonResponse,
  mockFetch,
  node,
  resetPage,
} from "./helpers";

function summary(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "dating/profile_1",
    category: "dating",
    name: "profile_1",
    language: "ru",
    niche: "dating",
    width: 544,
    height: 960,
    audio: true,
    outputs: ["video", "photo"],
    stock: 8,
    photo_stock: 14,
    target: 20,
    refill_at: 5,
    photo_target: 30,
    photo_refill_at: 5,
    refill_enabled: false,
    target_override: null,
    refill_at_override: null,
    preview_url: "/api/v1/content/profiles/dating/profile_1/preview",
    last_run: null,
    ...overrides,
  };
}

function detail(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ...summary(overrides),
    production: [
      {
        producer: "vast",
        type: "video",
        checks: [
          { name: "clef", stage: "source" },
          { name: "mp4", stage: "final" },
        ],
      },
      {
        producer: "card",
        type: "photo",
        checks: [{ name: "image", stage: "final" }],
      },
    ],
    settings: {
      video: {
        width: 544,
        height: 960,
        frames: 121,
        frames_jitter: 0,
        fps: 25,
        renderer: "default",
        image: "video-gen:test",
        audio_tracks: 3,
      },
      rent: {
        gpu: "RTX 4090",
        max_cost: 0.35,
        disk_space: 80,
        gpu_ram: 30,
        inet_down: 500,
        inet_up_cost: 0.004,
        inet_down_cost: 0.004,
      },
      text: {
        font: "arialbd.ttf",
        negative_prompt: null,
        cfg: null,
        style: { font_size: 0.06, text_color: "#000000" },
      },
      delivery: { title_mode: "generated", title: null, hashtags: [] },
    },
  };
}

function prompts(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    template: { filename: "prompt_template.txt", text: "Сцена: {age}" },
    cards: [
      {
        id: "c01",
        values: {
          id: "c01",
          age: 30,
          hair_color_text: "dark brown",
          place: "cafe",
          season: "summer",
          pose_text: "standing",
        },
        prompt: "Женщина 30 лет",
        overridden: false,
      },
    ],
    photo: { filename: "text_prompt.txt", text: "Голос: спокойный" },
    ...overrides,
  };
}

function handler(overrides: { detail?: unknown; prompts?: unknown } = {}): FetchHandler {
  return (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/v1/content/profiles" && method === "GET") {
      return jsonResponse({ profiles: [summary()] });
    }
    if (url === "/api/v1/content/profiles/dating/profile_1" && method === "GET") {
      return jsonResponse(overrides.detail ?? detail());
    }
    if (url === "/api/v1/content/profiles/dating/profile_1/prompts" && method === "GET") {
      return jsonResponse(overrides.prompts ?? prompts());
    }
    if (url.endsWith("/prompts/template") && method === "PUT") {
      return jsonResponse({ filename: "prompt_template.txt", text: bodyOf(init).text });
    }
    if (url.endsWith("/prompts/cards/c01") && method === "PUT") {
      const text = String(bodyOf(init).text ?? "");
      const overridden = text.trim() !== "";
      return jsonResponse({
        id: "c01",
        prompt: overridden ? text : "Женщина 30 лет",
        overridden,
      });
    }
    if (url.endsWith("/prompts/text") && method === "PUT") {
      return jsonResponse({ filename: "text_prompt.txt", text: bodyOf(init).text });
    }
    if (url === "/api/v1/content/profiles/dating/profile_1" && method === "PATCH") {
      return jsonResponse(summary({ refill_enabled: true, ...bodyOf(init) }));
    }
    throw new Error(`unexpected ${method} ${url}`);
  };
}

async function loadView(): Promise<ReturnType<typeof createProfilesView>> {
  const view = createProfilesView();
  await view.load();
  return view;
}

async function openProfile(): Promise<ReturnType<typeof createProfilesView>> {
  const view = await loadView();
  const button = gear();
  button.focus();
  button.click();
  await flush();
  await flush();
  return view;
}

function gear(): HTMLButtonElement {
  const found = node("profiles-list").querySelector<HTMLButtonElement>(".profile-open");
  if (!found) {
    throw new Error("gear button missing");
  }
  return found;
}

function refillButton(): HTMLButtonElement {
  const found = node("profiles-list").querySelector<HTMLButtonElement>(".profile-refill");
  if (!found) {
    throw new Error("refill button missing");
  }
  return found;
}

function templateTextarea(): HTMLTextAreaElement {
  const found = node("profile-video").querySelector<HTMLTextAreaElement>(".prompt-text");
  if (!found) {
    throw new Error("template textarea missing");
  }
  return found;
}

function cardBlock(): HTMLElement {
  const found = node("profile-video").querySelector<HTMLElement>(".prompt-card");
  if (!found) {
    throw new Error("card block missing");
  }
  return found;
}

function cardTextarea(): HTMLTextAreaElement {
  const found = cardBlock().querySelector<HTMLTextAreaElement>(".prompt-text");
  if (!found) {
    throw new Error("card textarea missing");
  }
  return found;
}

function chipTexts(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".pipeline-chip"), (chip) => {
    return chip.textContent ?? "";
  });
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  resetPage();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("createProfilesView", () => {
  it("renders rows without previews and with source flags and counters", async () => {
    mockFetch(handler());

    await loadView();

    const row = node("profiles-list").querySelector(".profile-row");
    expect(row).not.toBeNull();
    expect(row?.textContent).toContain("dating/profile_1");
    expect(row?.textContent).toContain("Русский · dating");
    expect(row?.textContent).toContain("музыка");
    expect(row?.textContent).toContain("544×960");
    expect(row?.textContent).toContain("видео");
    expect(row?.textContent).toContain("фото");
    expect(row?.textContent).toContain("8 / 20");
    expect(row?.querySelector(".profile-preview")).toBeNull();
    expect(refillButton().getAttribute("aria-label")).toBe("Включить добор dating/profile_1");
    expect(gear().getAttribute("aria-label")).toBe("Открыть профиль dating/profile_1");
  });

  it("patches refill from the list, disables the button while saving and reports the result", async () => {
    const pending = deferred<Response>();
    const fetched = mockFetch((input, init) => {
      if (init?.method === "PATCH") {
        return pending.promise;
      }
      return handler()(input, init);
    });

    await loadView();
    refillButton().click();
    await flush();

    const patch = fetched.calls().find((call) => call.init?.method === "PATCH");
    expect(patch?.url).toBe("/api/v1/content/profiles/dating/profile_1");
    expect(bodyOf(patch?.init)).toEqual({ refill_enabled: true });
    expect(refillButton().disabled).toBe(true);

    pending.resolve(jsonResponse(summary({ refill_enabled: true })));
    await flush();

    expect(refillButton().disabled).toBe(false);
    expect(refillButton().getAttribute("aria-label")).toBe("Пауза добора dating/profile_1");
  });

  it("keeps a refill error visible inline", async () => {
    mockFetch((input, init) =>
      init?.method === "PATCH"
        ? jsonResponse({ detail: "настройки недоступны" }, 500)
        : handler()(input, init),
    );

    await loadView();
    refillButton().click();
    await flush();

    const status = node("profiles-list").querySelector<HTMLElement>(".profile-row-status");
    expect(status?.textContent).toBe("настройки недоступны");
    expect(status?.classList.contains("is-error")).toBe(true);
    expect(refillButton().disabled).toBe(false);
  });

  it("opens the detail with meta, output tabs and the recipe chips", async () => {
    mockFetch(handler());

    await openProfile();

    expect(node("profiles-list-view").hidden).toBe(true);
    expect(node("profile-view").hidden).toBe(false);
    expect(node("profile-view-title").textContent).toBe("dating/profile_1");
    expect(node("profile-view-meta").textContent).toBe("Русский · dating · 544×960 · видео/фото");
    expect(node("profile-tab-video").textContent).toBe("Видео · 8");
    expect(node("profile-tab-photo").textContent).toBe("Фото · 14");
    expect(node("profile-tab-settings").textContent).toBe("Настройки");
    expect(node("profile-panel-video").hidden).toBe(false);
    expect(chipTexts(node("profile-video"))).toEqual([
      "vast",
      "проверка кадров",
      "текст + музыка",
      "проверка mp4",
      "сток",
      "card",
      "рендер",
      "проверка фото",
      "сток",
    ]);
    expect(cardBlock().querySelector(".prompt-card-values")?.textContent).toBe(
      "возраст: 30 · волосы: dark brown · место: cafe · сезон: summer · pose_text=standing",
    );
  });

  it("shows unknown checks and producers without inventing internal steps", async () => {
    mockFetch(
      handler({
        detail: {
          ...detail(),
          production: [
            {
              producer: "custom",
              type: "video",
              checks: [{ name: "custom-check", stage: "source" }],
            },
          ],
        },
      }),
    );

    await openProfile();

    expect(chipTexts(node("profile-video"))).toEqual(["custom", "custom-check", "сток"]);
  });

  it("toggles refill from the profile header and syncs the list row", async () => {
    const fetched = mockFetch(handler());
    await openProfile();
    const button = node<HTMLButtonElement>("profile-view-refill");

    expect(button.getAttribute("aria-label")).toBe("Включить добор dating/profile_1");
    button.click();
    await flush();

    const patch = fetched.calls().find((call) => call.init?.method === "PATCH");
    expect(bodyOf(patch?.init)).toEqual({ refill_enabled: true });
    expect(node<HTMLButtonElement>("profile-view-refill").getAttribute("aria-label")).toBe(
      "Пауза добора dating/profile_1",
    );
    expect(node("profiles-list").querySelector(".profile-refill")?.getAttribute("aria-label")).toBe(
      "Пауза добора dating/profile_1",
    );
  });

  it("autosaves the template after 500 ms", async () => {
    vi.useFakeTimers();
    const fetched = mockFetch(handler());
    await openProfile();

    const textarea = templateTextarea();
    textarea.value = "Новый шаблон";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    await vi.advanceTimersByTimeAsync(499);
    expect(fetched.calls().some((call) => call.init?.method === "PUT")).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    await flush();

    const put = fetched.calls().find((call) => call.url.endsWith("/prompts/template"));
    expect(put?.init?.method).toBe("PUT");
    expect(bodyOf(put?.init)).toEqual({ text: "Новый шаблон" });
    expect(node("profile-video").querySelector(".prompt-status")?.textContent).toBe("Сохранено.");
  });

  it("saves a card override and resets it when the text is cleared", async () => {
    vi.useFakeTimers();
    const fetched = mockFetch(handler());
    await openProfile();

    const textarea = cardTextarea();
    textarea.value = "Мой промт";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    await vi.advanceTimersByTimeAsync(500);
    await flush();

    const override = fetched.calls().find((call) => call.url.endsWith("/prompts/cards/c01"));
    expect(bodyOf(override?.init)).toEqual({ text: "Мой промт" });
    expect(cardBlock().textContent).toContain("Свой промт");

    textarea.value = "   ";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    await vi.advanceTimersByTimeAsync(500);
    await flush();

    const reset = fetched
      .calls()
      .filter((call) => call.url.endsWith("/prompts/cards/c01"))
      .at(-1);
    expect(bodyOf(reset?.init)).toEqual({ text: "   " });
    expect(textarea.value).toBe("Женщина 30 лет");
    expect(cardBlock().textContent).toContain("Из шаблона");
  });

  it("saves the photo prompt from the photo tab", async () => {
    vi.useFakeTimers();
    const fetched = mockFetch(handler());
    await openProfile();

    node<HTMLButtonElement>("profile-tab-photo").click();
    const textarea = node("profile-photo").querySelector<HTMLTextAreaElement>(".prompt-text");
    expect(textarea?.value).toBe("Голос: спокойный");

    if (!textarea) {
      throw new Error("photo textarea missing");
    }
    textarea.value = "Новый голос";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    await vi.advanceTimersByTimeAsync(500);
    await flush();

    const put = fetched.calls().find((call) => call.url.endsWith("/prompts/text"));
    expect(bodyOf(put?.init)).toEqual({ text: "Новый голос" });
  });

  it("shows that a photo profile has no text prompt", async () => {
    mockFetch(handler({ prompts: prompts({ photo: null }) }));

    await openProfile();
    node<HTMLButtonElement>("profile-tab-photo").click();

    expect(node("profile-photo").textContent).toContain("Промтов нет.");
    expect(node("profile-photo").querySelector("textarea")).toBeNull();
  });

  it("renders read-only settings and patches the editable refill fields", async () => {
    const fetched = mockFetch(handler());
    await openProfile();
    node<HTMLButtonElement>("profile-tab-settings").click();

    expect(node("profile-settings-video").textContent).toContain("Ширина");
    expect(node("profile-settings-video").textContent).toContain("544");
    expect(node("profile-settings-rent").textContent).toContain("RTX 4090");
    expect(node("profile-settings-text").textContent).toContain("arialbd.ttf");
    expect(node("profile-settings-delivery").textContent).toContain("generated");
    expect(node<HTMLInputElement>("profile-target").placeholder).toBe("20");
    expect(node<HTMLInputElement>("profile-photo-target").value).toBe("30");

    const target = node<HTMLInputElement>("profile-target");
    target.value = "25";
    target.dispatchEvent(new Event("change", { bubbles: true }));
    await flush();

    const patch = fetched.calls().find((call) => call.init?.method === "PATCH");
    expect(bodyOf(patch?.init)).toMatchObject({ target: 25 });
    expect(node("profile-settings-status").textContent).toBe("Сохранено.");
  });

  it("shows cards in the photo tab when the profile has no video output", async () => {
    mockFetch(handler({ detail: detail({ outputs: ["photo"] }) }));

    await openProfile();

    expect(node("profile-tab-video").hidden).toBe(true);
    expect(node<HTMLButtonElement>("profile-tab-photo").getAttribute("aria-pressed")).toBe("true");
    expect(node("profile-panel-video").hidden).toBe(true);
    expect(node("profile-panel-photo").hidden).toBe(false);
    expect(node("profile-photo").querySelector(".prompt-card")).not.toBeNull();
  });

  it("opens the lightbox, closes it on Escape and restores focus", async () => {
    mockFetch(handler());
    await openProfile();

    const preview = node<HTMLButtonElement>("profile-preview");
    preview.click();

    const dialog = node<HTMLDialogElement>("profile-lightbox");
    expect(dialog.open).toBe(true);
    expect(node("profile-lightbox-image").getAttribute("src")).toBe(
      "/api/v1/content/profiles/dating/profile_1/preview",
    );

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

    expect(dialog.open).toBe(false);
    expect(document.activeElement).toBe(preview);
  });

  it("closes the lightbox from the close button and the backdrop", async () => {
    mockFetch(handler());
    await openProfile();
    const preview = node<HTMLButtonElement>("profile-preview");
    const dialog = node<HTMLDialogElement>("profile-lightbox");

    preview.click();
    node<HTMLButtonElement>("profile-lightbox-close").click();
    expect(dialog.open).toBe(false);

    preview.click();
    dialog.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(dialog.open).toBe(false);
  });

  it("returns from the profile view to the list and restores the gear focus", async () => {
    mockFetch(handler());
    await openProfile();
    const trigger = gear();

    node<HTMLButtonElement>("profile-back").click();

    expect(node("profile-view").hidden).toBe(true);
    expect(node("profiles-list-view").hidden).toBe(false);
    expect(document.activeElement).toBe(trigger);
  });
});
