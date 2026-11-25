import { describe, expect, it } from "vitest";

import { formatBytes, formatCount, languageName, statusLabel } from "../../../src/lib/format";

describe("formatCount", () => {
  it("formats thousands with a non-breaking space", () => {
    expect(formatCount(1000)).toBe("1\u00a0000");
    expect(formatCount(42)).toBe("42");
  });
});

describe("formatBytes", () => {
  it("uses the matching unit and a comma decimal separator", () => {
    expect(formatBytes(0)).toBe("0 Б");
    expect(formatBytes(1023)).toBe("1023 Б");
    expect(formatBytes(1536)).toBe("1,5 КБ");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5,0 МБ");
    expect(formatBytes(3 * 1024 * 1024 * 1024)).toBe("3,0 ГБ");
  });

  it("returns a dash for negative or non-finite values", () => {
    expect(formatBytes(-1)).toBe("—");
    expect(formatBytes(Number.NaN)).toBe("—");
    expect(formatBytes(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("languageName", () => {
  it("maps known codes and uppercases unknown ones", () => {
    expect(languageName("ru")).toBe("Русский");
    expect(languageName("de")).toBe("Немецкий");
    expect(languageName("xx")).toBe("XX");
  });
});

describe("statusLabel", () => {
  it("maps known statuses and keeps unknown ones as is", () => {
    expect(statusLabel("building")).toBe("сборка");
    expect(statusLabel("ready_local")).toBe("локально");
    expect(statusLabel("custom")).toBe("custom");
  });
});
