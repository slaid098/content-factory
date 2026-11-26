import { describe, expect, it, vi } from "vitest";

import { createTabs } from "../../src/tabs";

const ORDER = ["delivery", "queries", "profiles", "settings"];

function fixture(): {
  tabs: HTMLElement[];
  panels: HTMLElement[];
  onChange: ReturnType<typeof vi.fn<(panelId: string) => void>>;
} {
  const tabs = ORDER.map((name) => {
    const tab = document.createElement("button");
    tab.id = `tab-${name}`;
    tab.setAttribute("aria-controls", `panel-${name}`);
    tab.setAttribute("aria-selected", "false");
    return tab;
  });
  const panels = ORDER.map((name) => {
    const panel = document.createElement("section");
    panel.id = `panel-${name}`;
    panel.hidden = name !== "delivery";
    return panel;
  });
  document.body.replaceChildren(...tabs, ...panels);
  const onChange = vi.fn<(panelId: string) => void>();
  return { tabs, panels, onChange };
}

describe("createTabs", () => {
  it("activates a tab on click and reports the panel", () => {
    const { tabs, panels, onChange } = fixture();
    createTabs({ tabs, panels, onChange });

    tabs[1]?.click();

    expect(tabs[1]?.classList.contains("is-active")).toBe(true);
    expect(tabs[1]?.getAttribute("aria-selected")).toBe("true");
    expect(tabs[1]?.tabIndex).toBe(0);
    expect(tabs[0]?.tabIndex).toBe(-1);
    expect(panels[1]?.hidden).toBe(false);
    expect(panels[0]?.hidden).toBe(true);
    expect(onChange).toHaveBeenCalledWith("panel-queries");
  });

  it("moves with arrows from edge to edge and focuses the tab", () => {
    const { tabs, panels, onChange } = fixture();
    createTabs({ tabs, panels, onChange });

    tabs[0]?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    expect(tabs[3]?.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs[3]);

    tabs[3]?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(tabs[0]?.getAttribute("aria-selected")).toBe("true");

    tabs[0]?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(tabs[1]?.getAttribute("aria-selected")).toBe("true");
    expect(panels[1]?.hidden).toBe(false);
  });

  it("supports Home and End and ignores other keys", () => {
    const { tabs, panels, onChange } = fixture();
    createTabs({ tabs, panels, onChange });

    tabs[1]?.dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true }));
    expect(tabs[3]?.getAttribute("aria-selected")).toBe("true");

    tabs[3]?.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
    expect(tabs[0]?.getAttribute("aria-selected")).toBe("true");

    onChange.mockClear();
    tabs[0]?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows a panel by id and ignores an unknown id", () => {
    const { tabs, panels, onChange } = fixture();
    const view = createTabs({ tabs, panels, onChange });

    view.show("panel-settings");
    expect(panels[3]?.hidden).toBe(false);
    expect(onChange).toHaveBeenCalledWith("panel-settings");

    view.show("panel-missing");
    expect(panels[3]?.hidden).toBe(false);
  });
});
