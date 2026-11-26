import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createQueriesView } from "../../src/queries";
import { bodyOf, flush, jsonResponse, mockFetch, node, resetPage } from "./helpers";

function createView() {
  return createQueriesView({
    language: node<HTMLSelectElement>("queries-language"),
    search: node<HTMLInputElement>("queries-search"),
    summary: node("queries-summary"),
    message: node("queries-message"),
    list: node("queries-list"),
    empty: node("queries-empty"),
    form: node<HTMLFormElement>("query-form"),
    input: node<HTMLInputElement>("new-query"),
    add: node<HTMLButtonElement>("query-add"),
    status: node("query-status"),
    refresh: node<HTMLButtonElement>("queries-refresh"),
  });
}

function submit(): void {
  node<HTMLFormElement>("query-form").dispatchEvent(
    new Event("submit", { bubbles: true, cancelable: true }),
  );
}

const responseBody = {
  queries: [
    { query: "заправка", count: 3, in_file: true },
    { query: "кафе", count: 0, in_file: false },
  ],
  unclassified: 2,
  total: 5,
};

beforeEach(() => {
  resetPage();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createQueriesView", () => {
  it("renders rows, badges and the summary", async () => {
    mockFetch(() => jsonResponse(responseBody));
    const view = createView();

    await view.load("ru");

    const list = node("queries-list");
    expect(list.hidden).toBe(false);
    expect(list.children.length).toBe(2);
    expect(list.children[1]?.classList.contains("is-zero")).toBe(true);
    expect(list.children[1]?.textContent).toContain("нет в файле");
    expect(node("queries-summary").textContent).toBe(
      "Запросов: 2, в файле: 1, только в базе: 1. Видео по темам: 5, без темы: 2.",
    );
  });

  it("marks the list busy while queries load", async () => {
    const pending = deferred<Response>();
    mockFetch(() => pending.promise);
    const view = createView();

    const loading = view.load("ru");

    expect(node("queries-summary").textContent).toBe("Загружаем запросы…");
    expect(node("queries-list").getAttribute("aria-busy")).toBe("true");

    pending.resolve(jsonResponse(responseBody));
    await loading;

    expect(node("queries-list").getAttribute("aria-busy")).toBe("false");
    expect(node("queries-list").children.length).toBe(2);
  });

  it("filters the list by the search field", async () => {
    mockFetch(() => jsonResponse(responseBody));
    const view = createView();
    await view.load("ru");

    const search = node<HTMLInputElement>("queries-search");
    search.value = "запр";
    search.dispatchEvent(new Event("input", { bubbles: true }));

    expect(node("queries-list").children.length).toBe(1);

    search.value = "нет-такого";
    search.dispatchEvent(new Event("input", { bubbles: true }));

    expect(node("queries-list").hidden).toBe(true);
    expect(node("queries-empty").hidden).toBe(false);
    expect(node("queries-empty").textContent).toBe("Ничего не найдено.");
  });

  it("shows the empty state for an empty list", async () => {
    mockFetch(() => jsonResponse({ queries: [], unclassified: 0, total: 0 }));
    const view = createView();

    await view.load("ru");

    expect(node("queries-list").hidden).toBe(true);
    expect(node("queries-empty").textContent).toBe("Запросов пока нет.");
  });

  it("does not request without a language", async () => {
    const fetched = mockFetch(() => jsonResponse(responseBody));
    const view = createView();

    await view.load("");

    expect(fetched.mock).not.toHaveBeenCalled();
  });

  it("shows a load error and an unavailable list", async () => {
    mockFetch(() => jsonResponse({ detail: "boom" }, 500));
    const view = createView();

    await view.load("ru");

    expect(node("queries-message").textContent).toBe("boom");
    expect(node("queries-summary").textContent).toBe("");
    expect(node("queries-list").hidden).toBe(true);
    expect(node("queries-empty").textContent).toBe("Список запросов недоступен.");
  });

  it("rejects an empty new query", () => {
    const fetched = mockFetch(() => jsonResponse({ created: true }));
    createView();

    submit();

    const status = node("query-status");
    expect(status.textContent).toBe("Введите поисковый запрос.");
    expect(status.classList.contains("is-error")).toBe(true);
    expect(fetched.mock).not.toHaveBeenCalled();
  });

  it("adds a query with the selected language and reloads the list", async () => {
    const fetched = mockFetch((_input, init) =>
      init?.method === "POST" ? jsonResponse({ created: true }) : jsonResponse(responseBody),
    );
    const view = createView();
    await view.load("ru");
    node<HTMLSelectElement>("queries-language").value = "ru";
    node<HTMLInputElement>("new-query").value = "  парковка  ";

    submit();
    await flush();

    expect(node("query-status").textContent).toBe("Запрос добавлен в файл.");
    expect(node<HTMLInputElement>("new-query").value).toBe("");
    const post = fetched.calls().find((call) => call.init?.method === "POST");
    expect(post?.url).toBe("/api/v1/content/queries");
    expect(bodyOf(post?.init)).toEqual({ language: "ru", query: "парковка" });
    expect(fetched.calls().filter((call) => call.init?.method !== "POST").length).toBe(2);
  });

  it("reports an existing query", async () => {
    mockFetch((_input, init) =>
      init?.method === "POST" ? jsonResponse({ created: false }) : jsonResponse(responseBody),
    );
    const view = createView();
    await view.load("ru");
    node<HTMLInputElement>("new-query").value = "кафе";

    submit();
    await flush();

    expect(node("query-status").textContent).toBe("Такой запрос уже есть в файле.");
  });

  it("shows an add error and keeps the input", async () => {
    mockFetch((_input, init) => {
      if (init?.method === "POST") {
        throw new TypeError("offline");
      }
      return jsonResponse(responseBody);
    });
    const view = createView();
    await view.load("ru");
    node<HTMLInputElement>("new-query").value = "кафе";

    submit();
    await flush();

    expect(node("query-status").textContent).toBe(
      "Сервер недоступен. Проверьте соединение и попробуйте ещё раз.",
    );
    expect(node<HTMLInputElement>("new-query").value).toBe("кафе");
    expect(node<HTMLButtonElement>("query-add").disabled).toBe(false);
  });

  it("does not add without any language", async () => {
    const fetched = mockFetch(() => jsonResponse(responseBody));
    createView();
    node<HTMLInputElement>("new-query").value = "кафе";

    submit();
    await flush();

    expect(fetched.mock).not.toHaveBeenCalled();
  });

  it("ignores a stale response and renders the latest one", async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    let call = 0;
    mockFetch(() => {
      call += 1;
      return call === 1 ? first.promise : second.promise;
    });
    const view = createView();

    const loadFirst = view.load("ru");
    const loadSecond = view.load("en");
    second.resolve(
      jsonResponse({
        queries: [{ query: "en", count: 1, in_file: true }],
        unclassified: 0,
        total: 1,
      }),
    );
    await loadSecond;
    first.resolve(
      jsonResponse({
        queries: [{ query: "ru", count: 9, in_file: true }],
        unclassified: 0,
        total: 9,
      }),
    );
    await loadFirst;

    expect(node("queries-list").textContent).toContain("en");
    expect(node("queries-list").textContent).not.toContain("ru");
  });

  it("reloads from the refresh button with the selected language", async () => {
    const fetched = mockFetch(() => jsonResponse(responseBody));
    createView();
    const select = node<HTMLSelectElement>("queries-language");
    const option = document.createElement("option");
    option.value = "de";
    option.textContent = "Немецкий";
    select.append(option);
    select.value = "de";

    node<HTMLButtonElement>("queries-refresh").click();
    await flush();

    expect(fetched.calls()[0]?.url).toBe("/api/v1/content/queries?language=de");
  });
});

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
