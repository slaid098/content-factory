import { describe, expect, it } from "vitest";

import { formatDateTime, formatRemaining } from "../../../src/lib/datetime";

describe("formatDateTime", () => {
  it("formats a valid ISO timestamp in Russian", () => {
    expect(formatDateTime("2026-11-26T10:05:00Z")).toMatch(/^\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}$/);
  });

  it("returns a dash for an invalid date", () => {
    expect(formatDateTime("not-a-date")).toBe("—");
  });
});

describe("formatRemaining", () => {
  const now = Date.parse("2026-11-26T10:00:00Z");

  it("shows hours and padded minutes", () => {
    expect(formatRemaining("2026-11-26T12:05:30Z", now)).toBe("2 ч 05 мин");
  });

  it("shows minutes and seconds under one hour", () => {
    expect(formatRemaining("2026-11-26T10:03:09Z", now)).toBe("3:09");
  });

  it("marks an expired or invalid value", () => {
    expect(formatRemaining("2026-11-26T09:59:00Z", now)).toBe("истёк");
    expect(formatRemaining("oops", now)).toBe("время неизвестно");
  });
});
