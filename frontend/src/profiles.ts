/** Generator profiles: preview, stock, refill settings and the newest batch. */

import { el, setMessage, setText, toggle } from "./dom";
import { formatDateTime } from "./lib/datetime";
import { errorMessage } from "./lib/errors";
import { formatCount, languageName, statusLabel } from "./lib/format";
import { ApiError, requestJson } from "./lib/http";

const PROFILES_URL = "/api/v1/content/profiles";
const EMPTY_TEXT = "Профилей пока нет.";
const NOT_FOUND_TEXT = "Профиль не найден";
const PREVIEW_FALLBACK = "Превью появится после первого ролика";
const ERROR_LIMIT = 200;
const TARGET_MIN = 1;
const TARGET_MAX = 1000;
const REFILL_MIN = 0;

interface LastRun {
  status: string;
  finishedAt: string;
  generated: number;
  failed: number;
  error: string;
}

interface Profile {
  id: string;
  category: string;
  name: string;
  language: string;
  niche: string;
  width: number;
  height: number;
  audio: boolean;
  stock: number;
  target: number;
  refillAt: number;
  refillEnabled: boolean;
  targetOverride: number | null;
  refillAtOverride: number | null;
  previewUrl: string;
  lastRun: LastRun | null;
}

interface CardController {
  node: HTMLElement;
  apply: (profile: Profile) => void;
  setBusy: (value: boolean) => void;
  setStatus: (text: string, isError: boolean) => void;
}

export interface ProfilesElements {
  list: HTMLElement;
  message: HTMLElement;
  empty: HTMLElement;
  refresh: HTMLButtonElement;
}

export interface ProfilesView {
  load: () => Promise<void>;
}

/**
 * Build the profiles panel.
 */
export function createProfilesView(elements: ProfilesElements): ProfilesView {
  let profiles: Profile[] = [];
  const cards = new Map<string, CardController>();
  // Ids with an in-flight PATCH: they stay locked and block a concurrent refresh.
  const pending = new Set<string>();
  let snapshot = "";
  // Every PATCH start invalidates a list read that began earlier and may be stale.
  let stateVersion = 0;
  let loading = false;

  function updateRefresh(): void {
    elements.refresh.disabled = loading || pending.size > 0;
  }

  /** Reload every profile unless one is being saved right now. */
  async function load(): Promise<void> {
    if (loading || pending.size > 0) {
      return;
    }
    loading = true;
    updateRefresh();
    elements.list.setAttribute("aria-busy", "true");
    setMessage(elements.message, "");
    const version = stateVersion;
    try {
      const response = await requestJson(PROFILES_URL);
      if (version !== stateVersion || pending.size > 0) {
        return;
      }
      profiles = normalizeProfiles(response);
      render();
    } catch (error) {
      if (version === stateVersion) {
        setMessage(elements.message, errorMessage(error));
      }
    } finally {
      loading = false;
      elements.list.removeAttribute("aria-busy");
      updateRefresh();
    }
  }

  function render(): void {
    const next = JSON.stringify(profiles);
    if (next === snapshot) {
      return;
    }
    snapshot = next;
    cards.clear();
    const nodes = profiles.map((profile) => {
      const controller = buildCard(profile);
      cards.set(profile.id, controller);
      return controller.node;
    });
    elements.list.replaceChildren(...nodes);
    toggle(elements.list, nodes.length !== 0);
    toggle(elements.empty, nodes.length === 0);
    setText(elements.empty, EMPTY_TEXT);
  }

  /**
   * Apply one PATCH result to the live card and keep the snapshot in step.
   */
  function applyResult(id: string, profile: Profile, text: string, isError: boolean): void {
    const live = cards.get(id);
    if (live) {
      live.apply(profile);
      live.setStatus(text, isError);
      snapshot = JSON.stringify(profiles);
      return;
    }
    snapshot = "";
    render();
    cards.get(id)?.setStatus(text, isError);
  }

  /**
   * Send one partial update; a pending id blocks a second request.
   */
  async function saveProfile(id: string, payload: Record<string, unknown>): Promise<void> {
    const controller = cards.get(id);
    const current = profiles.find((item) => item.id === id);
    if (!controller || !current || pending.has(id)) {
      return;
    }
    pending.add(id);
    stateVersion += 1;
    controller.setBusy(true);
    controller.setStatus("Сохраняем…", false);
    updateRefresh();
    try {
      const response = await requestJson(profileUrl(current), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const updated = normalizeProfile(response, current);
      const position = profiles.findIndex((item) => item.id === id);
      if (position >= 0) {
        profiles[position] = updated;
      }
      applyResult(id, updated, "Сохранено.", false);
    } catch (error) {
      const latest = profiles.find((item) => item.id === id) ?? current;
      applyResult(id, latest, patchMessage(error), true);
    } finally {
      pending.delete(id);
      const live = cards.get(id);
      if (live) {
        live.setBusy(false);
      }
      updateRefresh();
    }
  }

  function buildCard(initial: Profile): CardController {
    const id = initial.id;
    let profile = initial;
    const card = el("li", "profile-card");
    const media = el("div", "profile-media");
    const image = el("img", "profile-preview");
    const fallback = el("p", "profile-preview-fallback", PREVIEW_FALLBACK);
    const head = el("div", "profile-head");
    const badges = el("p", "profile-badges");
    const facts = el("p", "profile-facts");
    const targetInput = numberInput("Цель", TARGET_MIN, TARGET_MAX);
    const refillInput = numberInput("Порог", REFILL_MIN, TARGET_MAX);
    const enabled = el("input");
    const status = el("p", "status profile-status");
    const lastRun = el("div", "profile-last-run");

    image.alt = `Превью профиля ${profile.id}`;
    image.setAttribute("loading", "lazy");
    image.addEventListener("error", () => {
      toggle(image, false);
      toggle(fallback, true);
    });
    toggle(fallback, false);
    media.append(image, fallback);

    badges.append(
      el("span", "badge", languageName(profile.language)),
      el("span", "badge", profile.niche || "без ниши"),
      el("span", "badge", `${profile.width}×${profile.height}`),
      el("span", "badge", profile.audio ? "аудио" : "без аудио"),
    );
    head.append(el("h3", "profile-id", profile.id), badges);

    enabled.type = "checkbox";
    enabled.className = "profile-refill";
    const switchLabel = el("label", "profile-switch");
    switchLabel.append(enabled, el("span", "", "Добор"));

    const targetField = el("label", "profile-field");
    targetField.append(el("span", "field-label", "Цель"), targetInput);
    const refillField = el("label", "profile-field");
    refillField.append(el("span", "field-label", "Порог"), refillInput);
    const fields = el("div", "profile-fields");
    fields.append(targetField, refillField);

    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    card.append(media, head, facts, switchLabel, fields, status, lastRun);

    function fill(): void {
      fillInputs();
      renderFacts();
      renderLastRun();
      if (image.getAttribute("src") !== profile.previewUrl) {
        toggle(image, true);
        toggle(fallback, false);
        image.src = profile.previewUrl;
      }
    }

    function fillInputs(): void {
      targetInput.value = overrideValue(profile.targetOverride);
      targetInput.placeholder = effectiveValue(profile.target, profile.targetOverride);
      refillInput.value = overrideValue(profile.refillAtOverride);
      refillInput.placeholder = effectiveValue(profile.refillAt, profile.refillAtOverride);
      enabled.checked = profile.refillEnabled;
    }

    function renderFacts(): void {
      facts.replaceChildren(
        el("span", "", `Сток ${formatCount(profile.stock)} / цель ${formatCount(profile.target)}`),
        el("span", "profile-sep", "·"),
        el("span", "", `порог ${formatCount(profile.refillAt)}`),
      );
    }

    function renderLastRun(): void {
      toggle(lastRun, profile.lastRun !== null);
      if (profile.lastRun === null) {
        lastRun.replaceChildren();
        return;
      }
      const parts = [
        el("span", "badge", statusLabel(profile.lastRun.status)),
        el("span", "", `партия: ${formatCount(profile.lastRun.generated)} готово`),
        el("span", "", `${formatCount(profile.lastRun.failed)} с ошибкой`),
        el(
          "span",
          "",
          profile.lastRun.finishedAt
            ? `завершена ${formatDateTime(profile.lastRun.finishedAt)}`
            : "ещё идёт",
        ),
      ];
      if (profile.lastRun.error) {
        parts.push(el("p", "profile-error", truncate(profile.lastRun.error, ERROR_LIMIT)));
      }
      lastRun.replaceChildren(...parts);
    }

    function apply(next: Profile): void {
      profile = next;
      fill();
    }

    function setBusy(value: boolean): void {
      enabled.disabled = value;
      targetInput.disabled = value;
      refillInput.disabled = value;
      card.setAttribute("aria-busy", String(value));
    }

    function setStatus(text: string, isError: boolean): void {
      setText(status, text);
      status.classList.toggle("is-error", isError);
    }

    function saveNumber(field: string, input: HTMLInputElement, min: number, max: number): void {
      if (pending.has(id)) {
        return;
      }
      const raw = input.value.trim();
      if (raw === "") {
        void saveProfile(id, { [field]: null });
        return;
      }
      const value = Number(raw);
      if (!Number.isInteger(value) || value < min || value > max) {
        fillInputs();
        setStatus(`Введите целое число от ${min} до ${max}.`, true);
        return;
      }
      void saveProfile(id, { [field]: value });
    }

    enabled.addEventListener("change", () => {
      void saveProfile(id, { refill_enabled: enabled.checked });
    });
    targetInput.addEventListener("change", () => {
      saveNumber("target", targetInput, TARGET_MIN, TARGET_MAX);
    });
    refillInput.addEventListener("change", () => {
      saveNumber("refill_at", refillInput, REFILL_MIN, TARGET_MAX);
    });

    fill();
    setBusy(pending.has(id));
    return { node: card, apply, setBusy, setStatus };
  }

  elements.refresh.addEventListener("click", () => {
    void load();
  });

  return { load };
}

function normalizeProfiles(response: unknown): Profile[] {
  const body = asObject(response);
  const rows = Array.isArray(body.profiles) ? body.profiles : [];
  return rows.map((row) => normalizeProfile(row, null));
}

function normalizeProfile(value: unknown, fallback: Profile | null): Profile {
  const body = asObject(value);
  const lastRun =
    body.last_run === undefined
      ? (fallback?.lastRun ?? null)
      : body.last_run === null
        ? null
        : normalizeLastRun(body.last_run);
  return {
    id: text(body.id, fallback?.id ?? ""),
    category: text(body.category, fallback?.category ?? ""),
    name: text(body.name, fallback?.name ?? ""),
    language: text(body.language, fallback?.language ?? ""),
    niche: text(body.niche, fallback?.niche ?? ""),
    width: numberValue(body.width, fallback?.width ?? 0),
    height: numberValue(body.height, fallback?.height ?? 0),
    audio: body.audio === undefined ? (fallback?.audio ?? false) : Boolean(body.audio),
    stock: numberValue(body.stock, fallback?.stock ?? 0),
    target: numberValue(body.target, fallback?.target ?? 0),
    refillAt: numberValue(body.refill_at, fallback?.refillAt ?? 0),
    refillEnabled:
      body.refill_enabled === undefined
        ? (fallback?.refillEnabled ?? false)
        : Boolean(body.refill_enabled),
    targetOverride: overrideValueOf(body.target_override, fallback?.targetOverride ?? null),
    refillAtOverride: overrideValueOf(body.refill_at_override, fallback?.refillAtOverride ?? null),
    previewUrl: text(body.preview_url, fallback?.previewUrl ?? ""),
    lastRun,
  };
}

function normalizeLastRun(value: unknown): LastRun {
  const body = asObject(value);
  return {
    status: text(body.status, ""),
    finishedAt:
      body.finished_at === null || body.finished_at === undefined ? "" : String(body.finished_at),
    generated: numberValue(body.generated, 0),
    failed: numberValue(body.failed, 0),
    error: text(body.error, ""),
  };
}

function profileUrl(profile: Profile): string {
  const category = encodeURIComponent(profile.category);
  const name = encodeURIComponent(profile.name);
  return `${PROFILES_URL}/${category}/${name}`;
}

function numberInput(label: string, min: number, max: number): HTMLInputElement {
  const input = el("input");
  input.type = "number";
  input.inputMode = "numeric";
  input.min = String(min);
  input.max = String(max);
  input.step = "1";
  input.setAttribute("aria-label", label);
  return input;
}

function overrideValue(value: number | null): string {
  return value === null ? "" : String(value);
}

function effectiveValue(effective: number, override: number | null): string {
  const value = formatCount(effective);
  return override === null ? `${value} (глобально)` : value;
}

function truncate(value: string, limit: number): string {
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`;
}

function patchMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return NOT_FOUND_TEXT;
  }
  return `Не сохранено: ${errorMessage(error)}`;
}

function asObject(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function text(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function numberValue(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function overrideValueOf(value: unknown, fallback: number | null): number | null {
  if (value === null) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}
