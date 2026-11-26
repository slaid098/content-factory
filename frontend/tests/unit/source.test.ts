// @vitest-environment node

import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SOURCE = fileURLToPath(new URL("../../src", import.meta.url));
const PAGE = fileURLToPath(new URL("../../index.html", import.meta.url));
const STYLES = text(join(SOURCE, "styles.css"));

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? files(path) : [path];
  });
}

function text(path: string): string {
  return readFileSync(path, "utf8");
}

function sourceFiles(): string[] {
  return files(SOURCE).filter((path) => path.endsWith(".ts"));
}

function relativeName(path: string): string {
  return relative(SOURCE, path).replaceAll("\\", "/");
}

describe("source invariants", () => {
  it("contains only TypeScript and CSS files", () => {
    const names = files(SOURCE)
      .map(relativeName)
      .filter((name) => !name.endsWith(".ts") && !name.endsWith(".css"));

    expect(names).toEqual([]);
  });

  it("does not use HTML injection, eval, any or console output", () => {
    const forbidden = [
      "innerHTML",
      "insertAdjacentHTML",
      "outerHTML",
      "eval(",
      "console.log",
      "@ts-ignore",
      "@ts-expect-error",
    ];

    for (const path of sourceFiles()) {
      const source = text(path);
      for (const needle of forbidden) {
        expect(source, `${relativeName(path)}: ${needle}`).not.toContain(needle);
      }
      expect(source, `${relativeName(path)}: any`).not.toMatch(/\bany\b/);
    }
  });

  it("keeps the profile preview uncropped in a fixed box with a lightbox", () => {
    const css = text(join(SOURCE, "styles.css"));
    const start = css.indexOf("/* Profiles */");
    expect(start).toBeGreaterThan(0);
    const profiles = css.slice(start);

    expect(profiles).not.toContain("object-fit: cover");
    expect(profiles).toContain("object-fit: contain");
    expect(profiles).toMatch(
      /\.profile-preview-box\s*\{[^}]*width:\s*112px;[^}]*height:\s*198px;/s,
    );
    expect(profiles).toMatch(/\.profile-preview\s*\{[^}]*width:\s*100%;[^}]*height:\s*100%;/s);
    expect(profiles).toContain(".profile-lightbox");
    expect(profiles).toContain(".profile-lightbox-image");
  });

  it("imports modules without a .js extension", () => {
    for (const path of sourceFiles()) {
      expect(text(path), relativeName(path)).not.toMatch(/from\s+"[^"]+\.js"/);
    }
  });

  it("keeps fetch only in lib/http.ts", () => {
    const users = sourceFiles()
      .filter((path) => /\bfetch\(/.test(text(path)))
      .map(relativeName);

    expect(users).toEqual(["lib/http.ts"]);
  });

  it("keeps Intl and toLocale only in lib/datetime.ts", () => {
    const users = sourceFiles()
      .filter((path) => /Intl\.|toLocale/.test(text(path)))
      .map(relativeName);

    expect(users).toEqual(["lib/datetime.ts"]);
  });

  it("has no secrets, external URLs or generation buttons", () => {
    const forbidden = [
      "Bearer ",
      "CONTENT_FACTORY_DELIVERY_TOKEN",
      "Authorization",
      "Сгенерировать",
      "Запустить",
    ];
    const pages = [...sourceFiles(), PAGE];

    for (const path of pages) {
      const source = text(path);
      for (const needle of forbidden) {
        expect(source, `${relativeName(path)}: ${needle}`).not.toContain(needle);
      }
      expect(source, `${relativeName(path)}: external URL`).not.toMatch(/https?:\/\//);
    }
  });
});

describe("design system styles", () => {
  it("defines the shared component layer", () => {
    const selectors = [
      ".panel",
      ".button",
      ".button.primary",
      ".button.secondary",
      ".button.ghost",
      ".field",
      ".field-label",
      ".badge",
      ".message",
      ".empty",
      ".table-wrap",
      ".tab",
      ".stat-card",
      ".segmented-option",
      ".archive-table",
      ".query-table",
    ];

    expect(STYLES).toContain("@layer components");
    for (const selector of selectors) {
      expect(STYLES, selector).toContain(selector);
    }
  });

  it("is mobile first with a 44px control height", () => {
    expect(STYLES).toContain("--control-height: 44px");
    expect(STYLES).toContain("@media (min-width: 30rem)");
    expect(STYLES).toContain("@media (min-width: 48rem)");
    expect(STYLES).not.toMatch(/@media\s*\(max-width/);
  });

  it("keeps colors, radii and shadows in tokens", () => {
    expect(STYLES).not.toMatch(
      /(?:^|\n)\s*(?:background|background-color|color|border-color|outline-color)\s*:\s*#[0-9a-f]/i,
    );
    expect(STYLES).not.toMatch(/border-radius:\s*\d+px/);
    const shadows = STYLES.split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("box-shadow:"));
    for (const shadow of shadows) {
      expect(shadow).toMatch(/^box-shadow: var\(--shadow-card\);$/);
    }
  });

  it("keeps long content and motion accessible", () => {
    expect(STYLES).toMatch(/\.table-wrap\s*\{[^}]*overflow-x:\s*auto/s);
    expect(STYLES).toContain("@media (prefers-reduced-motion: reduce)");
    expect(STYLES).not.toContain("@font-face");
    expect(STYLES).not.toContain("http://");
    expect(STYLES).not.toContain("https://");
  });
});
