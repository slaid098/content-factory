import { beforeEach, describe, expect, it } from "vitest";

import { node, resetPage } from "./helpers";

const TABS = ["delivery", "queries", "profiles", "settings"];
const MESSAGES = ["stock", "archives", "queries", "settings", "profiles", "profiles-stock"];
const EMPTY = ["archives", "queries", "profiles"];

beforeEach(() => {
  resetPage();
});

describe("design system markup", () => {
  it("uses one panel and one tab component in the shell", () => {
    for (const name of TABS) {
      const panel = node(`panel-${name}`);
      expect(panel.classList.contains("tab-panel")).toBe(true);
      expect(panel.querySelector(".panel"), name).not.toBeNull();
    }

    const tabs = Array.from(document.querySelectorAll<HTMLElement>('[role="tab"]'));
    expect(tabs.map((tab) => tab.id)).toEqual([
      "tab-delivery",
      "tab-queries",
      "tab-profiles",
      "tab-settings",
    ]);
    for (const tab of tabs) {
      expect(tab.classList.contains("tab")).toBe(true);
      expect(tab.getAttribute("aria-controls")).toMatch(/^panel-/);
      expect(tab.getAttribute("aria-selected")).toMatch(/^(true|false)$/);
    }
    expect(document.querySelector('nav[role="tablist"]')?.classList.contains("tabs")).toBe(true);
  });

  it("gives every action button a shared class and one variant", () => {
    const buttons = Array.from(document.querySelectorAll<HTMLElement>("button:not([role='tab'])"));

    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      if (button.classList.contains("segmented-option")) {
        expect(button.classList.contains("button"), button.id).toBe(false);
        continue;
      }
      expect(button.classList.contains("button"), button.id).toBe(true);
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

  it("wraps long tables in a scroll container", () => {
    for (const id of ["queries-list", "archives"]) {
      const wrap = node(id).closest(".table-wrap");
      expect(wrap, id).not.toBeNull();
    }
  });

  it("keeps the delivery source segmented and the table headers explicit", () => {
    const source = node("delivery-source");
    expect(source.getAttribute("role")).toBe("group");
    expect(source.querySelectorAll(".segmented-option")).toHaveLength(2);

    for (const id of ["queries-list", "archives"]) {
      const table = node(id).closest("table");
      expect(table, id).not.toBeNull();
      expect(table?.querySelector("thead th"), id).not.toBeNull();
    }
  });

  it("keeps the document ids unique", () => {
    const ids = Array.from(document.querySelectorAll<HTMLElement>("[id]"), (item) => item.id);

    expect(new Set(ids).size).toBe(ids.length);
  });
});
