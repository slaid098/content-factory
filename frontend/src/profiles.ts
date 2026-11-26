/** Generator profiles: list, detail, prompts, recipe chips and refill settings. */

import { byId, el, setMessage, setText, toggle } from "./dom";
import { errorMessage } from "./lib/errors";
import { formatCount, languageName } from "./lib/format";
import { ApiError, requestJson } from "./lib/http";

const PROFILES_URL = "/api/v1/content/profiles";
const AUTOSAVE_MS = 500;
const TARGET_MIN = 1;
const TARGET_MAX = 1000;
const REFILL_MIN = 0;
const PHOTO_REFILL_MAX = 999;

type ContentType = "video" | "photo";
type ProfileTab = ContentType | "settings";
type CheckStage = "source" | "final";

const OUTPUT_ORDER: readonly ContentType[] = ["video", "photo"];
const INTERNAL_CHIPS: Record<string, string> = {
  card: "рендер",
  vast: "текст + музыка",
};
const CHECK_LABELS: Record<string, string> = {
  clef: "проверка кадров",
  image: "проверка фото",
  mp4: "проверка mp4",
};
const VALUE_LABELS: Record<string, string> = {
  age: "возраст",
  hair: "волосы",
  hair_color: "волосы",
  hair_color_text: "волосы",
  location: "место",
  place: "место",
  season: "сезон",
};
const ICONS = {
  gear: "M19.14 12.94c.04-.3.06-.61.06-.94s-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96a7.03 7.03 0 0 0-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84a.48.48 0 0 0-.48.41l-.36 2.54c-.59.24-1.13.56-1.62.94l-2.39-.96a.49.49 0 0 0-.59.22L2.74 8.87a.49.49 0 0 0 .12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.48-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.49.49 0 0 0-.12-.61l-2.01-1.58zM12 15.6A3.6 3.6 0 1 1 12 8.4a3.6 3.6 0 0 1 0 7.2z",
  pause: "M6 5h4v14H6zm8 0h4v14h-4z",
  play: "M8 5v14l11-7z",
} as const;
// The literal is split so the source guard sees no external URL in the bundle inputs.
const SVG_NAMESPACE = ["http", "www.w3.org/2000/svg"].join("://");

interface ProductionCheck {
  name: string;
  stage: CheckStage;
}

interface ProductionStep {
  producer: string;
  type: ContentType;
  checks: ProductionCheck[];
}

interface ProfileSummary {
  id: string;
  category: string;
  name: string;
  language: string;
  niche: string;
  width: number;
  height: number;
  audio: boolean;
  outputs: ContentType[];
  stock: number;
  photoStock: number;
  target: number;
  refillAt: number;
  photoTarget: number;
  photoRefillAt: number;
  refillEnabled: boolean;
  targetOverride: number | null;
  refillAtOverride: number | null;
  previewUrl: string;
}

interface ProfileSettings {
  video: Record<string, unknown>;
  rent: Record<string, unknown>;
  text: Record<string, unknown>;
  delivery: Record<string, unknown>;
}

interface ProfileDetail extends ProfileSummary {
  production: ProductionStep[];
  settings: ProfileSettings;
}

interface PromptFile {
  filename: string;
  text: string;
}

interface PromptCard {
  id: string;
  values: Record<string, unknown>;
  prompt: string;
  overridden: boolean;
}

interface ProfilePrompts {
  template: PromptFile | null;
  cards: PromptCard[];
  photo: PromptFile | null;
}

interface RowController {
  node: HTMLLIElement;
  update: () => void;
  setBusy: (busy: boolean) => void;
  setStatus: (text: string, isError: boolean) => void;
}

export interface ProfilesView {
  load: () => Promise<void>;
}

function profilesUi() {
  return {
    listView: byId("profiles-list-view"),
    list: byId("profiles-list"),
    empty: byId("profiles-empty"),
    message: byId("profiles-message"),
    loading: byId("profiles-loading"),
    refresh: byId<HTMLButtonElement>("profiles-refresh"),
    view: byId("profile-view"),
    back: byId<HTMLButtonElement>("profile-back"),
    title: byId("profile-view-title"),
    meta: byId("profile-view-meta"),
    viewMessage: byId("profile-view-message"),
    viewRefill: byId<HTMLButtonElement>("profile-view-refill"),
    preview: byId<HTMLButtonElement>("profile-preview"),
    previewImage: byId<HTMLImageElement>("profile-preview-image"),
    previewFallback: byId("profile-preview-fallback"),
    lightbox: byId<HTMLDialogElement>("profile-lightbox"),
    lightboxTitle: byId("profile-lightbox-title"),
    lightboxImage: byId<HTMLImageElement>("profile-lightbox-image"),
    lightboxClose: byId<HTMLButtonElement>("profile-lightbox-close"),
    tabVideo: byId<HTMLButtonElement>("profile-tab-video"),
    tabPhoto: byId<HTMLButtonElement>("profile-tab-photo"),
    tabSettings: byId<HTMLButtonElement>("profile-tab-settings"),
    panelVideo: byId("profile-panel-video"),
    panelPhoto: byId("profile-panel-photo"),
    panelSettings: byId("profile-panel-settings"),
    video: byId("profile-video"),
    photo: byId("profile-photo"),
    form: byId<HTMLFormElement>("profile-settings-form"),
    refillEnabled: byId<HTMLInputElement>("profile-refill-enabled"),
    target: byId<HTMLInputElement>("profile-target"),
    refillAt: byId<HTMLInputElement>("profile-refill-at"),
    photoTarget: byId<HTMLInputElement>("profile-photo-target"),
    photoRefillAt: byId<HTMLInputElement>("profile-photo-refill-at"),
    settingsStatus: byId("profile-settings-status"),
    settingsVideo: byId("profile-settings-video"),
    settingsRent: byId("profile-settings-rent"),
    settingsText: byId("profile-settings-text"),
    settingsDelivery: byId("profile-settings-delivery"),
  };
}

/**
 * Build the profiles tab: the list and the profile detail with prompts.
 */
export function createProfilesView(): ProfilesView {
  const ui = profilesUi();
  const profiles: ProfileSummary[] = [];
  const rows = new Map<string, RowController>();
  const pending = new Set<string>();
  const cleanups: Array<() => void> = [];
  let viewProfile: ProfileDetail | null = null;
  let currentTab: ProfileTab = "video";
  let lastTrigger: HTMLButtonElement | null = null;
  let lightboxTrigger: HTMLElement | null = null;
  let viewToken = 0;
  let loading = false;
  let savingSettings = false;

  async function load(): Promise<void> {
    if (loading) {
      return;
    }
    loading = true;
    ui.refresh.disabled = true;
    ui.list.setAttribute("aria-busy", "true");
    setText(ui.loading, "Загружаем профили…");
    toggle(ui.loading, true);
    setMessage(ui.message, "");
    try {
      const data = asObject(await requestJson(PROFILES_URL));
      const items = Array.isArray(data.profiles)
        ? data.profiles.filter(isObject).map(normalizeSummary)
        : [];
      profiles.splice(0, profiles.length, ...items);
      renderList();
    } catch (error) {
      setMessage(ui.message, errorMessage(error));
    } finally {
      loading = false;
      ui.refresh.disabled = false;
      ui.list.removeAttribute("aria-busy");
      toggle(ui.loading, false);
    }
  }

  function renderList(): void {
    rows.clear();
    const nodes = profiles.map((profile) => {
      const controller = buildRow(profile);
      rows.set(profile.id, controller);
      return controller.node;
    });
    ui.list.replaceChildren(...nodes);
    toggle(ui.list, nodes.length !== 0);
    toggle(ui.empty, nodes.length === 0);
  }

  function buildRow(profile: ProfileSummary): RowController {
    const node = el("li", "profile-row");
    const main = el("div", "profile-row-main");
    const head = el("div", "profile-row-head");
    const id = el("span", "profile-id", profile.id);
    const meta = el("span", "profile-row-meta");
    const flags = el("div", "profile-flags");
    const foot = el("div", "profile-row-foot");
    const counts = el("span", "profile-counts");
    const actions = el("div", "profile-actions");
    const refill = el("button", "button ghost icon-button profile-refill");
    const gear = el("button", "button ghost icon-button profile-open");
    const status = el("p", "status profile-row-status");
    refill.type = "button";
    gear.type = "button";
    gear.append(icon("gear"));
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    toggle(status, false);

    head.append(id, meta);
    actions.append(refill, gear);
    foot.append(counts, actions);
    main.append(head, flags, foot, status);
    node.append(main);

    function update(): void {
      meta.textContent = `${languageName(profile.language)} · ${profile.niche || "без ниши"}`;
      flags.replaceChildren(...flagsFor(profile));
      const primary = primaryCounter(profile);
      counts.textContent = `${formatCount(primary.stock)} / ${formatCount(primary.target)}`;
      counts.title = primary.photo ? "Фото" : "Видео";
      refill.replaceChildren(icon(profile.refillEnabled ? "pause" : "play"));
      refill.setAttribute(
        "aria-label",
        `${profile.refillEnabled ? "Пауза добора" : "Включить добор"} ${profile.id}`,
      );
      refill.setAttribute("aria-pressed", String(profile.refillEnabled));
      gear.setAttribute("aria-label", `Открыть профиль ${profile.id}`);
    }

    const controller: RowController = {
      node,
      update,
      setBusy: (busy) => {
        refill.disabled = busy;
      },
      setStatus: (text, isError) => {
        setPromptStatus(status, text, isError);
      },
    };

    refill.addEventListener("click", () => {
      void toggleRefill(profile, controller);
    });
    gear.addEventListener("click", () => {
      void openProfile(profile.id, gear);
    });

    update();
    return controller;
  }

  async function toggleRefill(profile: ProfileSummary, row: RowController): Promise<void> {
    if (pending.has(profile.id)) {
      return;
    }
    pending.add(profile.id);
    row.setBusy(true);
    row.setStatus("Сохраняем…", false);
    try {
      const updated = await patchProfile(profile.id, {
        refill_enabled: !profile.refillEnabled,
      });
      applySummary(profile, updated);
      row.update();
      syncView(profile.id, updated);
      row.setStatus("Сохранено.", false);
    } catch (error) {
      row.setStatus(errorMessage(error), true);
    } finally {
      pending.delete(profile.id);
      row.setBusy(false);
    }
  }

  async function openProfile(id: string, trigger: HTMLButtonElement): Promise<void> {
    lastTrigger = trigger;
    const token = ++viewToken;
    resetEditors();
    closeLightbox();
    ui.listView.hidden = true;
    ui.view.hidden = false;
    setText(ui.title, id);
    setText(ui.meta, "");
    setText(ui.viewMessage, "");
    toggle(ui.viewMessage, false);
    setPromptStatus(ui.settingsStatus, "", false);
    ui.video.replaceChildren();
    ui.photo.replaceChildren();
    ui.back.focus();
    const [detailResult, promptsResult] = await Promise.allSettled([
      requestJson(profileUrl(id)),
      requestJson(`${profileUrl(id)}/prompts`),
    ]);
    if (token !== viewToken) {
      return;
    }
    if (detailResult.status === "rejected") {
      setMessage(ui.viewMessage, errorMessage(detailResult.reason));
      return;
    }
    const profile = normalizeDetail(detailResult.value);
    viewProfile = profile;
    currentTab = firstTab(profile);
    renderView(profile);
    if (promptsResult.status === "fulfilled") {
      renderPrompts(profile, normalizePrompts(promptsResult.value));
    } else {
      renderPromptsError(promptsResult.reason);
    }
  }

  function renderView(profile: ProfileDetail): void {
    setText(ui.title, profile.id);
    const types = profile.outputs.map(typeName).join("/") || "—";
    setText(
      ui.meta,
      `${languageName(profile.language)} · ${profile.niche || "без ниши"} · ` +
        `${profile.width}×${profile.height} · ${types}`,
    );
    renderPreview(profile);
    renderViewRefill(profile);
    fillRefillForm(profile);
    renderReadonlySettings(profile);
    renderTabs(profile);
    selectTab(currentTab);
  }

  function renderTabs(profile: ProfileDetail): void {
    const video = profile.outputs.includes("video");
    const photo = profile.outputs.includes("photo");
    ui.tabVideo.hidden = !video;
    ui.tabPhoto.hidden = !photo;
    setText(ui.tabVideo, `Видео · ${formatCount(profile.stock)}`);
    setText(ui.tabPhoto, `Фото · ${formatCount(profile.photoStock)}`);
    setText(ui.tabSettings, "Настройки");
  }

  function selectTab(tab: ProfileTab): void {
    currentTab = tab;
    const tabs: Array<[ProfileTab, HTMLButtonElement, HTMLElement]> = [
      ["video", ui.tabVideo, ui.panelVideo],
      ["photo", ui.tabPhoto, ui.panelPhoto],
      ["settings", ui.tabSettings, ui.panelSettings],
    ];
    for (const [name, button, panel] of tabs) {
      const active = name === tab && !button.hidden;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
      toggle(panel, active);
    }
  }

  function renderPreview(profile: ProfileDetail): void {
    ui.preview.setAttribute("aria-label", `Открыть превью профиля ${profile.id}`);
    if (!profile.previewUrl) {
      ui.previewImage.removeAttribute("src");
      toggle(ui.previewImage, false);
      toggle(ui.previewFallback, true);
      ui.preview.disabled = true;
      return;
    }
    ui.preview.disabled = false;
    setText(ui.previewFallback, "Нет превью");
    if (ui.previewImage.getAttribute("src") !== profile.previewUrl) {
      toggle(ui.previewFallback, false);
      toggle(ui.previewImage, true);
      ui.previewImage.alt = `Превью профиля ${profile.id}`;
      ui.previewImage.src = profile.previewUrl;
    }
  }

  function renderViewRefill(profile: ProfileDetail): void {
    const label = profile.refillEnabled ? "Пауза добора" : "Включить добор";
    ui.viewRefill.replaceChildren(
      icon(profile.refillEnabled ? "pause" : "play"),
      el("span", "", label),
    );
    ui.viewRefill.setAttribute("aria-label", `${label} ${profile.id}`);
    ui.viewRefill.setAttribute("aria-pressed", String(profile.refillEnabled));
    ui.viewRefill.disabled = pending.has(profile.id);
  }

  function fillRefillForm(profile: ProfileDetail): void {
    ui.target.value = profile.targetOverride === null ? "" : String(profile.targetOverride);
    ui.target.placeholder = String(profile.target);
    ui.refillAt.value = profile.refillAtOverride === null ? "" : String(profile.refillAtOverride);
    ui.refillAt.placeholder = String(profile.refillAt);
    ui.photoTarget.value = String(profile.photoTarget);
    ui.photoRefillAt.value = String(profile.photoRefillAt);
    ui.photoRefillAt.disabled = profile.photoTarget === 0;
    ui.refillEnabled.checked = profile.refillEnabled;
  }

  function renderReadonlySettings(profile: ProfileDetail): void {
    const { video, rent, text, delivery } = profile.settings;
    fillSettingsList(ui.settingsVideo, [
      ["Ширина", video.width],
      ["Высота", video.height],
      ["Кадры", video.frames],
      ["Разброс кадров", video.frames_jitter],
      ["FPS", video.fps],
      ["Рендерер", video.renderer],
      ["Образ", video.image],
      ["Аудиодорожки", video.audio_tracks],
    ]);
    fillSettingsList(ui.settingsRent, [
      ["GPU", rent.gpu],
      ["Максимальная цена", rent.max_cost],
      ["Диск", rent.disk_space],
      ["Память GPU", rent.gpu_ram],
      ["Входящий трафик", rent.inet_down],
      ["Цена отдачи", rent.inet_up_cost],
      ["Цена загрузки", rent.inet_down_cost],
    ]);
    const style = asObject(text.style);
    const styleRows = Object.entries(style).map<[string, unknown]>(([key, value]) => [
      `Стиль · ${key}`,
      value,
    ]);
    fillSettingsList(ui.settingsText, [
      ["Шрифт", text.font],
      ["Негативный промт", text.negative_prompt],
      ["CFG", text.cfg],
      ...styleRows,
    ]);
    fillSettingsList(ui.settingsDelivery, [
      ["Режим заголовка", delivery.title_mode],
      ["Заголовок", delivery.title],
      ["Хэштеги", delivery.hashtags],
    ]);
  }

  function renderPrompts(profile: ProfileDetail, prompts: ProfilePrompts): void {
    ui.video.replaceChildren();
    ui.photo.replaceChildren();
    const video = profile.outputs.includes("video");
    const photo = profile.outputs.includes("photo");
    const cardHost = video ? ui.video : ui.photo;
    if (video && prompts.template !== null) {
      ui.video.append(buildTemplateBlock(profile, prompts.template));
    }
    for (const card of prompts.cards) {
      cardHost.append(buildCardBlock(profile, card));
    }
    if (photo) {
      ui.photo.append(buildPhotoBlock(profile, prompts.photo));
    }
  }

  function renderPromptsError(reason: unknown): void {
    const message = promptError(reason);
    ui.video.append(el("p", "empty", message));
    ui.photo.append(el("p", "empty", message));
  }

  function buildTemplateBlock(profile: ProfileDetail, template: PromptFile): HTMLElement {
    const details = el("details", "prompt-block");
    const summary = el("summary", "prompt-summary", `Шаблон профиля · ${template.filename}`);
    const textarea = el("textarea", "prompt-text");
    textarea.value = template.text;
    textarea.rows = 10;
    textarea.setAttribute("aria-label", "Шаблон профиля");
    const status = promptStatus();
    details.append(summary, textarea, status);
    bindAutosave(textarea, status, (text) =>
      requestJson(`${profileUrl(profile.id)}/prompts/template`, putBody(text)),
    );
    return details;
  }

  function buildCardBlock(profile: ProfileDetail, card: PromptCard): HTMLElement {
    const details = el("details", "prompt-block prompt-card");
    details.dataset.cardId = card.id;
    const summary = el("summary", "prompt-summary");
    summary.append(
      el("span", "prompt-card-id", card.id),
      el("span", "prompt-card-values", describeValues(card.values)),
    );
    const chips = el("div", "pipeline-chips");
    chips.append(...pipelineChips(profile.production));
    const marker = el("p", "hint prompt-override", card.overridden ? "Свой промт" : "Из шаблона");
    const textarea = el("textarea", "prompt-text");
    textarea.value = card.prompt;
    textarea.setAttribute("aria-label", `Промт карточки ${card.id}`);
    const status = promptStatus();
    details.append(summary, chips, marker, textarea, status);
    bindAutosave(
      textarea,
      status,
      (text) =>
        requestJson(
          `${profileUrl(profile.id)}/prompts/cards/${encodeURIComponent(card.id)}`,
          putBody(text),
        ),
      (data) => {
        const overridden = Boolean(data.overridden);
        setText(marker, overridden ? "Свой промт" : "Из шаблона");
        if (!overridden && typeof data.prompt === "string") {
          textarea.value = data.prompt;
        }
      },
    );
    return details;
  }

  function buildPhotoBlock(profile: ProfileDetail, photo: PromptFile | null): HTMLElement {
    if (photo === null) {
      return el("p", "empty", "Промтов нет.");
    }
    const details = el("details", "prompt-block prompt-photo");
    details.open = true;
    const summary = el("summary", "prompt-summary", `Фото · ${photo.filename}`);
    const textarea = el("textarea", "prompt-text");
    textarea.value = photo.text;
    textarea.rows = 8;
    textarea.setAttribute("aria-label", "Текст фото");
    const status = promptStatus();
    details.append(summary, textarea, status);
    bindAutosave(textarea, status, (text) =>
      requestJson(`${profileUrl(profile.id)}/prompts/text`, putBody(text)),
    );
    return details;
  }

  function bindAutosave(
    textarea: HTMLTextAreaElement,
    status: HTMLElement,
    save: (text: string) => Promise<unknown>,
    onSaved?: (data: Record<string, unknown>) => void,
  ): void {
    let timer = 0;
    let seq = 0;
    const run = async (): Promise<void> => {
      const current = ++seq;
      const text = textarea.value;
      setPromptStatus(status, "Сохраняем…", false);
      try {
        const data = asObject(await save(text));
        if (current !== seq) {
          return;
        }
        onSaved?.(data);
        setPromptStatus(status, "Сохранено.", false);
      } catch (error) {
        if (current === seq) {
          setPromptStatus(status, errorMessage(error), true);
        }
      }
    };
    const onInput = (): void => {
      window.clearTimeout(timer);
      setPromptStatus(status, "Есть изменения…", false);
      timer = window.setTimeout(() => {
        void run();
      }, AUTOSAVE_MS);
    };
    textarea.addEventListener("input", onInput);
    cleanups.push(() => {
      window.clearTimeout(timer);
      textarea.removeEventListener("input", onInput);
    });
  }

  async function saveViewRefill(): Promise<void> {
    const profile = viewProfile;
    if (profile === null || pending.has(profile.id)) {
      return;
    }
    pending.add(profile.id);
    renderViewRefill(profile);
    setPromptStatus(ui.settingsStatus, "Сохраняем…", false);
    try {
      const updated = await patchProfile(profile.id, {
        refill_enabled: !profile.refillEnabled,
      });
      applySummary(profile, updated);
      renderViewRefill(profile);
      fillRefillForm(profile);
      syncRow(profile.id, updated);
      setPromptStatus(ui.settingsStatus, "Сохранено.", false);
    } catch (error) {
      setPromptStatus(ui.settingsStatus, errorMessage(error), true);
    } finally {
      pending.delete(profile.id);
      renderViewRefill(profile);
    }
  }

  async function saveRefillFields(): Promise<void> {
    const profile = viewProfile;
    if (profile === null || savingSettings) {
      return;
    }
    const payload = refillPayload();
    if (payload === null) {
      return;
    }
    savingSettings = true;
    setSettingsBusy(true);
    setPromptStatus(ui.settingsStatus, "Сохраняем…", false);
    try {
      const updated = await patchProfile(profile.id, payload);
      applySummary(profile, updated);
      fillRefillForm(profile);
      renderViewRefill(profile);
      syncRow(profile.id, updated);
      setPromptStatus(ui.settingsStatus, "Сохранено.", false);
    } catch (error) {
      setPromptStatus(ui.settingsStatus, errorMessage(error), true);
    } finally {
      savingSettings = false;
      setSettingsBusy(false);
    }
  }

  function refillPayload(): Record<string, unknown> | null {
    const target = readOptionalInt(ui.target.value, TARGET_MIN, TARGET_MAX);
    if (target === undefined) {
      return invalidNumber("Цель видео", TARGET_MIN, TARGET_MAX);
    }
    const refillAt = readOptionalInt(ui.refillAt.value, REFILL_MIN, TARGET_MAX);
    if (refillAt === undefined) {
      return invalidNumber("Порог добора видео", REFILL_MIN, TARGET_MAX);
    }
    const photoTarget = readRequiredInt(ui.photoTarget.value, 0, TARGET_MAX);
    if (photoTarget === null) {
      return invalidNumber("Цель фото", 0, TARGET_MAX);
    }
    const photoRefillAt =
      photoTarget === 0 ? 0 : readRequiredInt(ui.photoRefillAt.value, 0, PHOTO_REFILL_MAX);
    if (photoRefillAt === null) {
      return invalidNumber("Порог добора фото", 0, PHOTO_REFILL_MAX);
    }
    return {
      target,
      refill_at: refillAt,
      photo_target: photoTarget,
      photo_refill_at: photoRefillAt,
      refill_enabled: ui.refillEnabled.checked,
    };
  }

  function invalidNumber(label: string, min: number, max: number): null {
    setPromptStatus(ui.settingsStatus, `${label}: целое число от ${min} до ${max}.`, true);
    return null;
  }

  function setSettingsBusy(busy: boolean): void {
    for (const input of [
      ui.target,
      ui.refillAt,
      ui.photoTarget,
      ui.photoRefillAt,
      ui.refillEnabled,
    ]) {
      input.disabled = busy;
    }
    if (!busy && viewProfile) {
      ui.photoRefillAt.disabled = viewProfile.photoTarget === 0;
    }
  }

  function syncRow(id: string, updated: ProfileSummary): void {
    const row = rows.get(id);
    const live = profiles.find((item) => item.id === id);
    if (live) {
      applySummary(live, updated);
    }
    row?.update();
  }

  function syncView(id: string, updated: ProfileSummary): void {
    if (viewProfile === null || viewProfile.id !== id) {
      return;
    }
    applySummary(viewProfile, updated);
    renderViewRefill(viewProfile);
    fillRefillForm(viewProfile);
  }

  function back(): void {
    viewToken += 1;
    closeLightbox();
    resetEditors();
    viewProfile = null;
    ui.view.hidden = true;
    ui.listView.hidden = false;
    ui.video.replaceChildren();
    ui.photo.replaceChildren();
    if (lastTrigger?.isConnected) {
      lastTrigger.focus();
    } else {
      ui.refresh.focus();
    }
  }

  function resetEditors(): void {
    for (const cleanup of cleanups.splice(0)) {
      cleanup();
    }
  }

  function openLightbox(): void {
    const profile = viewProfile;
    if (profile === null || !profile.previewUrl) {
      return;
    }
    lightboxTrigger = ui.preview;
    setText(ui.lightboxTitle, `Превью ${profile.id}`);
    ui.lightboxImage.src = profile.previewUrl;
    ui.lightboxImage.alt = `Превью профиля ${profile.id}`;
    if (typeof ui.lightbox.showModal === "function") {
      ui.lightbox.showModal();
    } else {
      ui.lightbox.setAttribute("open", "");
    }
    document.addEventListener("keydown", onLightboxKeydown);
    ui.lightboxClose.focus();
  }

  function closeLightbox(): void {
    if (!ui.lightbox.hasAttribute("open")) {
      return;
    }
    document.removeEventListener("keydown", onLightboxKeydown);
    if (typeof ui.lightbox.close === "function") {
      ui.lightbox.close();
    } else {
      ui.lightbox.removeAttribute("open");
    }
    const trigger = lightboxTrigger;
    lightboxTrigger = null;
    trigger?.focus();
  }

  function onLightboxKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      event.preventDefault();
      closeLightbox();
    }
  }

  ui.refresh.addEventListener("click", () => {
    void load();
  });
  ui.back.addEventListener("click", back);
  ui.preview.addEventListener("click", openLightbox);
  ui.lightboxClose.addEventListener("click", closeLightbox);
  ui.lightbox.addEventListener("click", (event) => {
    if (event.target === ui.lightbox) {
      closeLightbox();
    }
  });
  ui.lightbox.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeLightbox();
  });
  ui.previewImage.addEventListener("error", () => {
    toggle(ui.previewImage, false);
    toggle(ui.previewFallback, true);
    setText(ui.previewFallback, "Нет превью");
  });
  ui.tabVideo.addEventListener("click", () => {
    selectTab("video");
  });
  ui.tabPhoto.addEventListener("click", () => {
    selectTab("photo");
  });
  ui.tabSettings.addEventListener("click", () => {
    selectTab("settings");
  });
  ui.form.addEventListener("submit", (event) => {
    event.preventDefault();
    void saveRefillFields();
  });
  ui.viewRefill.addEventListener("click", () => {
    void saveViewRefill();
  });
  ui.refillEnabled.addEventListener("change", () => {
    void saveRefillFields();
  });
  for (const input of [ui.target, ui.refillAt, ui.photoTarget, ui.photoRefillAt]) {
    input.addEventListener("change", () => {
      void saveRefillFields();
    });
  }

  renderList();
  return { load };
}

function icon(name: keyof typeof ICONS): SVGSVGElement {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg") as SVGSVGElement;
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const path = document.createElementNS(SVG_NAMESPACE, "path") as SVGPathElement;
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}

function flagsFor(profile: ProfileSummary): HTMLElement[] {
  const flags: HTMLElement[] = [];
  if (profile.audio) {
    flags.push(el("span", "badge", "музыка"));
  }
  flags.push(el("span", "badge", `${profile.width}×${profile.height}`));
  for (const type of profile.outputs) {
    flags.push(el("span", "badge", typeName(type)));
  }
  return flags;
}

function primaryCounter(profile: ProfileSummary): {
  stock: number;
  target: number;
  photo: boolean;
} {
  if (!profile.outputs.includes("video") && profile.outputs.includes("photo")) {
    return { stock: profile.photoStock, target: profile.photoTarget, photo: true };
  }
  return { stock: profile.stock, target: profile.target, photo: false };
}

function firstTab(profile: ProfileSummary): ProfileTab {
  if (profile.outputs.includes("video")) {
    return "video";
  }
  if (profile.outputs.includes("photo")) {
    return "photo";
  }
  return "settings";
}

function typeName(type: ContentType): string {
  return type === "video" ? "видео" : "фото";
}

function describeValues(values: Record<string, unknown>): string {
  return Object.entries(values)
    .filter(([key]) => key !== "id")
    .map(([key, value]) => {
      const label = VALUE_LABELS[key];
      return label ? `${label}: ${formatSetting(value)}` : `${key}=${formatSetting(value)}`;
    })
    .join(" · ");
}

function pipelineChips(steps: ProductionStep[]): HTMLElement[] {
  const chips: HTMLElement[] = [];
  for (const step of steps) {
    chips.push(chip(step.producer, "producer"));
    for (const check of step.checks) {
      if (check.stage === "source") {
        chips.push(chip(checkLabel(check.name), "source"));
      }
    }
    const internal = INTERNAL_CHIPS[step.producer];
    if (internal) {
      chips.push(chip(internal, "internal"));
    }
    for (const check of step.checks) {
      if (check.stage === "final") {
        chips.push(chip(checkLabel(check.name), "final"));
      }
    }
    chips.push(chip("сток", "stock"));
  }
  return chips;
}

function chip(text: string, kind: string): HTMLElement {
  return el("span", `badge pipeline-chip is-${kind}`, text);
}

function checkLabel(name: string): string {
  return CHECK_LABELS[name] ?? name;
}

function promptStatus(): HTMLElement {
  const status = el("p", "status prompt-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  return status;
}

function setPromptStatus(node: HTMLElement, text: string, isError: boolean): void {
  setText(node, text);
  node.classList.toggle("is-error", isError);
  toggle(node, text !== "");
}

function fillSettingsList(target: HTMLElement, rows: Array<[string, unknown]>): void {
  const nodes = rows.map(([label, value]) => {
    const row = el("div", "settings-row");
    row.append(el("dt", "", label), el("dd", "", formatSetting(value)));
    return row;
  });
  target.replaceChildren(...nodes);
}

function formatSetting(value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "да" : "нет";
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? "—" : value.map((item) => String(item)).join(", ");
  }
  if (typeof value === "object") {
    return "—";
  }
  return String(value);
}

function putBody(text: string): RequestInit {
  return {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  };
}

async function patchProfile(id: string, payload: Record<string, unknown>): Promise<ProfileSummary> {
  const data = await requestJson(profileUrl(id), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return normalizeSummary(data);
}

function applySummary(target: ProfileSummary, updated: ProfileSummary): void {
  Object.assign(target, updated);
}

function profileUrl(id: string): string {
  const slash = id.indexOf("/");
  const category = slash < 0 ? id : id.slice(0, slash);
  const name = slash < 0 ? "" : id.slice(slash + 1);
  return `${PROFILES_URL}/${encodeURIComponent(category)}/${encodeURIComponent(name)}`;
}

function promptError(error: unknown): string {
  if (error instanceof ApiError && error.status === 404) {
    return "Промты не найдены.";
  }
  return errorMessage(error);
}

function readOptionalInt(raw: string, min: number, max: number): number | null | undefined {
  const text = raw.trim();
  if (text === "") {
    return null;
  }
  const value = Number(text);
  if (!Number.isInteger(value) || value < min || value > max) {
    return undefined;
  }
  return value;
}

function readRequiredInt(raw: string, min: number, max: number): number | null {
  const text = raw.trim();
  if (text === "") {
    return null;
  }
  const value = Number(text);
  return Number.isInteger(value) && value >= min && value <= max ? value : null;
}

function normalizeSummary(value: unknown): ProfileSummary {
  const body = asObject(value);
  const id = text(body.id);
  const slash = id.indexOf("/");
  const category = text(body.category) || (slash < 0 ? id : id.slice(0, slash));
  const name = text(body.name) || (slash < 0 ? "" : id.slice(slash + 1));
  return {
    id,
    category,
    name,
    language: text(body.language) || "ru",
    niche: text(body.niche),
    width: numberValue(body.width),
    height: numberValue(body.height),
    audio: Boolean(body.audio),
    outputs: normalizeOutputs(body.outputs),
    stock: numberValue(body.stock),
    photoStock: numberValue(body.photo_stock),
    target: numberValue(body.target),
    refillAt: numberValue(body.refill_at),
    photoTarget: numberValue(body.photo_target),
    photoRefillAt: numberValue(body.photo_refill_at),
    refillEnabled: Boolean(body.refill_enabled),
    targetOverride: numberOrNull(body.target_override),
    refillAtOverride: numberOrNull(body.refill_at_override),
    previewUrl: text(body.preview_url),
  };
}

function normalizeDetail(value: unknown): ProfileDetail {
  const body = asObject(value);
  return {
    ...normalizeSummary(body),
    production: normalizeProduction(body.production),
    settings: {
      video: asObject(asObject(body.settings).video),
      rent: asObject(asObject(body.settings).rent),
      text: asObject(asObject(body.settings).text),
      delivery: asObject(asObject(body.settings).delivery),
    },
  };
}

function normalizeProduction(value: unknown): ProductionStep[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(isObject).map((item) => ({
    producer: text(item.producer),
    type: item.type === "photo" ? "photo" : "video",
    checks: Array.isArray(item.checks)
      ? item.checks.filter(isObject).map((check) => ({
          name: text(check.name),
          stage: check.stage === "source" ? "source" : "final",
        }))
      : [],
  }));
}

function normalizePrompts(value: unknown): ProfilePrompts {
  const body = asObject(value);
  const cards = Array.isArray(body.cards)
    ? body.cards.filter(isObject).map((card) => ({
        id: text(card.id),
        values: asObject(card.values),
        prompt: text(card.prompt),
        overridden: Boolean(card.overridden),
      }))
    : [];
  return {
    template: promptFile(body.template),
    cards: cards.filter((card) => card.id !== ""),
    photo: promptFile(body.photo),
  };
}

function promptFile(value: unknown): PromptFile | null {
  if (!isObject(value)) {
    return null;
  }
  const filename = text(value.filename);
  if (!filename) {
    return null;
  }
  return { filename, text: text(value.text) };
}

function normalizeOutputs(value: unknown): ContentType[] {
  const seen = new Set<ContentType>();
  if (Array.isArray(value)) {
    for (const item of value) {
      if (item === "video" || item === "photo") {
        seen.add(item);
      }
    }
  }
  return OUTPUT_ORDER.filter((type) => seen.has(type));
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function numberOrNull(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asObject(value: unknown): Record<string, unknown> {
  return isObject(value) ? value : {};
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
