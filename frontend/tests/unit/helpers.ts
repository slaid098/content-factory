import { vi } from "vitest";

import pageHtml from "../../index.html?raw";

export type FetchHandler = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Response | Promise<Response>;

export interface MockFetch {
  mock: ReturnType<typeof vi.fn<FetchHandler>>;
  calls: () => Array<{ url: string; init: RequestInit | undefined }>;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

export function brokenJsonResponse(status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      throw new SyntaxError("bad json");
    },
  } as unknown as Response;
}

export function mockFetch(handler: FetchHandler): MockFetch {
  const mock = vi.fn<FetchHandler>(handler);
  vi.stubGlobal("fetch", mock);
  return {
    mock,
    calls: () =>
      mock.mock.calls.map(([input, init]) => ({
        url: typeof input === "string" ? input : String(input),
        init,
      })),
  };
}

export function resetPage(): void {
  document.body.className = "";
  const parsed = new DOMParser().parseFromString(pageHtml, "text/html");
  const nodes = Array.from(parsed.body.childNodes, (node) => document.importNode(node, true));
  document.body.replaceChildren(...nodes);
}

export function node<T extends HTMLElement = HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) {
    throw new Error(`Не найден элемент #${id}`);
  }
  return found as T;
}

export async function flush(): Promise<void> {
  for (let step = 0; step < 10; step += 1) {
    await Promise.resolve();
  }
}

export function bodyOf(init: RequestInit | undefined): Record<string, unknown> {
  if (typeof init?.body !== "string") {
    return {};
  }
  return JSON.parse(init.body) as Record<string, unknown>;
}
