import { expect, test } from "@playwright/test";

test("renders the delivery page without horizontal overflow", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  await page.goto("/");

  await expect(page).toHaveTitle("Выдача видео — content-factory");
  await expect(page.getByRole("heading", { level: 1, name: "Выдача видео" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Выдача" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#panel-delivery")).toBeVisible();
  await expect(page.locator("#panel-queries")).toBeHidden();
  await expect(page.locator("#stock-title")).toBeVisible();
  await expect(page.locator("#delivery-title")).toBeVisible();
  await expect(page.locator("#archives-title")).toBeVisible();
  await expect(page.locator("#archives-empty")).toBeVisible();
  await expect(page.getByRole("button", { name: "Сгенерировать" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Запустить" })).toHaveCount(0);

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  expect(consoleErrors).toEqual([]);
});

test("renders compact profile cards with an uncropped preview and a dialog", async ({ page }) => {
  const preview = `<svg xmlns="http://www.w3.org/2000/svg" width="544" height="960" viewBox="0 0 544 960"><rect width="544" height="960" fill="#1a63d8"/></svg>`;
  const profile = {
    id: "dating/profile_1",
    category: "dating",
    name: "profile_1",
    language: "ru",
    niche: "dating",
    width: 544,
    height: 960,
    audio: true,
    stock: 8,
    photo_stock: 14,
    target: 20,
    refill_at: 5,
    refill_enabled: false,
    target_override: null,
    refill_at_override: null,
    preview_url: "/api/v1/content/profiles/dating/profile_1/preview.png",
    last_run: null,
  };

  await page.route(/\/api\/v1\/content\/profiles$/, (route) =>
    route.fulfill({ json: { profiles: [profile] } }),
  );
  await page.route(/\/api\/v1\/content\/profiles\/dating\/profile_1\/preview\.png$/, (route) =>
    route.fulfill({ contentType: "image/svg+xml", body: preview }),
  );

  await page.goto("/");
  await page.getByRole("tab", { name: "Профили" }).click();

  const card = page.locator(".profile-card").first();
  await expect(card).toBeVisible();
  await expect(card).toContainText("Shorts 8 / 20");
  await expect(card).toContainText("Посты 14");
  await expect(card).toContainText("только учёт, вне добора");

  const image = card.locator(".profile-preview");
  await expect
    .poll(() => image.evaluate((node) => (node as HTMLImageElement).naturalWidth))
    .toBe(544);
  const metrics = await image.evaluate((node) => {
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return { objectFit: style.objectFit, height: rect.height };
  });
  const viewport = page.viewportSize();
  const maxHeight = (viewport?.width ?? 0) >= 768 ? 96 : 80;
  expect(metrics.objectFit).toBe("contain");
  expect(metrics.height).toBeGreaterThan(0);
  expect(metrics.height).toBeLessThanOrEqual(maxHeight + 0.5);

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);

  const button = card.getByRole("button", { name: "Открыть превью профиля dating/profile_1" });
  await button.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("dating/profile_1 · 544×960");

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(button).toBeFocused();
});

test("keeps the preview slot stable when the thumbnail fails", async ({ page }) => {
  const profile = {
    id: "dating/profile_1",
    category: "dating",
    name: "profile_1",
    language: "ru",
    niche: "dating",
    width: 544,
    height: 960,
    audio: true,
    stock: 8,
    photo_stock: 14,
    target: 20,
    refill_at: 5,
    refill_enabled: false,
    target_override: null,
    refill_at_override: null,
    preview_url: "/api/v1/content/profiles/dating/profile_1/preview.png",
    last_run: null,
  };
  const previewGate: { release: () => void } = { release: () => undefined };
  const gate = new Promise<void>((resolve) => {
    previewGate.release = () => resolve();
  });

  await page.route(/\/api\/v1\/content\/profiles$/, (route) =>
    route.fulfill({ json: { profiles: [profile] } }),
  );
  await page.route(
    /\/api\/v1\/content\/profiles\/dating\/profile_1\/preview\.png$/,
    async (route) => {
      await gate;
      await route.fulfill({ status: 404, body: "not found" });
    },
  );

  await page.goto("/");
  await page.getByRole("tab", { name: "Профили" }).click();

  const card = page.locator(".profile-card").first();
  const media = card.locator(".profile-media");
  const image = card.locator(".profile-preview");
  const fallback = card.locator(".profile-preview-fallback");
  await expect(media).toBeVisible();
  const cardBefore = await card.boundingBox();
  const mediaBefore = await media.boundingBox();
  expect(cardBefore).not.toBeNull();
  expect(mediaBefore).not.toBeNull();

  previewGate.release();
  await expect(fallback).toBeVisible();
  await expect(fallback).toHaveText("Нет превью");
  await expect(image).toBeHidden();

  const cardAfter = await card.boundingBox();
  const mediaAfter = await media.boundingBox();
  const fallbackAfter = await fallback.boundingBox();
  expect(cardAfter).not.toBeNull();
  expect(mediaAfter).not.toBeNull();
  expect(fallbackAfter).not.toBeNull();
  if (!cardBefore || !mediaBefore || !cardAfter || !mediaAfter || !fallbackAfter) {
    throw new Error("preview slot has no box");
  }

  expect(mediaAfter.width).toBeCloseTo(mediaBefore.width, 1);
  expect(mediaAfter.height).toBeCloseTo(mediaBefore.height, 1);
  expect(cardAfter.width).toBeCloseTo(cardBefore.width, 1);
  expect(cardAfter.height).toBeCloseTo(cardBefore.height, 1);
  expect(fallbackAfter.width).toBeLessThanOrEqual(mediaAfter.width + 0.5);
  expect(fallbackAfter.height).toBeLessThanOrEqual(mediaAfter.height + 0.5);
  expect(mediaAfter.width / mediaAfter.height).toBeCloseTo(544 / 960, 2);
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
  await expect(page.getByRole("tab", { name: "Настройки" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.locator("#panel-settings")).toBeVisible();

  await page.getByRole("tab", { name: "Настройки" }).press("End");
  await expect(page.getByRole("tab", { name: "Профили" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#panel-profiles")).toBeVisible();
  await expect(page.locator("#profiles-empty")).toBeVisible();

  await page.getByRole("tab", { name: "Профили" }).press("Home");
  await expect(page.getByRole("tab", { name: "Выдача" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#panel-delivery")).toBeVisible();
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
    await page.getByRole("tab", { name: "Профили" }).click();
    await expect(page.locator("#panel-profiles")).toBeVisible();
  });
});
