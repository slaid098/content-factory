import { describe, expect, it } from "vitest";

import { byId, el, setMessage, setText, toggle } from "../../src/dom";

describe("dom", () => {
  it("builds an element with a class and text", () => {
    const span = el("span", "badge", "готово");

    expect(span.tagName).toBe("SPAN");
    expect(span.className).toBe("badge");
    expect(span.textContent).toBe("готово");
  });

  it("builds a typed element without a class or text", () => {
    const input = el("input");

    expect(input.type).toBe("text");
    expect(input.className).toBe("");
  });

  it("finds an element by id and throws a Russian error when missing", () => {
    const known = el("p");
    known.id = "known";
    document.body.append(known);

    expect(byId("known")).toBe(known);
    expect(() => byId("missing")).toThrow("Не найден элемент #missing");
  });

  it("toggles visibility through the hidden attribute", () => {
    const node = el("div");

    toggle(node, true);
    expect(node.hidden).toBe(false);
    toggle(node, false);
    expect(node.hidden).toBe(true);
  });

  it("shows a message only when it has text", () => {
    const message = el("p");

    setMessage(message, "ошибка");
    expect(message.textContent).toBe("ошибка");
    expect(message.hidden).toBe(false);

    setMessage(message, "");
    expect(message.textContent).toBe("");
    expect(message.hidden).toBe(true);
  });

  it("sets text without touching the hidden attribute", () => {
    const label = el("span");
    label.hidden = true;

    setText(label, "4 видео");

    expect(label.textContent).toBe("4 видео");
    expect(label.hidden).toBe(true);
  });
});
