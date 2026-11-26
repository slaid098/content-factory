import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it } from "vitest";

import { node, resetPage } from "./helpers";

const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
const TABS = ["delivery", "queries", "settings", "profiles"];
const MESSAGES = ["stock", "archives", "queries", "settings", "profiles"];
const EMPTY = ["stock", "archives", "queries", "profiles"];

beforeEach(() => {
  resetPage();
});

describe("design system markup", () => {
  it("uses one panel and one tab component in the shell", () => {
    for (const name of TABS) {
      const panel = node(`panel-${name}`);
      expect(panel.classList.contains("tab-panel")).toBe(true);
      expect(panel.querySelector(":scope > .panel")).not.toBeNull();
    }

    const tabs = Array.from(document.querySelectorAll<HTMLElement>('[role="tab"]'));
    expect(tabs).toHaveLength(TABS.length);
    for (const tab of tabs) {
      expect(tab.classList.contains("tab")).toBe(true);
      expect(tab.getAttribute("aria-controls")).toMatch(/^panel-/);
      expect(tab.getAttribute("aria-selected")).toMatch(/^(true|false)$/);
    }
    expect(document.querySelector('nav[role="tablist"]')?.classList.contains("tabs")).toBe(true);
  });

  it("gives every button a shared class and one variant", () => {
    const buttons = Array.from(document.querySelectorAll<HTMLElement>("button:not([role='tab'])"));

    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      expect(button.classList.contains("button")).toBe(true);
      const variants = ["primary", "secondary", "ghost"].filter((variant) =>
        button.classList.contains(variant),
      );
      expect(variants, button.id).toHaveLength(1);
    }

    for (const link of Array.from(document.querySelectorAll<HTMLElement>("a.button"))) {
      expect(link.classList.contains("primary") || link.classList.contains("secondary")).toBe(true);
    }
  });

  it("labels every static control and puts the hint under the field", () => {
    const controls = Array.from(
      document.querySelectorAll<HTMLElement>("input:not([type='hidden']), select, textarea"),
    );

    expect(controls.length).toBeGreaterThan(0);
    for (const control of controls) {
      const field = control.closest(".field");
      expect(field, control.id).not.toBeNull();
      expect(field?.querySelector(".field-label"), control.id).not.toBeNull();
      expect(control.closest("label")).toBeNull();
    }
  });

  it("renders messages and empty states with the shared components", () => {
    for (const name of MESSAGES) {
      const message = node(`${name}-message`);
      expect(message.classList.contains("message")).toBe(true);
      expect(message.classList.contains("is-error")).toBe(true);
      expect(message.getAttribute("role")).toBe("status");
      expect(message.getAttribute("aria-live")).toBe("polite");
    }

    for (const name of EMPTY) {
      expect(node(`${name}-empty`).classList.contains("empty")).toBe(true);
    }
    expect(node("job-state").classList.contains("badge")).toBe(true);
  });

  it("wraps long lists in a scroll container", () => {
    for (const id of ["queries-list", "archives"]) {
      const wrap = node(id).closest(".table-wrap");
      expect(wrap, id).not.toBeNull();
    }
  });

  it("keeps the document ids unique", () => {
    const ids = Array.from(document.querySelectorAll<HTMLElement>("[id]"), (item) => item.id);

    expect(new Set(ids).size).toBe(ids.length);
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
    ];

    expect(styles).toContain("@layer components");
    for (const selector of selectors) {
      expect(styles, selector).toContain(selector);
    }
  });

  it("is mobile first with a 44px control height", () => {
    expect(styles).toContain("--control-height: 44px");
    expect(styles).toContain("@media (min-width: 30rem)");
    expect(styles).toContain("@media (min-width: 48rem)");
    expect(styles).not.toMatch(/@media\s*\(max-width/);
  });

  it("keeps colors, radii and shadows in tokens", () => {
    expect(styles).not.toMatch(
      /(?:^|\n)\s*(?:background|background-color|color|border-color|outline-color)\s*:\s*#[0-9a-f]/i,
    );
    expect(styles).not.toMatch(/border-radius:\s*\d+px/);
    const shadows = styles
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("box-shadow:"));
    for (const shadow of shadows) {
      expect(shadow).toMatch(/^box-shadow: var\(--shadow-card\);$/);
    }
  });

  it("keeps long content and motion accessible", () => {
    expect(styles).toMatch(/\.table-wrap\s*\{[^}]*overflow-x:\s*auto/s);
    expect(styles).toContain("@media (prefers-reduced-motion: reduce)");
    expect(styles).not.toContain("@font-face");
    expect(styles).not.toContain("http://");
    expect(styles).not.toContain("https://");
  });
});
