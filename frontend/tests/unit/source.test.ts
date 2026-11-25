// @vitest-environment node

import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SOURCE = fileURLToPath(new URL("../../src", import.meta.url));
const PAGE = fileURLToPath(new URL("../../index.html", import.meta.url));

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

  it("keeps profile previews compact and uncropped", () => {
    const css = text(join(SOURCE, "styles.css"));
    const start = css.indexOf("/* Profiles */");
    expect(start).toBeGreaterThan(0);
    const profiles = css.slice(start);

    expect(profiles).not.toContain("object-fit: cover");
    expect(profiles).not.toMatch(/height:\s*150px/);
    expect(profiles).toContain("object-fit: contain");
    expect(profiles).toContain("max-height: 80px");
    expect(profiles).toContain("max-height: 96px");
    expect(profiles).toContain("max-width: 96px");
    expect(profiles).toContain("max-width: 128px");
    expect(profiles).toContain("grid-template-columns: repeat(2, minmax(0, 1fr))");
    expect(profiles).not.toContain("minmax(280px");
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
