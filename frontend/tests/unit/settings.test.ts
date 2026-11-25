import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSettingsView } from "../../src/settings";
import { flush, jsonResponse, mockFetch, node, resetPage } from "./helpers";

function settings(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    languages: [
      { code: "ru", enabled: true, stock_target: 10 },
      { code: "en", enabled: false, stock_target: 3 },
    ],
    run: { batch: 5, interval: "6h", max_duration: "45m" },
    search: { max_queries: 20 },
    filter: { max: 100 },
    download: { workers: 4 },
    delivery: { max_count: 50 },
    ...overrides,
  };
}

function createView(onLimits = vi.fn<(value: Record<string, unknown>) => void>()) {
  const view = createSettingsView(
    {
      form: node<HTMLFormElement>("settings-form"),
      languages: node("settings-languages"),
      batch: node<HTMLInputElement>("settings-batch"),
      interval: node<HTMLInputElement>("settings-interval"),
      maxDuration: node<HTMLInputElement>("settings-max-duration"),
      maxQueries: node<HTMLInputElement>("settings-max-queries"),
      filterMax: node<HTMLInputElement>("settings-filter-max"),
      workers: node<HTMLInputElement>("settings-workers"),
      deliveryMax: node<HTMLInputElement>("settings-delivery-max"),
      preview: node<HTMLButtonElement>("settings-preview"),
      apply: node<HTMLButtonElement>("settings-apply"),
      status: node("settings-status"),
      diff: node("settings-diff"),
      diffText: node("settings-diff-text"),
      backup: node("settings-backup"),
      message: node("settings-message"),
      refresh: node<HTMLButtonElement>("settings-refresh"),
    },
    { onLimits },
  );
  return { view, onLimits };
}

function preview(): void {
  node<HTMLButtonElement>("settings-preview").click();
}

function submit(): void {
  node<HTMLFormElement>("settings-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  );
}

function changeInput(): void {
  node<HTMLFormElement>("settings-form").dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  resetPage();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createSettingsView", () => {
  it("loads settings into the form and reports the limits", async () => {
    mockFetch(() => jsonResponse(settings()));
    const { view, onLimits } = createView();

    const loaded = await view.load();

    expect(loaded).toEqual(settings());
    expect(onLimits).toHaveBeenCalledWith(settings());
    expect(node("settings-languages").children.length).toBe(2);
    expect(node("settings-languages").textContent).toContain("Русский");
    expect(node<HTMLInputElement>("settings-batch").value).toBe("5");
    expect(node<HTMLInputElement>("settings-interval").value).toBe("6h");
    expect(node<HTMLInputElement>("settings-max-duration").value).toBe("45m");
    expect(node<HTMLInputElement>("settings-max-queries").value).toBe("20");
    expect(node<HTMLInputElement>("settings-filter-max").value).toBe("100");
    expect(node<HTMLInputElement>("settings-workers").value).toBe("4");
    expect(node<HTMLInputElement>("settings-delivery-max").value).toBe("50");
    expect(node<HTMLButtonElement>("settings-refresh").disabled).toBe(false);
  });

  it("loads once and only reloads when forced", async () => {
    const fetched = mockFetch(() => jsonResponse(settings()));
    const { view } = createView();

    await view.load();
    await view.load();
    expect(fetched.mock).toHaveBeenCalledTimes(1);

    await view.load(true);
    expect(fetched.mock).toHaveBeenCalledTimes(2);
  });

  it("shows a read error and returns null", async () => {
    mockFetch(() => jsonResponse({ detail: "нет файла" }, 500));
    const { view } = createView();

    const result = await view.load();

    expect(result).toBeNull();
    expect(node("settings-message").textContent).toBe("нет файла");
    expect(node("settings-status").textContent).toBe("Настройки недоступны.");
    expect(node("settings-status").classList.contains("is-error")).toBe(true);
  });

  it("previews changes and enables apply", async () => {
    mockFetch((input) => {
      if (String(input).endsWith("/preview")) {
        return jsonResponse({ changed: true, diff: "- old\n+ new" });
      }
      return jsonResponse(settings());
    });
    const { view } = createView();
    await view.load();

    preview();
    await flush();

    expect(node("settings-diff").hidden).toBe(false);
    expect(node("settings-diff-text").textContent).toBe("- old\n+ new");
    expect(node("settings-status").textContent).toBe(
      "Есть изменения. Проверьте diff и нажмите «Применить».",
    );
    expect(node<HTMLButtonElement>("settings-apply").disabled).toBe(false);
  });

  it("reports no changes without a diff", async () => {
    mockFetch((input) =>
      String(input).endsWith("/preview")
        ? jsonResponse({ changed: false })
        : jsonResponse(settings()),
    );
    const { view } = createView();
    await view.load();

    preview();
    await flush();

    expect(node("settings-status").textContent).toBe("Изменений нет.");
    expect(node("settings-diff").hidden).toBe(true);
    expect(node<HTMLButtonElement>("settings-apply").disabled).toBe(true);
  });

  it("rejects preview when a field is invalid", async () => {
    const fetched = mockFetch(() => jsonResponse(settings()));
    const { view } = createView();
    await view.load();
    node<HTMLInputElement>("settings-interval").value = " ";

    preview();
    await flush();

    expect(node("settings-status").textContent).toBe("Поле «run.interval» не может быть пустым.");
    expect(node("settings-status").classList.contains("is-error")).toBe(true);
    expect(fetched.calls().filter((call) => call.url.endsWith("/preview"))).toHaveLength(0);
  });

  it("invalidates a preview when the form changes during the request", async () => {
    const pending = deferred<Response>();
    mockFetch((input) =>
      String(input).endsWith("/preview") ? pending.promise : jsonResponse(settings()),
    );
    const { view } = createView();
    await view.load();

    preview();
    node<HTMLInputElement>("settings-batch").value = "7";
    changeInput();
    pending.resolve(jsonResponse({ changed: true, diff: "- old\n+ new" }));
    await flush();

    expect(node("settings-status").textContent).toBe(
      "Настройки изменены. Нажмите «Проверить» заново.",
    );
    expect(node("settings-diff").hidden).toBe(true);
    expect(node<HTMLButtonElement>("settings-apply").disabled).toBe(true);
  });

  it("applies a previewed change, shows the diff and the backup", async () => {
    const updated = settings({ run: { batch: 6, interval: "6h", max_duration: "45m" } });
    mockFetch((input, init) => {
      if (String(input).endsWith("/preview")) {
        return jsonResponse({ changed: true, diff: "- batch 5\n+ batch 6" });
      }
      if (init?.method === "PUT") {
        return jsonResponse({
          changed: true,
          diff: "- batch 5\n+ batch 6",
          backup: "config.yaml.2026.bak",
          settings: updated,
        });
      }
      return jsonResponse(settings());
    });
    const { view, onLimits } = createView();
    await view.load();
    node<HTMLInputElement>("settings-batch").value = "6";
    preview();
    await flush();

    submit();
    await flush();

    expect(node("settings-status").textContent).toBe("Настройки применены.");
    expect(node("settings-diff-text").textContent).toBe("- batch 5\n+ batch 6");
    expect(node("settings-backup").hidden).toBe(false);
    expect(node("settings-backup").textContent).toBe("Бэкап: config.yaml.2026.bak");
    expect(node<HTMLButtonElement>("settings-apply").disabled).toBe(true);
    expect(onLimits).toHaveBeenCalledTimes(2);
    expect(onLimits).toHaveBeenLastCalledWith(updated);
  });

  it("applies an unchanged file without a diff", async () => {
    mockFetch((input, init) => {
      if (String(input).endsWith("/preview")) {
        return jsonResponse({ changed: true, diff: "same" });
      }
      if (init?.method === "PUT") {
        return jsonResponse({ changed: false, settings: settings() });
      }
      return jsonResponse(settings());
    });
    const { view } = createView();
    await view.load();
    preview();
    await flush();

    submit();
    await flush();

    expect(node("settings-status").textContent).toBe("Изменений нет. Файл не изменён.");
    expect(node("settings-backup").hidden).toBe(true);
  });

  it("refuses to apply without a successful preview", async () => {
    mockFetch(() => jsonResponse(settings()));
    const { view } = createView();
    await view.load();

    submit();
    await flush();

    expect(node("settings-status").textContent).toBe("Сначала нажмите «Проверить».");
    expect(node("settings-status").classList.contains("is-error")).toBe(true);
  });

  it("refuses to apply a form changed after the preview", async () => {
    mockFetch((input) =>
      String(input).endsWith("/preview")
        ? jsonResponse({ changed: true, diff: "d" })
        : jsonResponse(settings()),
    );
    const { view } = createView();
    await view.load();
    preview();
    await flush();

    node<HTMLInputElement>("settings-batch").value = "9";
    submit();
    await flush();

    expect(node("settings-status").textContent).toBe(
      "Настройки изменены. Нажмите «Проверить» заново.",
    );
    expect(node<HTMLButtonElement>("settings-apply").disabled).toBe(true);
  });

  it("resets a preview on any form input", async () => {
    mockFetch((input) =>
      String(input).endsWith("/preview")
        ? jsonResponse({ changed: true, diff: "d" })
        : jsonResponse(settings()),
    );
    const { view } = createView();
    await view.load();
    preview();
    await flush();
    expect(node<HTMLButtonElement>("settings-apply").disabled).toBe(false);

    changeInput();

    expect(node<HTMLButtonElement>("settings-apply").disabled).toBe(true);
    expect(node("settings-status").textContent).toBe(
      "Настройки изменены. Нажмите «Проверить» заново.",
    );
  });

  it("shows a preview request error", async () => {
    mockFetch((input) => {
      if (String(input).endsWith("/preview")) {
        return jsonResponse({ detail: "bad" }, 422);
      }
      return jsonResponse(settings());
    });
    const { view } = createView();
    await view.load();

    preview();
    await flush();

    expect(node("settings-status").textContent).toBe("Проверка не удалась.");
    expect(node("settings-message").textContent).toBe("bad");
  });

  it("shows an apply request error", async () => {
    mockFetch((input, init) => {
      if (String(input).endsWith("/preview")) {
        return jsonResponse({ changed: true, diff: "d" });
      }
      if (init?.method === "PUT") {
        return jsonResponse({ detail: "locked" }, 500);
      }
      return jsonResponse(settings());
    });
    const { view } = createView();
    await view.load();
    preview();
    await flush();

    submit();
    await flush();

    expect(node("settings-status").textContent).toBe("Применить не удалось.");
    expect(node("settings-message").textContent).toBe("locked");
  });

  it("rejects an invalid payload while applying", async () => {
    mockFetch((input) =>
      String(input).endsWith("/preview")
        ? jsonResponse({ changed: true, diff: "d" })
        : jsonResponse(settings()),
    );
    const { view } = createView();
    await view.load();
    preview();
    await flush();

    node<HTMLInputElement>("settings-batch").value = "0";
    submit();
    await flush();

    expect(node("settings-status").textContent).toBe("Поле «run.batch» должно быть от 1 до 1000.");
    expect(node("settings-diff").hidden).toBe(true);
  });

  it("reloads from the refresh button", async () => {
    const fetched = mockFetch(() => jsonResponse(settings()));
    const { view } = createView();
    await view.load();

    node<HTMLButtonElement>("settings-refresh").click();
    await flush();

    expect(fetched.mock).toHaveBeenCalledTimes(2);
  });

  it("disables the controls while a request is in flight", async () => {
    const pending = deferred<Response>();
    mockFetch((input) =>
      String(input).endsWith("/preview") ? pending.promise : jsonResponse(settings()),
    );
    const { view } = createView();
    await view.load();

    preview();

    expect(node<HTMLButtonElement>("settings-preview").disabled).toBe(true);
    expect(node<HTMLButtonElement>("settings-refresh").disabled).toBe(true);
    expect(node("settings-form").getAttribute("aria-busy")).toBe("true");

    pending.resolve(jsonResponse({ changed: false }));
    await flush();

    expect(node<HTMLButtonElement>("settings-preview").disabled).toBe(false);
    expect(node("settings-form").getAttribute("aria-busy")).toBe("false");
  });
});

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
