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

function generator(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    rent_pool_size: 3,
    videos_per_server: 5,
    max_servers: 4,
    offer_refresh_seconds: 60,
    server_ready_timeout: 900,
    comfy_health_timeout: 600,
    comfy_job_timeout: 1800,
    comfy_health_interval: 15,
    max_cost_per_hour: 0.35,
    stale_instance_hours: 6,
    safety_enabled: true,
    safety_frames: 12,
    safety_threshold: 0.5,
    safety_nude_threshold: 0.3,
    safety_frame_attempts: 2,
    model: "gpt-test",
    ...overrides,
  };
}

function createView(onLimits = vi.fn<(value: Record<string, unknown>) => void>()) {
  const view = createSettingsView({ onLimits });
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
  it("loads the runtime settings, the generator panel and reports the limits", async () => {
    const fetched = mockFetch((input) => {
      if (String(input).endsWith("/settings/generator")) {
        return jsonResponse(generator());
      }
      return jsonResponse(settings());
    });
    const { view, onLimits } = createView();

    const loaded = await view.load();

    expect(loaded).toEqual(settings());
    expect(onLimits).toHaveBeenCalledWith(settings());
    expect(fetched.calls().map((call) => call.url)).toEqual([
      "/api/v1/content/settings",
      "/api/v1/content/settings/generator",
    ]);
    expect(node("settings-generator").textContent).toContain("Пул аренды");
    expect(node("settings-generator").textContent).toContain("3");
    expect(node("settings-generator").textContent).toContain("Максимум серверов");
    expect(node("settings-generator").textContent).toContain("4");
    expect(node("settings-generator").textContent).toContain("Модель");
    expect(node("settings-generator").textContent).toContain("gpt-test");
    expect(node("settings-generator").textContent).toContain("да");
  });

  it("fills the editable runtime form", async () => {
    mockFetch((input) =>
      String(input).endsWith("/settings/generator")
        ? jsonResponse(generator())
        : jsonResponse(settings()),
    );
    const { view } = createView();

    await view.load();

    expect(node("settings-languages").children.length).toBe(2);
    expect(node("settings-languages").textContent).toContain("Русский");
    expect(node<HTMLInputElement>("settings-batch").value).toBe("5");
    expect(node<HTMLInputElement>("settings-interval").value).toBe("6h");
    expect(node<HTMLInputElement>("settings-max-duration").value).toBe("45m");
    expect(node<HTMLInputElement>("settings-max-queries").value).toBe("20");
    expect(node<HTMLInputElement>("settings-filter-max").value).toBe("100");
    expect(node<HTMLInputElement>("settings-workers").value).toBe("4");
    expect(node<HTMLInputElement>("settings-delivery-max").value).toBe("50");
  });

  it("loads once and only reloads when forced", async () => {
    const fetched = mockFetch((input) =>
      String(input).endsWith("/settings/generator")
        ? jsonResponse(generator())
        : jsonResponse(settings()),
    );
    const { view } = createView();

    await view.load();
    await view.load();
    expect(fetched.mock).toHaveBeenCalledTimes(2);

    await view.load(true);
    expect(fetched.mock).toHaveBeenCalledTimes(4);
  });

  it("keeps the runtime form when the generator panel fails", async () => {
    mockFetch((input) =>
      String(input).endsWith("/settings/generator")
        ? jsonResponse({ detail: "генератор недоступен" }, 500)
        : jsonResponse(settings()),
    );
    const { view } = createView();

    const loaded = await view.load();

    expect(loaded).toEqual(settings());
    expect(node<HTMLInputElement>("settings-batch").value).toBe("5");
    expect(node("settings-generator-message").hidden).toBe(false);
    expect(node("settings-generator-message").textContent).toBe("генератор недоступен");
  });

  it("shows a runtime read error and returns null", async () => {
    mockFetch((input) =>
      String(input).endsWith("/settings/generator")
        ? jsonResponse(generator())
        : jsonResponse({ detail: "нет файла" }, 500),
    );
    const { view } = createView();

    const result = await view.load();

    expect(result).toBeNull();
    expect(node("settings-message").textContent).toBe("нет файла");
    expect(node("settings-status").textContent).toBe("Настройки недоступны.");
    expect(node("settings-status").classList.contains("is-error")).toBe(true);
    expect(node("settings-generator").textContent).toContain("gpt-test");
  });

  it("reports a malformed runtime body as unavailable", async () => {
    mockFetch((input) =>
      String(input).endsWith("/settings/generator")
        ? jsonResponse(generator())
        : jsonResponse({ run: { batch: 5 } }),
    );
    const { view } = createView();

    const result = await view.load();

    expect(result).toBeNull();
    expect(node("settings-message").textContent).toBe("Сервер вернул неожиданный ответ.");
    expect(node("settings-status").textContent).toBe("Настройки недоступны.");
  });

  it("previews changes and enables apply", async () => {
    mockFetch((input) => {
      if (String(input).endsWith("/preview")) {
        return jsonResponse({ changed: true, diff: "- old\n+ new" });
      }
      if (String(input).endsWith("/settings/generator")) {
        return jsonResponse(generator());
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

  it("applies a previewed change, shows the diff and the backup", async () => {
    const updated = settings({ run: { batch: 6, interval: "6h", max_duration: "45m" } });
    mockFetch((input, init) => {
      if (String(input).endsWith("/preview")) {
        return jsonResponse({ changed: true, diff: "- batch 5\n+ batch 6" });
      }
      if (String(input).endsWith("/settings/generator")) {
        return jsonResponse(generator());
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

  it("reports no changes when the server keeps the file", async () => {
    mockFetch((input, init) => {
      if (String(input).endsWith("/settings/generator")) {
        return jsonResponse(generator());
      }
      if (String(input).endsWith("/preview")) {
        return jsonResponse({ changed: true, diff: "- old\n+ new" });
      }
      if (init?.method === "PUT") {
        return jsonResponse({ changed: false, diff: "", backup: null, settings: settings() });
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
    expect(node("settings-diff").hidden).toBe(true);
    expect(node<HTMLButtonElement>("settings-apply").disabled).toBe(true);
  });

  it("rejects preview when a field is invalid", async () => {
    const fetched = mockFetch((input) =>
      String(input).endsWith("/settings/generator")
        ? jsonResponse(generator())
        : jsonResponse(settings()),
    );
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
    mockFetch((input) => {
      if (String(input).endsWith("/preview")) {
        return pending.promise;
      }
      if (String(input).endsWith("/settings/generator")) {
        return jsonResponse(generator());
      }
      return jsonResponse(settings());
    });
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

  it("refuses to apply without a successful preview", async () => {
    mockFetch((input) =>
      String(input).endsWith("/settings/generator")
        ? jsonResponse(generator())
        : jsonResponse(settings()),
    );
    const { view } = createView();
    await view.load();

    submit();
    await flush();

    expect(node("settings-status").textContent).toBe("Сначала нажмите «Проверить».");
    expect(node("settings-status").classList.contains("is-error")).toBe(true);
  });

  it("refreshes both panels from the refresh button", async () => {
    let generatorCalls = 0;
    const fetched = mockFetch((input) => {
      if (String(input).endsWith("/settings/generator")) {
        generatorCalls += 1;
        return jsonResponse(generator({ model: `gpt-${generatorCalls}` }));
      }
      return jsonResponse(settings());
    });
    const { view } = createView();
    await view.load();
    expect(node("settings-generator").textContent).toContain("gpt-1");

    node<HTMLButtonElement>("settings-refresh").click();
    await flush();

    expect(node("settings-generator").textContent).toContain("gpt-2");
    expect(fetched.calls().filter((call) => call.url === "/api/v1/content/settings")).toHaveLength(
      2,
    );
  });
});

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
