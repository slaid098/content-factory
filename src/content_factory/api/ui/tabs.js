/** Accessible tabs: one visible panel at a time, arrow-key navigation. */

/**
 * Wire the tab buttons and panels of the page.
 * @param {{tabs: HTMLElement[], panels: HTMLElement[], onChange: (panelId: string) => void}} options
 */
export function createTabs(options) {
  const { tabs, panels, onChange } = options;

  /**
   * @param {number} index
   * @param {boolean} focus
   */
  function activate(index, focus) {
    const tab = tabs[index];
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

  /** @param {string} panelId */
  function show(panelId) {
    const index = tabs.findIndex((tab) => tab.getAttribute("aria-controls") === panelId);
    if (index >= 0) {
      activate(index, false);
    }
  }

  return { show };
}
