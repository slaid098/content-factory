import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, MESSAGES, READ_TIMEOUT_MS, requestJson } from "../../../src/lib/http";
import { brokenJsonResponse, jsonResponse, mockFetch } from "../helpers";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("requestJson", () => {
  it("requests JSON with a same-origin Accept header", async () => {
    const fetched = mockFetch(() => jsonResponse({ ok: true }));

    const data = await requestJson("/api/v1/stock");

    expect(data).toEqual({ ok: true });
    expect(fetched.calls()[0]?.url).toBe("/api/v1/stock");
    expect(fetched.calls()[0]?.init?.headers).toMatchObject({ Accept: "application/json" });
    expect(READ_TIMEOUT_MS).toBe(15000);
  });

  it("keeps custom headers and the request method", async () => {
    const fetched = mockFetch(() => jsonResponse({ created: true }));

    await requestJson("/api/v1/content/queries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });

    expect(fetched.calls()[0]?.init?.method).toBe("POST");
    expect(fetched.calls()[0]?.init?.headers).toMatchObject({
      Accept: "application/json",
      "Content-Type": "application/json",
    });
  });

  it.each([
    [401, MESSAGES.unauthorized],
    [404, MESSAGES.archiveGone],
    [409, MESSAGES.busy],
    [503, MESSAGES.disabled],
  ])("maps status %i to a fixed Russian message", async (status, message) => {
    mockFetch(() => jsonResponse({ detail: "server text" }, status));

    await expect(requestJson("/api/v1/content/x")).rejects.toMatchObject({
      name: "ApiError",
      message,
      status,
    });
  });

  it("uses the server detail for 422 and the generic fallback without it", async () => {
    mockFetch(() => jsonResponse({ detail: "Поле обязательно" }, 422));
    await expect(requestJson("/x")).rejects.toMatchObject({
      message: "Поле обязательно",
      status: 422,
    });

    vi.unstubAllGlobals();
    mockFetch(() => jsonResponse({}, 422));
    await expect(requestJson("/x")).rejects.toMatchObject({
      message: MESSAGES.invalid,
      status: 422,
    });
  });

  it("uses the server detail for other errors and falls back to the status text", async () => {
    mockFetch(() => jsonResponse({ detail: "Внутренняя ошибка" }, 500));
    await expect(requestJson("/x")).rejects.toMatchObject({
      message: "Внутренняя ошибка",
      status: 500,
    });

    vi.unstubAllGlobals();
    mockFetch(() => jsonResponse({}, 500));
    await expect(requestJson("/x")).rejects.toMatchObject({
      message: "Сервер вернул ошибку (код 500).",
      status: 500,
    });
  });

  it("turns an unreadable body into a bad-response error", async () => {
    mockFetch(() => brokenJsonResponse(200));

    await expect(requestJson("/x")).rejects.toMatchObject({
      message: MESSAGES.badResponse,
      status: 200,
    });
  });

  it("turns a network failure into the offline message", async () => {
    mockFetch(() => {
      throw new TypeError("network down");
    });

    const error = await requestJson("/x").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ message: MESSAGES.offline, status: 0 });
  });

  it("aborts after the timeout and reports it as a timeout", async () => {
    vi.useFakeTimers();
    mockFetch(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );

    const pending = expect(requestJson("/x", {}, 10)).rejects.toMatchObject({
      message: MESSAGES.timeout,
      status: 0,
    });
    await vi.advanceTimersByTimeAsync(10);

    await pending;
  });
});
