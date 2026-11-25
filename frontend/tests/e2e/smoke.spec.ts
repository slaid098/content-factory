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
