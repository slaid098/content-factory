/** Generator profiles: preview, stock, refill settings and the newest batch. */

import { el, setMessage, setText, toggle } from "./dom";
import { formatDateTime } from "./lib/datetime";
import { errorMessage } from "./lib/errors";
import { formatCount, languageName, statusLabel } from "./lib/format";
import { ApiError, requestJson } from "./lib/http";

const PROFILES_URL = "/api/v1/content/profiles";
const EMPTY_TEXT = "Профилей пока нет.";
const LOADING_TEXT = "Загружаем профили…";
const NOT_FOUND_TEXT = "Профиль не найден";
const PREVIEW_FALLBACK = "Превью появится после первого ролика или поста.";
const PREVIEW_FALLBACK_SHORT = "Нет превью";
const ERROR_LIMIT = 200;
const TARGET_MIN = 1;
const TARGET_MAX = 1000;
const REFILL_MIN = 0;
const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

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
  photoStock: number;
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
  focusPreview: () => void;
}

interface PreviewDialog {
  open: (profile: Profile, trigger: HTMLButtonElement) => void;
  close: () => void;
}

interface PreviewDialogOptions {
  onClose: (profileId: string, trigger: HTMLButtonElement | null) => void;
}

export interface ProfilesElements {
  list: HTMLElement;
  message: HTMLElement;
  loading: HTMLElement;
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

  const dialog = createPreviewDialog({ onClose: restorePreviewFocus });

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
    setText(elements.loading, LOADING_TEXT);
    toggle(elements.loading, true);
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
      toggle(elements.loading, false);
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

  function restorePreviewFocus(profileId: string, trigger: HTMLButtonElement | null): void {
    if (trigger?.isConnected) {
      trigger.focus();
      return;
    }
    cards.get(profileId)?.focusPreview();
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
    const media = el("button", "profile-media");
    const image = el("img", "profile-preview");
    const fallback = el("span", "profile-preview-fallback", PREVIEW_FALLBACK_SHORT);
    const body = el("div", "profile-body");
    const head = el("div", "profile-head");
    const badges = el("p", "profile-badges");
    const counters = el("div", "profile-counters");
    const shortsValue = el("span", "profile-counter-value");
    const shortsBar = el("div", "profile-progress");
    const shortsFill = el("span", "profile-progress-fill");
    const postsValue = el("span", "profile-counter-value");
    const targetInput = numberInput("Цель", TARGET_MIN, TARGET_MAX);
    const refillInput = numberInput("Порог добора", REFILL_MIN, TARGET_MAX);
    const enabled = el("input");
    const status = el("p", "status profile-status");
    const lastRun = el("div", "profile-last-run");

    media.type = "button";
    media.setAttribute("aria-haspopup", "dialog");
    fallback.setAttribute("role", "img");
    fallback.title = PREVIEW_FALLBACK;
    fallback.setAttribute("aria-label", PREVIEW_FALLBACK);
    image.setAttribute("loading", "lazy");
    image.setAttribute("decoding", "async");
    image.addEventListener("error", () => {
      showPreview(false);
    });
    media.append(image, fallback);
    media.addEventListener("click", () => {
      dialog.open(profile, media);
    });

    badges.append(
      el("span", "badge", languageName(profile.language)),
      el("span", "badge", profile.niche || "без ниши"),
      el("span", "badge", `${profile.width}×${profile.height}`),
      el("span", "badge", profile.audio ? "аудио" : "без аудио"),
    );
    head.append(el("h3", "profile-id", profile.id), badges);

    shortsBar.setAttribute("role", "progressbar");
    shortsBar.setAttribute("aria-label", "Shorts");
    shortsBar.setAttribute("aria-valuemin", "0");
    shortsBar.append(shortsFill);
    const shorts = el("div", "profile-counter");
    const shortsHead = el("p", "profile-counter-head");
    shortsHead.append(el("span", "profile-counter-name", "Shorts"), " ", shortsValue);
    shorts.append(shortsHead, shortsBar);

    const posts = el("div", "profile-counter");
    const postsHead = el("p", "profile-counter-head");
    postsHead.append(el("span", "profile-counter-name", "Посты"), " ", postsValue);
    posts.append(postsHead, el("p", "profile-counter-note", "только учёт, вне добора"));
    counters.append(shorts, posts);

    enabled.type = "checkbox";
    enabled.className = "profile-refill";
    enabled.setAttribute("role", "switch");
    const switchLabel = el("label", "profile-switch");
    switchLabel.append(enabled, el("span", "profile-switch-label", "Добор роликов"));

    const targetField = el("label", "profile-field");
    targetField.append(el("span", "field-label", "Цель"), targetInput);
    const refillField = el("label", "profile-field");
    refillField.append(el("span", "field-label", "Порог добора"), refillInput);
    const fields = el("div", "profile-fields");
    fields.append(targetField, refillField);

    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    body.append(head, counters, switchLabel, fields, status, lastRun);
    card.append(media, body);

    function showPreview(visible: boolean): void {
      toggle(image, visible);
      toggle(fallback, !visible);
    }

    function fill(): void {
      fillInputs();
      renderPreview();
      renderCounters();
      renderLastRun();
    }

    function renderPreview(): void {
      media.setAttribute("aria-label", `Открыть превью профиля ${profile.id}`);
      image.alt = `Превью профиля ${profile.id}`;
      const ratio = profile.width > 0 && profile.height > 0 ? profile.width / profile.height : 1;
      media.style.setProperty("--preview-ratio", String(ratio));
      if (profile.width > 0 && profile.height > 0) {
        image.setAttribute("width", String(profile.width));
        image.setAttribute("height", String(profile.height));
      } else {
        image.removeAttribute("width");
        image.removeAttribute("height");
      }
      if (!profile.previewUrl) {
        image.removeAttribute("src");
        showPreview(false);
        return;
      }
      if (image.getAttribute("src") !== profile.previewUrl) {
        showPreview(true);
        image.src = profile.previewUrl;
      }
    }

    function renderCounters(): void {
      const { stock, target } = profile;
      shortsValue.textContent = `${formatCount(stock)} / ${formatCount(target)}`;
      const capped = target > 0 ? Math.min(stock, target) : 0;
      shortsBar.setAttribute("aria-valuemax", String(target));
      shortsBar.setAttribute("aria-valuenow", String(capped));
      shortsBar.setAttribute("aria-valuetext", `${formatCount(stock)} из ${formatCount(target)}`);
      const share = target > 0 ? Math.min(100, Math.max(0, (stock / target) * 100)) : 0;
      shortsFill.style.width = `${share}%`;
      postsValue.textContent = formatCount(profile.photoStock);
    }

    function fillInputs(): void {
      targetInput.value = overrideValue(profile.targetOverride);
      targetInput.placeholder = effectiveValue(profile.target, profile.targetOverride);
      refillInput.value = overrideValue(profile.refillAtOverride);
      refillInput.placeholder = effectiveValue(profile.refillAt, profile.refillAtOverride);
      enabled.checked = profile.refillEnabled;
      enabled.setAttribute("aria-checked", String(profile.refillEnabled));
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
      enabled.setAttribute("aria-checked", String(enabled.checked));
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
    return { node: card, apply, setBusy, setStatus, focusPreview: () => media.focus() };
  }

  elements.refresh.addEventListener("click", () => {
    void load();
  });

  return { load };
}

let dialogCount = 0;

/**
 * Build the one preview dialog of the profiles tab.
 */
function createPreviewDialog(options: PreviewDialogOptions): PreviewDialog {
  dialogCount += 1;
  const titleId = `profiles-preview-title-${dialogCount}`;
  const overlay = el("div", "profile-dialog");
  overlay.hidden = true;
  overlay.tabIndex = -1;
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-labelledby", titleId);

  const panel = el("div", "profile-dialog-panel");
  const closeButton = el("button", "profile-dialog-close", "×");
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "Закрыть превью");
  const title = el("h2", "profile-dialog-title");
  title.id = titleId;
  const media = el("div", "profile-dialog-media");
  const image = el("img", "profile-dialog-image");
  const fallback = el("p", "profile-dialog-fallback", PREVIEW_FALLBACK);
  media.append(image, fallback);
  panel.append(closeButton, title, media);
  overlay.append(panel);
  document.body.append(overlay);

  let profileId: string | null = null;
  let trigger: HTMLButtonElement | null = null;

  image.addEventListener("error", () => {
    image.hidden = true;
    fallback.hidden = false;
  });
  closeButton.addEventListener("click", hideDialog);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      hideDialog();
    }
  });

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      event.preventDefault();
      hideDialog();
      return;
    }
    if (event.key !== "Tab") {
      return;
    }
    const items = Array.from(overlay.querySelectorAll<HTMLElement>(FOCUSABLE));
    const first = items[0];
    const last = items[items.length - 1];
    if (!first || !last) {
      event.preventDefault();
      return;
    }
    const active = document.activeElement;
    const outside = active === overlay || !overlay.contains(active);
    if (event.shiftKey && (active === first || outside)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || outside)) {
      event.preventDefault();
      first.focus();
    }
  }

  function onFocusIn(event: FocusEvent): void {
    const target = event.target;
    if (target instanceof Node && target !== overlay && overlay.contains(target)) {
      return;
    }
    closeButton.focus();
  }

  function hideDialog(): void {
    if (overlay.hidden) {
      return;
    }
    document.removeEventListener("keydown", onKeydown);
    document.removeEventListener("focusin", onFocusIn);
    overlay.hidden = true;
    document.body.classList.remove("has-profile-dialog");
    const closedId = profileId ?? "";
    const origin = trigger;
    profileId = null;
    trigger = null;
    options.onClose(closedId, origin);
  }

  function open(profile: Profile, origin: HTMLButtonElement): void {
    profileId = profile.id;
    trigger = origin;
    title.textContent = `${profile.id} · ${profile.width}×${profile.height}`;
    image.alt = `Превью профиля ${profile.id}`;
    if (profile.previewUrl) {
      image.hidden = false;
      fallback.hidden = true;
      image.src = profile.previewUrl;
    } else {
      image.hidden = true;
      fallback.hidden = false;
      image.removeAttribute("src");
    }
    overlay.hidden = false;
    document.body.classList.add("has-profile-dialog");
    document.addEventListener("keydown", onKeydown);
    document.addEventListener("focusin", onFocusIn);
    closeButton.focus();
  }

  return { open, close: hideDialog };
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
    photoStock:
      body.photo_stock === undefined
        ? (fallback?.photoStock ?? 0)
        : numberValue(body.photo_stock, 0),
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
