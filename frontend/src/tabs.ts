/** Accessible tabs: one visible panel at a time, arrow-key navigation. */

export interface TabsOptions {
  tabs: HTMLElement[];
  panels: HTMLElement[];
  onChange: (panelId: string) => void;
}

export interface TabsView {
  show: (panelId: string) => void;
}

/**
 * Wire the tab buttons and panels of the page.
 */
export function createTabs(options: TabsOptions): TabsView {
  const { tabs, panels, onChange } = options;

  function activate(index: number, focus: boolean): void {
    const tab = tabs[index];
    if (!tab) {
      return;
    }
    const panelId = tab.getAttribute("aria-controls");
    for (const [position, item] of tabs.entries()) {
      const selected = position === index;
      item.classList.toggle("is-active", selected);
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
    }
    for (const panel of panels) {
      panel.hidden = panel.id !== panelId;
    }
    if (focus) {
      tab.focus();
    }
    if (panelId) {
      onChange(panelId);
    }
  }

  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => {
      activate(index, false);
    });
    tab.addEventListener("keydown", (event) => {
      const last = tabs.length - 1;
      let next = index;
      if (event.key === "ArrowRight") {
        next = index === last ? 0 : index + 1;
      } else if (event.key === "ArrowLeft") {
        next = index === 0 ? last : index - 1;
      } else if (event.key === "Home") {
        next = 0;
      } else if (event.key === "End") {
        next = last;
      } else {
        return;
      }
      event.preventDefault();
      activate(next, true);
    });
  }

  function show(panelId: string): void {
    const index = tabs.findIndex((tab) => tab.getAttribute("aria-controls") === panelId);
    if (index >= 0) {
      activate(index, false);
    }
  }

  return { show };
}
