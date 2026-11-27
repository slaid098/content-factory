import { Buffer } from "node:buffer";

import { expect, type Page, test } from "@playwright/test";

const RU_STOCK = {
  language: "ru",
  stock: 12,
  target: 20,
  deficit: 8,
  titled: 10,
  untitled: 2,
  video_bytes: 1024,
};

const STOCK = {
  generated_at: "2026-11-26T10:00:00Z",
  languages: [RU_STOCK],
  general: [{ language: "ru", stock: 4, target: 10 }],
};

const PROFILE = {
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
  photo_target: 30,
  photo_refill_at: 5,
  refill_enabled: false,
  target_override: null,
  refill_at_override: null,
  preview_url: "/api/v1/content/profiles/dating/profile_1/preview.png",
  last_run: null,
};

async function mockStock(page: Page, stock = STOCK): Promise<void> {
  await page.route(/\/api\/v1\/stock$/, (route) => route.fulfill({ json: stock }));
}

async function mockProfiles(page: Page, profiles: unknown[] = [PROFILE]): Promise<void> {
  await page.route(/\/api\/v1\/content\/profiles$/, (route) =>
    route.fulfill({
      json: {
        profiles,
        languages: [{ code: "ru", label: "Русский" }],
        niches: profiles.length === 0 ? [] : ["dating"],
        total: profiles.length,
      },
    }),
  );
}

const PROFILE_DETAIL = {
  ...PROFILE,
  production: [
    {
      producer: "vast",
      type: "video",
      checks: [
        { name: "clef", stage: "source" },
        { name: "mp4", stage: "final" },
      ],
    },
    { producer: "card", type: "photo", checks: [{ name: "image", stage: "final" }] },
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
      style: { font_size: 0.06 },
    },
    delivery: { title_mode: "generated", title: null, hashtags: [] },
  },
};

const PROMPTS = {
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
      },
      prompt: "Женщина 30 лет",
      overridden: false,
    },
  ],
  photo: { filename: "text_prompt.txt", text: "Голос: спокойный" },
};

const RUNTIME_SETTINGS = {
  languages: [
    { code: "ru", enabled: true, stock_target: 10 },
    { code: "en", enabled: false, stock_target: 3 },
  ],
  run: { batch: 5, interval: "6h", max_duration: "45m" },
  search: { max_queries: 20 },
  filter: { max: 100 },
  download: { workers: 4 },
  delivery: { max_count: 50 },
};

const GENERATOR_SETTINGS = {
  rent_pool_size: 3,
  videos_per_server: 5,
  max_servers: 4,
  offer_refresh_seconds: 60,
  server_ready_timeout: 900,
  comfy_health_timeout: 600,
  comfy_job_timeout: 1800,
  comfy_health_interval: 15,
  max_cost_per_hour: 0.35,
  stale_instance_hours: 6,
  safety_enabled: true,
  safety_frames: 12,
  safety_threshold: 0.5,
  safety_nude_threshold: 0.3,
  safety_frame_attempts: 2,
  model: "gpt-test",
};

const PREVIEW_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

async function mockProfileDetail(page: Page): Promise<void> {
  await page.route(/\/api\/v1\/content\/profiles\/dating\/profile_1$/, (route) => {
    const request = route.request();
    if (request.method() === "PATCH") {
      const patch = request.postDataJSON() as Record<string, unknown>;
      return route.fulfill({ json: { ...PROFILE, ...patch } });
    }
    return route.fulfill({ json: PROFILE_DETAIL });
  });
  await page.route(/\/api\/v1\/content\/profiles\/dating\/profile_1\/prompts$/, (route) =>
    route.fulfill({ json: PROMPTS }),
  );
  await page.route(/\/api\/v1\/content\/profiles\/dating\/profile_1\/preview\.png$/, (route) =>
    route.fulfill({ contentType: "image/png", body: PREVIEW_PNG }),
  );
}

async function mockSettings(page: Page): Promise<void> {
  await page.route(/\/api\/v1\/content\/settings$/, (route) => {
    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON() as typeof RUNTIME_SETTINGS;
      return route.fulfill({
        json: {
          changed: true,
          diff: "- batch 5\n+ batch 6",
          backup: "config.yaml.2026.bak",
          settings: payload,
        },
      });
    }
    return route.fulfill({ json: RUNTIME_SETTINGS });
  });
  await page.route(/\/api\/v1\/content\/settings\/generator$/, (route) =>
    route.fulfill({ json: GENERATOR_SETTINGS }),
  );
  await page.route(/\/api\/v1\/content\/settings\/preview$/, (route) =>
    route.fulfill({ json: { changed: true, diff: "- batch 5\n+ batch 6" } }),
  );
}

test("renders the delivery page without horizontal overflow", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  await mockStock(page);
  await mockProfiles(page);

  await page.goto("/");

  await expect(page).toHaveTitle("Выдача видео — content-factory");
  await expect(page.getByRole("heading", { level: 1, name: "Выдача видео" })).toBeVisible();
  await expect(page.locator(".brand")).toHaveText("content-factory");
  await expect(page.getByRole("tab")).toHaveText(["Выдача", "Запросы", "Профили", "Настройки"]);
  await expect(page.getByRole("tab", { name: "Выдача" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#panel-delivery")).toBeVisible();
  await expect(page.locator("#panel-queries")).toBeHidden();
  await expect(page.locator("#panel-profiles")).toBeHidden();
  await expect(page.locator("#panel-settings")).toBeHidden();
  await expect(page.locator("#general-title")).toBeVisible();
  await expect(page.locator("#general-value")).toHaveText("4 / 10");
  await expect(page.locator("#profiles-stat-title")).toBeVisible();
  await expect(page.locator("#profiles-total")).toHaveText("8");
  await expect(page.locator("#delivery-source")).toBeVisible();
  await expect(page.locator("#delivery-title")).toBeVisible();
  await expect(page.locator("#archives-title")).toBeVisible();
  await expect(page.locator("#archives-empty")).toBeVisible();
  await expect(page.locator(".lead")).toHaveCount(0);
  await expect(page.locator("footer")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Сгенерировать" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Запустить" })).toHaveCount(0);

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  expect(consoleErrors).toEqual([]);
});

test("keeps stock cards compact with many profiles", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-1280", "desktop grid layout regression");

  const profiles = Array.from({ length: 24 }, (_, index) => ({
    ...PROFILE,
    id: `dating/profile_${index + 1}`,
    name: `profile_${index + 1}`,
  }));
  await mockStock(page);
  await mockProfiles(page, profiles);

  await page.goto("/");
  await expect(page.locator("#profiles-stock .mini-row")).toHaveCount(profiles.length);

  const boxes = await page.evaluate(() => {
    const bar = document.querySelector("#general-bar");
    const hint = document.querySelector("#general-hint");
    const general = document.querySelector("#general-title")?.closest(".stat-card");
    const profilesCard = document.querySelector("#profiles-stat-title")?.closest(".stat-card");
    if (
      !bar ||
      !hint ||
      !(general instanceof HTMLElement) ||
      !(profilesCard instanceof HTMLElement)
    ) {
      return null;
    }
    const barBox = bar.getBoundingClientRect();
    const hintBox = hint.getBoundingClientRect();
    return {
      gap: hintBox.top - barBox.bottom,
      generalHeight: general.getBoundingClientRect().height,
      profilesHeight: profilesCard.getBoundingClientRect().height,
    };
  });

  expect(boxes).not.toBeNull();
  expect(boxes?.gap).toBeLessThanOrEqual(48);
  expect(boxes?.generalHeight).toBeLessThan(boxes?.profilesHeight ?? 0);
});

test("serves the built assets with stable names", async ({ page, request }) => {
  const css = await request.get("/ui/app.css");
  expect(css.status()).toBe(200);
  expect(css.headers()["content-type"]).toContain("text/css");

  const js = await request.get("/ui/app.js");
  expect(js.status()).toBe(200);
  expect(js.headers()["content-type"]).toMatch(/javascript/);

  await page.goto("/");
  await expect(page.locator('link[rel="stylesheet"][href="/ui/app.css"]')).toHaveCount(1);
  await expect(page.locator('script[src="/ui/app.js"]')).toHaveCount(1);
});

test("switches tabs by click and keyboard", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("tab", { name: "Запросы" }).click();
  await expect(page.locator("#panel-queries")).toBeVisible();
  await expect(page.locator("#panel-delivery")).toBeHidden();
  await expect(page.getByRole("tab", { name: "Запросы" })).toHaveAttribute("aria-selected", "true");

  await page.getByRole("tab", { name: "Запросы" }).press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Профили" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#panel-profiles")).toBeVisible();
  await expect(page.locator("#profiles-title")).toBeVisible();

  await page.getByRole("tab", { name: "Профили" }).press("End");
  await expect(page.getByRole("tab", { name: "Настройки" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.locator("#panel-settings")).toBeVisible();
  await expect(page.locator("#settings-title")).toBeVisible();

  await page.getByRole("tab", { name: "Настройки" }).press("Home");
  await expect(page.getByRole("tab", { name: "Выдача" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#panel-delivery")).toBeVisible();
});

test("keeps every tab inside the viewport", async ({ page }) => {
  const tabs = [
    {
      name: "Выдача",
      panel: "#panel-delivery",
      controls: ["#general-title", "#delivery-form", "#deliver", "#archives-title"],
    },
    {
      name: "Запросы",
      panel: "#panel-queries",
      controls: ["#queries-title", "#query-form", "#queries-search", "#query-add"],
    },
    {
      name: "Профили",
      panel: "#panel-profiles",
      controls: ["#profiles-title"],
    },
    {
      name: "Настройки",
      panel: "#panel-settings",
      controls: ["#settings-title"],
    },
  ];

  await page.goto("/");

  for (const tab of tabs) {
    await page.getByRole("tab", { name: tab.name }).click();
    await expect(page.locator(tab.panel)).toBeVisible();
    for (const selector of tab.controls) {
      await expect(page.locator(selector), `${tab.name}: ${selector}`).toBeVisible();
    }

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, tab.name).toBeLessThanOrEqual(1);
  }
});

test("switches the delivery source to a profile", async ({ page }) => {
  await mockStock(page);
  await mockProfiles(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Профиль" }).click();

  await expect(page.locator("#profile-field")).toBeVisible();
  await expect(page.locator("#language-field")).toBeHidden();
  await expect(page.locator("#delivery-profile")).toHaveValue("dating/profile_1");
  await expect(page.locator("#count")).toHaveAttribute("max", "8");
  await expect(page.locator("#available")).toHaveText("доступно 8");
});

test("builds a general archive from the delivery form", async ({ page }) => {
  await mockStock(page);
  await mockProfiles(page, []);
  await page.route(/\/api\/v1\/content\/deliveries\/link$/, (route) =>
    route.fulfill({
      json: {
        request_id: "req-1",
        language: "ru",
        status: "ready",
        url: "https://filebin.example/req-1.zip",
        expires_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
      },
    }),
  );
  await page.goto("/");

  await page.locator("#count").fill("2");
  await page.getByRole("button", { name: "Собрать" }).click();

  await expect(page.locator("#delivery-job")).toBeVisible();
  await expect(page.locator("#job-title")).toHaveText("delivery-req-1.zip");
  await expect(page.locator("#job-link")).toHaveAttribute(
    "href",
    "https://filebin.example/req-1.zip",
  );
  await expect(page.locator("#delivery-status")).toContainText("Архив готов");
});

test("keeps long query content and wide tables inside the scroll container", async ({ page }) => {
  const longQuery = `длинный-запрос-${"очень-".repeat(60)}конец`;
  await mockStock(page, {
    ...STOCK,
    languages: [{ ...RU_STOCK, stock: 1, target: 1, titled: 1, untitled: 0 }],
    general: [{ language: "ru", stock: 1, target: 1 }],
  });
  await mockProfiles(page, []);
  await page.route(/\/api\/v1\/content\/queries\?language=ru$/, (route) =>
    route.fulfill({
      json: { queries: [{ query: longQuery, count: 1, in_file: true }], unclassified: 0, total: 1 },
    }),
  );

  await page.goto("/");
  await expect(page.locator("#general-value")).toHaveText("1 / 1");
  await page.getByRole("tab", { name: "Запросы" }).click();

  const item = page.locator("#queries-list .query-item").first();
  await expect(item).toBeVisible();
  await expect(item).toContainText("длинный-запрос");

  const wrap = page.locator(".table-wrap").filter({ has: page.locator("#queries-list") });
  await expect(wrap).toHaveCount(1);

  const probe = await wrap.evaluate((element) => {
    const table = document.createElement("table");
    table.style.width = "1200px";
    const row = table.insertRow();
    const cell = row.insertCell();
    cell.textContent = "широкая таблица";
    element.append(table);
    return {
      overflowX: getComputedStyle(element).overflowX,
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
    };
  });

  expect(probe.overflowX).toBe("auto");
  expect(probe.scrollWidth).toBeGreaterThan(probe.clientWidth);

  const pageOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(pageOverflow).toBeLessThanOrEqual(1);

  const escapes = await item.evaluate((element) => {
    const container = element.closest(".table-wrap");
    if (!container) {
      return Number.POSITIVE_INFINITY;
    }
    const box = element.getBoundingClientRect();
    const frame = container.getBoundingClientRect();
    return box.right - frame.right;
  });
  expect(escapes).toBeLessThanOrEqual(1);
});

test.describe("profiles", () => {
  test("opens a profile with the real pipeline, prompts and preview lightbox", async ({ page }) => {
    await mockStock(page);
    await mockProfiles(page);
    await mockProfileDetail(page);
    await page.goto("/");

    await page.getByRole("tab", { name: "Профили" }).click();
    const row = page.locator(".profile-row");
    await expect(row).toContainText("dating/profile_1");
    await expect(row.locator(".profile-row-badges")).toContainText("Русский");
    await expect(row.locator(".profile-row-badges")).toContainText("dating");
    await expect(row.locator(".profile-title")).toHaveText("Знакомства");
    await expect(row.locator(".profile-summary")).toHaveText("Профиль для знакомств");
    await expect(row).toContainText("8 / 20");
    await expect(row.locator(".profile-preview")).toHaveCount(0);
    await expect(page.locator("#profiles-language-filter")).toBeVisible();
    await expect(page.locator("#profiles-niche-filter")).toBeVisible();
    await expect(page.locator("#profiles-count")).toHaveText("1 из 1");

    await page.getByRole("button", { name: "Открыть профиль dating/profile_1" }).click();

    await expect(page.locator("#profiles-list-view")).toBeHidden();
    await expect(page.locator("#profile-view-title")).toHaveText("dating/profile_1");
    await expect(page.locator("#profile-view-name")).toHaveText("Знакомства");
    await expect(page.locator("#profile-view-summary")).toHaveText("Профиль для знакомств");
    await expect(page.locator("#profile-view-meta")).toHaveText(
      "Русский · dating · 544×960 · видео/фото",
    );
    await expect(page.locator("#profile-tab-video")).toHaveText("Видео · 8");
    await expect(page.locator("#profile-tab-photo")).toHaveText("Фото · 14");

    const box = await page.locator(".profile-preview-box").boundingBox();
    expect(box?.width).toBe(112);
    expect(box?.height).toBe(198);

    await expect(page.locator("#profile-video .pipeline-chip")).toHaveText([
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
    await expect(page.locator("#profile-video .prompt-card-values")).toContainText("возраст: 30");
    await expect(page.locator("#profile-video")).toContainText("Из шаблона");

    await page.locator("#profile-preview").click();
    const lightbox = page.locator("#profile-lightbox");
    await expect(lightbox).toBeVisible();
    await expect(page.locator("#profile-lightbox-image")).toHaveAttribute(
      "src",
      "/api/v1/content/profiles/dating/profile_1/preview.png",
    );

    await page.keyboard.press("Escape");
    await expect(lightbox).toBeHidden();
    await expect(page.locator("#profile-preview")).toBeFocused();

    await page.getByRole("button", { name: "← Назад" }).click();
    await expect(page.locator("#profiles-list-view")).toBeVisible();
    await expect(page.locator("#profile-view")).toBeHidden();
    await expect(
      page.getByRole("button", { name: "Открыть профиль dating/profile_1" }),
    ).toBeFocused();
  });

  test("toggles refill from the list and keeps the row in sync", async ({ page }) => {
    await mockStock(page);
    await mockProfiles(page);
    await mockProfileDetail(page);
    await page.goto("/");

    await page.getByRole("tab", { name: "Профили" }).click();
    await page.getByRole("button", { name: "Включить добор dating/profile_1" }).click();

    await expect(page.getByRole("button", { name: "Пауза добора dating/profile_1" })).toBeVisible();
  });

  test("shows the photo prompt and read-only settings tabs", async ({ page }) => {
    await mockStock(page);
    await mockProfiles(page);
    await mockProfileDetail(page);
    await page.goto("/");

    await page.getByRole("tab", { name: "Профили" }).click();
    await page.getByRole("button", { name: "Открыть профиль dating/profile_1" }).click();

    await page.locator("#profile-tab-photo").click();
    await expect(page.locator("#profile-photo textarea")).toHaveValue("Голос: спокойный");

    await page.locator("#profile-tab-settings").click();
    await expect(page.locator("#profile-settings-video")).toContainText("544");
    await expect(page.locator("#profile-settings-rent")).toContainText("RTX 4090");
    await expect(page.locator("#profile-settings-text")).toContainText("arialbd.ttf");
    await expect(page.locator("#profile-settings-delivery")).toContainText("generated");
  });
});

test.describe("settings", () => {
  test("shows the generator panel and applies a previewed runtime change", async ({ page }) => {
    await mockStock(page);
    await mockProfiles(page);
    await mockSettings(page);
    await page.goto("/");

    await page.getByRole("tab", { name: "Настройки" }).click();

    await expect(page.locator("#settings-generator")).toContainText("Пул аренды");
    await expect(page.locator("#settings-generator")).toContainText("gpt-test");
    await expect(page.locator("#settings-generator")).toContainText("Максимум серверов");
    await expect(page.locator("#settings-languages")).toContainText("Русский");
    await expect(page.locator("#settings-batch")).toHaveValue("5");

    await page.locator("#settings-batch").fill("6");
    await page.getByRole("button", { name: "Проверить" }).click();

    await expect(page.locator("#settings-diff")).toBeVisible();
    await expect(page.locator("#settings-diff-text")).toHaveText("- batch 5\n+ batch 6");
    await expect(page.locator("#settings-apply")).toBeEnabled();

    await page.getByRole("button", { name: "Применить" }).click();

    await expect(page.locator("#settings-status")).toHaveText("Настройки применены.");
    await expect(page.locator("#settings-backup")).toHaveText("Бэкап: config.yaml.2026.bak");
    await expect(page.locator("#settings-apply")).toBeDisabled();
  });
});

test.describe("dark color scheme", () => {
  test.use({ colorScheme: "dark" });

  test("keeps the dark background tokens", async ({ page }) => {
    await page.goto("/");

    const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    expect(background).toBe("rgb(15, 20, 26)");
  });
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("keeps the page usable with animations reduced", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByRole("heading", { level: 1, name: "Выдача видео" })).toBeVisible();
    await page.getByRole("tab", { name: "Настройки" }).click();
    await expect(page.locator("#panel-settings")).toBeVisible();
  });
});
