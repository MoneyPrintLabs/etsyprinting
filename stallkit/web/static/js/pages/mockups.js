// Mockuplar (frames t170, t180) and the print-area editor (frame t190).
//
//   /kurulum/mockuplar         grid of mockup cards, type filter chips, upload by picker or drop
//   /kurulum/mockuplar/:name   editor: library list, canvas with the print-area rectangle,
//                              X / Y / width / height fields, same-size apply, preview design
//
// Everything is local (1-MOCKUPS in the products folder); no Etsy call is made, so the
// page works before any key is saved. Endpoints: stallkit/web/api/mockups.py.

import {
  badge,
  button,
  checkbox,
  chips,
  cx,
  debounce,
  dropzone,
  emptyState,
  field,
  filesFromDrop,
  h,
  iconButton,
  infoNote,
  menu,
  mount,
  progressBar,
  sectionTitle,
  select,
  skeleton,
  spinner,
  textInput,
  toggle,
} from "../ui.js";
import { icon } from "../icons.js";
import { percent } from "../format.js";

const IMAGE_RE = /\.(png|jpe?g|webp|gif|bmp|tiff?)$/i;
const ACCEPT = ".png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,image/png,image/jpeg,image/webp,image/gif,image/bmp,image/tiff";
const TYPES = ["tshirt", "sweatshirt", "hoodie", "mug", "poster", "canvas", "phone_case", "tote", "pillow", "sticker", "other"];
const APPLIED_KEY = "stallkit.mockups.applied";
const DESIGN_KEY = "stallkit.mockups.design";
const SHOW_KEY = "stallkit.mockups.show-design";
const ZOOMS = [1, 1.5, 2, 3, 4];
const IMAGE_MAX = 1400;
const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const SAMPLE = { id: "sample", version: "1" };
const GRID_PATH = "/kurulum/mockuplar";

const enc = encodeURIComponent;
const editorPath = (name) => `${GRID_PATH}/${enc(name)}`;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export default {
  async mount(el, ctx) {
    return ctx.params && ctx.params.name ? mountEditor(el, ctx, ctx.params.name) : mountGrid(el, ctx);
  },
};

// ------------------------------------------------------------------ small helpers

function store(kind) {
  try {
    return kind === "session" ? window.sessionStorage : window.localStorage;
  } catch {
    return null;
  }
}

function readStored(kind, key, fallback) {
  const s = store(kind);
  if (!s) return fallback;
  try {
    const raw = s.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function writeStored(kind, key, value) {
  const s = store(kind);
  if (!s) return;
  try {
    s.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode or full: a convenience only */
  }
}

function stem(name) {
  return String(name || "").replace(/\.[^.]+$/, "");
}

function typeLabel(t, type) {
  const key = `type.${type}`;
  return t.has(key) ? t(key) : String(type || "");
}

function colorLabel(t, color) {
  if (!color) return "";
  const key = `color.${color}`;
  return t.has(key) ? t(key) : color;
}

/** "Tişört · Beyaz"; a mockup of type "other" is called by its file name. */
function itemLabel(t, item) {
  const base = item.type === "other" ? stem(item.name) : typeLabel(t, item.type);
  const color = colorLabel(t, item.color);
  return color ? `${base} · ${color}` : base;
}

function sizeText(item) {
  return item.width && item.height ? `${item.width}×${item.height}` : "–";
}

function thumbUrl(ctx, item, w) {
  return ctx.api.url("/api/files/thumb", { path: item.path, w, v: item.version });
}

function designUrl(ctx, design, max) {
  return ctx.api.url("/api/mockups/design-image", { design: design.id, max, v: design.version || "1" });
}

function designLabel(t, design) {
  return !design || design.id === SAMPLE.id ? t("editor.sample") : design.label || design.name || design.id;
}

function sameArea(a, b) {
  if (!a || !b) return false;
  return ["x", "y", "w", "h"].every((k) => Math.abs(a[k] - b[k]) < 1e-5);
}

function roundArea(a) {
  const r = (v) => Math.round(v * 100000) / 100000;
  return { x: r(a.x), y: r(a.y), w: r(a.w), h: r(a.h) };
}

/** "%34,0" (tr) / "34.0%" (en) for a fraction. */
function pct(fraction) {
  return percent(fraction, 1);
}

/** Read what someone typed into a % field ("34,5", "%34.5", "34") -> 0..100 or null. */
function parsePercent(raw) {
  let s = String(raw ?? "").replace(/[%\s ]/g, "");
  if (!s) return null;
  if (s.includes(",") && !s.includes(".")) s = s.replace(",", ".");
  else s = s.replace(/,/g, "");
  const n = Number(s);
  return Number.isFinite(n) ? clamp(n, 0, 100) : null;
}

function pickFiles({ multiple = true } = {}) {
  return new Promise((resolve) => {
    const input = h("input", { type: "file", accept: ACCEPT, multiple, class: "sr-only", tabindex: "-1", "aria-hidden": "true" });
    const done = (files) => {
      input.remove();
      resolve(files);
    };
    input.addEventListener("change", () => done([...(input.files || [])]), { once: true });
    input.addEventListener("cancel", () => done([]), { once: true });
    document.body.appendChild(input);
    input.click();
  });
}

async function openFolder(ctx) {
  try {
    await ctx.api.post("/api/open-folder", { which: "mockups" });
  } catch (err) {
    if (!ctx.api.isAbort(err)) ctx.toast({ tone: "danger", title: ctx.t("open_folder_failed"), message: ctx.api.errorText(err, ctx.t) });
  }
}

/**
 * Upload files one by one (PUT /api/mockups/files). A progress toast for the batch, an
 * error toast per refused file, a success toast at the end. hooks: onStart, onProgress,
 * onDone(file, item), onFail(file, err). Resolves {added: [item], failed: [{file, err}]}.
 */
async function uploadBatch(ctx, list, hooks = {}) {
  const t = ctx.t;
  const images = list.filter((f) => IMAGE_RE.test((f && f.name) || ""));
  const skipped = list.length - images.length;
  if (skipped) ctx.toast({ tone: "warning", title: t("upload.skipped", { n: skipped }) });
  const result = { added: [], failed: [] };
  if (!images.length) return result;

  const msg = h("span", { class: "num" }, t("upload.progress", { done: 0, total: images.length }));
  const bar = progressBar({ value: 0, max: images.length, size: "sm", label: t("upload.title") });
  const note = ctx.toast({ tone: "info", title: t("upload.title"), message: h("span", { class: "mk-toast-body" }, msg, bar.el), timeout: 0 });
  const small = [];
  try {
    for (let i = 0; i < images.length; i += 1) {
      const file = images[i];
      if (hooks.onStart) hooks.onStart(file);
      try {
        const res = await ctx.api.upload("/api/mockups/files", file, {
          query: { name: file.name },
          signal: ctx.signal,
          onProgress: (p) => {
            bar.update(i + p.fraction);
            if (hooks.onProgress) hooks.onProgress(file, p.fraction);
          },
        });
        result.added.push(res.item);
        if (res.item && res.item.small) small.push(res.item.name);
        if (hooks.onDone) hooks.onDone(file, res.item);
      } catch (err) {
        if (ctx.api.isAbort(err)) throw err;
        result.failed.push({ file, err });
        if (hooks.onFail) hooks.onFail(file, err);
        ctx.toast({ tone: "danger", title: t("upload.failed", { name: file.name }), message: ctx.api.errorText(err, t), timeout: 9000 });
      }
      bar.update(i + 1);
      msg.textContent = t("upload.progress", { done: i + 1, total: images.length });
    }
  } finally {
    note.close();
  }
  if (result.added.length) {
    ctx.toast({ tone: "success", title: t("upload.done", { n: result.added.length }), message: t("upload.done_msg") });
  }
  if (small.length) {
    ctx.toast({ tone: "warning", title: t("upload.small_title"), message: t("upload.small", { names: small.join(", ") }), timeout: 9000 });
  }
  return result;
}

/**
 * Accept files dropped anywhere on the page (the element is a drop target for app.js).
 * Shows a full-area overlay while files are dragged over; ui.dropzone children handle
 * their own drops.
 */
function installPageDrop(el, ctx, onFiles) {
  el.dataset.dropTarget = "";
  const overlay = h(
    "div",
    { class: "mk-drop", "aria-hidden": "true" },
    h(
      "div",
      { class: "mk-drop-box" },
      h("span", { class: "mk-drop-icon" }, icon("upload", { size: 26 })),
      h("p", { class: "mk-drop-title" }, ctx.t("drop.title")),
      h("p", { class: "mk-drop-sub" }, ctx.t("drop.sub")),
    ),
  );
  overlay.hidden = true;
  el.appendChild(overlay);
  let timer = null;
  const hasFiles = (e) => !!e.dataTransfer && [...(e.dataTransfer.types || [])].includes("Files");
  const inZone = (e) => !!(e.target && e.target.closest && e.target.closest(".dropzone"));
  const onOver = (e) => {
    if (!hasFiles(e) || inZone(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    overlay.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(() => {
      overlay.hidden = true;
    }, 200);
  };
  const onDrop = async (e) => {
    if (!hasFiles(e) || inZone(e) || e.defaultPrevented) return;
    e.preventDefault();
    clearTimeout(timer);
    overlay.hidden = true;
    const items = await filesFromDrop(e.dataTransfer);
    onFiles(items.map((x) => x.file));
  };
  el.addEventListener("dragenter", onOver);
  el.addEventListener("dragover", onOver);
  el.addEventListener("drop", onDrop);
  return () => clearTimeout(timer);
}

/** The type / colour modal. Resolves with the updated item, or null. */
function editMeta(ctx, item) {
  const t = ctx.t;
  return new Promise((resolve) => {
    let result = null;
    const typeSel = select({ options: TYPES.map((ty) => ({ value: ty, label: typeLabel(t, ty) })), value: item.type, ariaLabel: t("meta.type") });
    const colorIn = textInput({ value: item.color || "", placeholder: t("meta.color_placeholder"), maxLength: 40 });
    const colorField = field({ label: t("meta.color"), input: colorIn, hint: t("meta.hint") });
    const m = ctx.modal({
      title: t("meta.title"),
      subtitle: item.name,
      width: 440,
      body: h("div", { class: "stack mk-meta" }, field({ label: t("meta.type"), input: typeSel }), colorField),
      actions: [
        { label: t("common.cancel"), variant: "secondary" },
        {
          label: t("common.save"),
          variant: "primary",
          icon: "check",
          autoLoading: true,
          onClick: async ({ close }) => {
            try {
              const res = await ctx.api.patch(`/api/mockups/${enc(item.name)}`, { type: typeSel.value, color: colorIn.value.trim() });
              result = res.item;
              close();
            } catch (err) {
              colorField.setError(ctx.api.errorText(err, t));
            }
          },
        },
      ],
      onClose: () => resolve(result),
    });
    colorIn.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const save = m.el.querySelector(".modal-actions .btn-primary");
        if (save) save.click();
      }
    });
  });
}

async function deleteMockup(ctx, item) {
  const t = ctx.t;
  const ok = await ctx.confirm({
    title: t("delete.title"),
    message: t("delete.message", { label: itemLabel(t, item), name: item.name }),
    confirmLabel: t("menu.delete"),
    danger: true,
  });
  if (!ok) return false;
  try {
    await ctx.api.del(`/api/mockups/${enc(item.name)}`);
    ctx.toast({ tone: "success", title: t("delete.done"), message: item.name });
    return true;
  } catch (err) {
    if (!ctx.api.isAbort(err)) ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t) });
    return false;
  }
}

async function setEnabled(ctx, item, enabled) {
  const t = ctx.t;
  try {
    const res = await ctx.api.patch(`/api/mockups/${enc(item.name)}`, { enabled });
    ctx.toast({ tone: enabled ? "success" : "info", title: t(enabled ? "enabled.on" : "enabled.off"), message: itemLabel(t, res.item), timeout: 3000 });
    return res.item;
  } catch (err) {
    if (!ctx.api.isAbort(err)) ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t) });
    return null;
  }
}

function areaState(t, item) {
  if (item.area_source === "own") {
    return h("span", { class: "mk-state is-own", title: t("area.own_hint") }, t("area.own"), icon("check", { size: 12, strokeWidth: 2.4 }));
  }
  if (item.area_source === "same_size") return h("span", { class: "mk-state is-shared", title: t("area.same_size_hint") }, t("area.same_size"));
  return h("span", { class: "mk-state is-default", title: t("area.default_hint") }, t("area.default"));
}

// ------------------------------------------------------------------ which mockups drafts use

/**
 * The one rule (catalog.usage on the server, the same for Tasarım Yükle and the run):
 * switched-on mockups in the seller's order; the first `max` are used, position 1 is the
 * draft's main image, the rest are over the limit. -> {pos: Map(name -> n), over: Set,
 * used, on, main}
 */
function plan(order, isOn, max) {
  const pos = new Map();
  const over = new Set();
  let on = 0;
  let main = null;
  for (const name of order) {
    if (!isOn(name)) continue;
    on += 1;
    if (on === 1) main = name;
    if (on <= max) pos.set(name, on);
    else over.add(name);
  }
  return { pos, over, used: Math.min(on, max), on, main };
}

/** `order` with `name` taken out and put before (or after) `target`. */
function placed(order, name, target, after = false) {
  const rest = order.filter((n) => n !== name);
  if (target === null) return [name, ...rest];
  const i = rest.indexOf(target);
  if (i < 0) return order.slice();
  rest.splice(after ? i + 1 : i, 0, name);
  return rest;
}

function sameList(a, b) {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

// ------------------------------------------------------------------ grid

async function mountGrid(el, ctx) {
  const t = ctx.t;
  el.classList.add("mk-page-grid");
  let data = null;
  let byName = new Map();
  let filter = ctx.query.type || "all";
  const uploads = new Map(); // File -> {el, bar}
  const images = new Map(); // name|version -> <img>, reused so re-renders do not flicker
  let chain = Promise.resolve();
  // Selection mode: a draft of the order and of which mockups are on, saved in one go.
  let sel = null; // {order: [names], on: Set, saving: bool}
  let drag = null;
  let suppressClick = false;
  const cleanups = [];

  const max = () => (data && data.max_enabled) || 19;
  const savedOrder = () => (data ? data.items.map((it) => it.name) : []);
  const order = () => (sel ? sel.order : savedOrder());
  const isOn = (name) => (sel ? sel.on.has(name) : !!(byName.get(name) || {}).enabled);
  const current = () => plan(order(), isOn, max());
  const selDirty = () => {
    if (!sel || !data) return false;
    if (!sameList(sel.order, savedOrder())) return true;
    return data.items.some((it) => it.enabled !== sel.on.has(it.name));
  };

  const addBtn = button({ label: t("add"), icon: "plus", variant: "primary", onClick: () => pickAndUpload() });
  const folderBtn = iconButton({ icon: "folder-open", title: t("open_folder"), variant: "ghost", onClick: () => openFolder(ctx) });
  ctx.setHeader({ actions: [folderBtn, addBtn] });

  const chipsCtl = chips({
    items: [{ id: "all", label: t("all") }],
    value: filter,
    ariaLabel: t("filter_label"),
    onChange: (id) => {
      filter = id;
      ctx.setQuery({ type: id === "all" ? null : id });
      renderGrid();
    },
  });
  const toolbar = h(
    "div",
    { class: "mk-toolbar" },
    chipsCtl.el,
    h("p", { class: "mk-autonote" }, icon("sparkles", { size: 14 }), h("span", null, t("auto_note"))),
  );
  const usageHost = h("section", { class: "mk-usage", "aria-label": t("usage.label") });
  // Selection tools sit under the counter and scroll away; the counter and Save stay.
  const toolsHost = h("div", { class: "mk-select-panel" });
  toolsHost.hidden = true;
  const notes = h("div", { class: "mk-notes" });
  const grid = h("div", { class: "mk-grid", role: "list" });
  el.append(toolbar, usageHost, toolsHost, notes, grid);
  const offDrop = installPageDrop(el, ctx, (files) => queueUpload(files));
  cleanups.push(offDrop);

  ctx.onBeforeLeave(async () => {
    if (!selDirty()) return true;
    const ok = await ctx.confirm({ title: t("select.unsaved_title"), message: t("select.unsaved_msg"), confirmLabel: t("editor.leave"), danger: true });
    if (ok) {
      sel = null;
      ctx.setDirty(false);
    }
    return ok;
  });

  function renderSkeleton() {
    toolbar.hidden = false;
    notes.hidden = true;
    usageHost.hidden = true;
    toolsHost.hidden = true;
    chipsCtl.update([{ id: "all", label: t("all") }], "all");
    mount(
      grid,
      Array.from({ length: 8 }, () =>
        h(
          "div",
          { class: "mk-card is-skeleton", "aria-hidden": "true" },
          h("div", { class: "mk-card-media" }, h("span", { class: "skeleton mk-sk-img" })),
          h("div", { class: "mk-card-foot" }, skeleton({ lines: 2, height: 11, widths: ["62%", "44%"] })),
        ),
      ),
    );
  }

  function renderError(err) {
    toolbar.hidden = true;
    notes.hidden = true;
    usageHost.hidden = true;
    toolsHost.hidden = true;
    mount(
      grid,
      h(
        "div",
        { class: "mk-grid-full" },
        emptyState({
          icon: "alert",
          title: t("load_error"),
          message: ctx.api.errorText(err, t),
          action: button({ label: t("common.retry"), icon: "refresh", onClick: () => load() }),
        }),
      ),
    );
  }

  function renderEmpty() {
    toolbar.hidden = true;
    notes.hidden = true;
    usageHost.hidden = true;
    toolsHost.hidden = true;
    const examples = [
      ["shirt", "tshirt"],
      ["mug", "mug"],
      ["frame", "poster"],
      ["phone", "phone_case"],
      ["bag", "tote"],
    ];
    const zone = dropzone({
      accept: ACCEPT,
      multiple: true,
      class: "mk-empty",
      onFiles: (list) => queueUpload(list.map((x) => x.file)),
      onReject: (bad) => ctx.toast({ tone: "warning", title: t("upload.skipped", { n: bad.length }) }),
      content: [
        h("span", { class: "mk-empty-icon" }, icon("image", { size: 26 })),
        h("p", { class: "mk-empty-title" }, t("empty.title")),
        h("p", { class: "mk-empty-sub" }, t("empty.sub")),
        h(
          "div",
          { class: "mk-empty-types", "aria-hidden": "true" },
          examples.map(([ic, ty]) => h("span", { class: "mk-empty-type" }, icon(ic, { size: 18 }), h("span", null, typeLabel(t, ty)))),
        ),
        h(
          "div",
          { class: "mk-empty-actions" },
          button({ label: t("empty.pick"), icon: "upload", variant: "primary", onClick: () => pickAndUpload() }),
          button({ label: t("open_folder"), icon: "folder-open", variant: "ghost", onClick: () => openFolder(ctx) }),
        ),
        h("p", { class: "mk-empty-what" }, t("empty.what")),
      ],
    });
    mount(grid, h("div", { class: "mk-grid-full" }, zone));
  }

  // ---- the counter, and the selection tools while choosing

  function renderUsage() {
    if (!data || !data.items.length) {
      usageHost.hidden = true;
      toolsHost.hidden = true;
      return;
    }
    usageHost.hidden = false;
    const p = current();
    const m = max();
    usageHost.classList.toggle("is-selecting", !!sel);
    usageHost.classList.toggle("is-over", p.on > m);
    const fill = h("span", { class: "mk-meter-fill", style: { width: `${Math.round((p.used / m) * 100)}%` } });
    const meter = h("span", { class: "mk-meter", role: "meter", "aria-valuemin": "0", "aria-valuemax": String(m), "aria-valuenow": String(p.used), "aria-label": t("usage.count", { used: p.used, max: m }) }, fill);
    let sub;
    if (p.on > m) sub = h("p", { class: "mk-usage-sub is-over" }, icon("alert", { size: 13 }), h("span", null, t("usage.over", { n: p.on - m })));
    else if (!p.on) sub = h("p", { class: "mk-usage-sub is-none" }, icon("info", { size: 13 }), h("span", null, t("usage.none")));
    else {
      const main = byName.get(p.main);
      sub = h("p", { class: "mk-usage-sub" }, icon("star", { size: 13 }), h("span", null, t("usage.main", { label: main ? itemLabel(t, main) : p.main })));
    }
    const text = h(
      "div",
      { class: "mk-usage-text" },
      h("p", { class: "mk-usage-count" }, h("strong", { class: "num" }, t("usage.count", { used: p.used, max: m })), h("span", { class: "mk-usage-rule" }, ` — ${t("usage.rule")}`)),
      sub,
    );
    if (!sel) {
      const start = button({ label: t("select.start"), icon: "check-circle", variant: "secondary", class: "mk-select-start", onClick: () => enterSelect() });
      mount(usageHost, h("div", { class: "mk-usage-row" }, meter, text, h("div", { class: "spacer" }), start));
      toolsHost.hidden = true;
      mount(toolsHost);
      return;
    }
    const dirty = selDirty();
    const cancel = button({ label: t("common.cancel"), variant: "secondary", onClick: () => leaveSelect() });
    const save = button({ label: t("common.save"), icon: "check", variant: "primary", disabled: !dirty, loading: sel.saving, onClick: () => saveSelect() });
    const shown = visibleItems().map((it) => it.name);
    const allOn = shown.length > 0 && shown.every((n) => sel.on.has(n));
    const noneOn = shown.every((n) => !sel.on.has(n));
    const tools = h(
      "div",
      { class: "mk-select-tools" },
      button({ label: t("select.all"), icon: "check", size: "sm", variant: "ghost", disabled: allOn, onClick: () => setMany(shown, true) }),
      button({ label: t("select.none"), icon: "x", size: "sm", variant: "ghost", disabled: noneOn, onClick: () => setMany(shown, false) }),
      quickGroup("type"),
      quickGroup("color"),
    );
    mount(usageHost, h("div", { class: "mk-usage-row" }, meter, text, h("div", { class: "spacer" }), cancel, save));
    toolsHost.hidden = false;
    mount(toolsHost, tools, h("p", { class: "mk-select-hint" }, icon("move", { size: 13 }), h("span", null, t("select.hint"))));
    ctx.setDirty(dirty);
  }

  /** "Türe göre: Tişört 3/20 · Kupa 0/5": one click selects the whole group, again clears it. */
  function quickGroup(kind) {
    const groups = new Map();
    for (const name of order()) {
      const it = byName.get(name);
      if (!it) continue;
      const key = kind === "type" ? it.type : it.color;
      if (!key) continue;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(name);
    }
    if (groups.size < 2) return null;
    const label = (key) => (kind === "type" ? typeLabel(t, key) : colorLabel(t, key));
    const keys = [...groups.keys()];
    if (kind === "type") keys.sort((a, b) => typeOrder().indexOf(a) - typeOrder().indexOf(b));
    return h(
      "div",
      { class: "mk-qgroup", role: "group", "aria-label": t(kind === "type" ? "select.by_type" : "select.by_color") },
      h("span", { class: "mk-qgroup-label" }, t(kind === "type" ? "select.by_type" : "select.by_color")),
      keys.map((key) => {
        const names = groups.get(key);
        const on = names.filter((n) => sel.on.has(n)).length;
        const all = on === names.length;
        return h(
          "button",
          {
            type: "button",
            class: cx("mk-qchip", all && "is-all", on > 0 && !all && "is-some"),
            "aria-pressed": all ? "true" : on ? "mixed" : "false",
            title: t(all ? "select.group_clear" : "select.group_pick", { label: label(key), n: names.length }),
            onClick: () => setMany(names, !all),
          },
          all ? icon("check", { size: 12, strokeWidth: 2.6 }) : null,
          h("span", null, label(key)),
          h("span", { class: "mk-qchip-count num" }, `${on}/${names.length}`),
        );
      }),
    );
  }

  function setMany(names, on) {
    if (!sel) return;
    for (const n of names) {
      if (on) sel.on.add(n);
      else sel.on.delete(n);
    }
    renderGrid();
  }

  function enterSelect() {
    if (!data) return;
    sel = { order: savedOrder(), on: new Set(data.items.filter((it) => it.enabled).map((it) => it.name)), saving: false };
    renderGrid();
    usageHost.scrollIntoView({ block: "nearest" });
  }

  function leaveSelect() {
    sel = null;
    ctx.setDirty(false);
    renderGrid();
  }

  async function saveSelect() {
    if (!sel || sel.saving) return;
    sel.saving = true;
    renderUsage();
    try {
      const res = await ctx.api.post("/api/mockups/arrange", { order: sel.order, enabled: [...sel.on] }, { signal: ctx.signal });
      setData(res);
      sel = null;
      ctx.setDirty(false);
      renderGrid();
      const n = res.usage ? res.usage.used.length : 0;
      ctx.toast({ tone: "success", title: t("select.saved"), message: t("select.saved_msg", { n }), timeout: 3500 });
    } catch (err) {
      if (ctx.api.isAbort(err)) return;
      if (sel) sel.saving = false;
      renderUsage();
      ctx.toast({ tone: "danger", title: t("select.save_failed"), message: ctx.api.errorText(err, t) });
    }
  }

  // ---- notes above the grid

  function renderNotes() {
    const list = [];
    const d = data && data.default_area;
    if (d && d.count && d.first && !sel) {
      const first = byName.get(d.first);
      const how = d.first_same_size
        ? t("banner.same_size", { n: d.first_same_size, label: first ? itemLabel(t, first) : d.first })
        : t("banner.one_by_one");
      list.push(
        infoNote({
          tone: "warning",
          icon: "crop",
          text: [h("strong", null, t("banner.default", { n: d.count })), " ", h("span", null, how)],
          action: h("a", { class: "btn btn-primary btn-sm mk-banner-link", href: editorPath(d.first) }, h("span", { class: "btn-label" }, t("banner.fix")), icon("arrow-right", { size: 14 })),
        }),
      );
    }
    if (data && data.positions_error) {
      list.push(infoNote({ tone: "warning", icon: "alert", text: [t("positions_error"), " ", h("span", { class: "mono muted" }, data.positions_error)] }));
    }
    mount(notes, list);
    notes.hidden = !list.length;
  }

  function typeOrder() {
    return (data && data.type_order) || TYPES;
  }

  function renderChips() {
    const counts = new Map();
    for (const it of data.items) counts.set(it.type, (counts.get(it.type) || 0) + 1);
    const items = [{ id: "all", label: t("all"), count: data.items.length }];
    for (const ty of typeOrder()) if (counts.get(ty)) items.push({ id: ty, label: typeLabel(t, ty), count: counts.get(ty) });
    if (filter !== "all" && !counts.get(filter)) filter = "all";
    chipsCtl.update(items, filter);
  }

  function visibleItems() {
    if (!data) return [];
    return order()
      .map((n) => byName.get(n))
      .filter((it) => it && (filter === "all" || it.type === filter));
  }

  // ---- cards

  function thumbImg(item) {
    const key = `${item.name}|${item.version}`;
    let img = images.get(key);
    if (!img) {
      img = h("img", { src: thumbUrl(ctx, item, 600), alt: "", loading: "lazy", decoding: "async", draggable: "false" });
      img.addEventListener("error", () => img.replaceWith(h("span", { class: "mk-img-fallback" }, icon("image", { size: 26 }))), { once: true });
      images.set(key, img);
    }
    return img;
  }

  function positionMark(item, p) {
    const n = p.pos.get(item.name);
    if (n === 1) return h("span", { class: "mk-pos is-main", title: t("badge.main_hint") }, icon("star", { size: 11, strokeWidth: 2.2 }), h("span", null, t("badge.main")));
    if (n) return h("span", { class: "mk-pos num", title: t("badge.position", { n }), "aria-label": t("badge.position", { n }) }, String(n));
    if (p.over.has(item.name)) return h("span", { class: "mk-pos is-over", title: t("badge.over_hint", { max: max() }) }, icon("alert", { size: 11, strokeWidth: 2.2 }), h("span", null, t("badge.over")));
    return h("span", { class: "mk-pos is-off" }, icon("eye-off", { size: 11 }), h("span", null, t("unused")));
  }

  function cardFor(item, p) {
    const label = itemLabel(t, item);
    const on = isOn(item.name);
    const over = p.over.has(item.name);
    const more = iconButton({
      icon: "more",
      title: t("menu.more", { name: label }),
      variant: "ghost",
      size: "sm",
      class: "mk-more",
      onClick: (e) => {
        e.preventDefault();
        e.stopPropagation();
        openMenu(more, item);
      },
    });
    const setArea = h(
      "a",
      { class: cx("mk-set-area", item.area_source === "default" && "is-default"), href: editorPath(item.name), title: t(`area.${item.area_source}_hint`) },
      icon("crop", { size: 14 }),
      h("span", null, t("card.set_area")),
    );
    let check = null;
    if (sel) {
      check = checkbox({ checked: on, ariaLabel: t("select.use", { label }), onChange: (v) => setMany([item.name], v) });
      check.classList.add("mk-card-check");
    }
    return h(
      "div",
      {
        class: cx("mk-card", !on && "is-disabled", over && "is-over", sel && "is-selecting", sel && on && "is-selected"),
        role: "listitem",
        title: item.name,
        dataset: { name: item.name },
      },
      h("div", { class: "mk-card-media" }, thumbImg(item), h("span", { class: "mk-card-pos" }, positionMark(item, p)), check),
      h(
        "div",
        { class: "mk-card-foot" },
        h("div", { class: "mk-card-row" }, sel ? h("span", { class: "mk-card-title ellipsis" }, label) : h("a", { class: "mk-card-link ellipsis", href: editorPath(item.name) }, label), more),
        h(
          "p",
          { class: "mk-card-meta" },
          h("span", { class: "mono" }, sizeText(item)),
          h("span", { class: "mk-sep", "aria-hidden": "true" }, "·"),
          areaState(t, item),
        ),
        setArea,
      ),
    );
  }

  function uploadingCard(file) {
    const bar = progressBar({ value: 0, max: 1, size: "sm", label: file.name });
    const node = h(
      "div",
      { class: "mk-card is-uploading", role: "listitem" },
      h("div", { class: "mk-card-media" }, spinner({ size: 22, tone: "accent" })),
      h(
        "div",
        { class: "mk-card-foot" },
        h("div", { class: "mk-card-row" }, h("span", { class: "mk-card-title ellipsis" }, file.name)),
        h("p", { class: "mk-card-meta" }, h("span", null, t("uploading"))),
        bar.el,
      ),
    );
    return { el: node, bar };
  }

  function renderGrid() {
    if (!data) return;
    if (!data.items.length && !uploads.size) {
      if (sel) leaveSelect();
      renderEmpty();
      return;
    }
    toolbar.hidden = false;
    renderChips();
    renderUsage();
    renderNotes();
    el.classList.toggle("is-selecting", !!sel);
    const p = current();
    const m = max();
    const cards = [];
    for (const it of visibleItems()) {
      cards.push(cardFor(it, p));
      // Everything switched on after this card is over the limit: say so in the grid.
      if (p.over.size && p.pos.get(it.name) === m && filter === "all") {
        cards.push(h("div", { class: "mk-grid-full mk-limit-line", role: "separator" }, h("span", null, icon("alert", { size: 13 }), t("limit.line", { max: m, n: p.over.size }))));
      }
    }
    for (const u of uploads.values()) cards.push(u.el);
    if (!cards.length) cards.push(h("div", { class: "mk-grid-full" }, emptyState({ icon: "filter", title: t("filter_empty"), compact: true })));
    mount(grid, cards);
  }

  // ---- order: menu actions, drag in selection mode

  function neighbours(name) {
    const shown = visibleItems().map((it) => it.name);
    const i = shown.indexOf(name);
    return { prev: i > 0 ? shown[i - 1] : null, next: i >= 0 && i < shown.length - 1 ? shown[i + 1] : null };
  }

  async function reorder(next, toastTitle) {
    if (sameList(next, order())) return;
    if (sel) {
      sel.order = next;
      renderGrid();
      return;
    }
    try {
      setData(await ctx.api.post("/api/mockups/arrange", { order: next }, { signal: ctx.signal }));
      renderGrid();
      ctx.toast({ tone: "success", title: toastTitle || t("order.saved"), timeout: 2500 });
    } catch (err) {
      if (!ctx.api.isAbort(err)) ctx.toast({ tone: "danger", title: t("order.failed"), message: ctx.api.errorText(err, t) });
    }
  }

  function openMenu(anchor, item) {
    const label = itemLabel(t, item);
    const { prev, next } = neighbours(item.name);
    const p = current();
    const items = [{ label: t("menu.edit_area"), icon: "crop", onClick: () => ctx.navigate(editorPath(item.name)) }];
    if (!sel) {
      items.push({
        label: t("menu.use_in_drafts"),
        icon: "layers",
        checked: !!item.enabled,
        onClick: async () => {
          if (await setEnabled(ctx, item, !item.enabled)) await load({ quiet: true, warnOver: item.enabled ? [] : [item.name] });
        },
      });
    }
    items.push(
      { divider: true },
      { label: t("menu.make_main"), icon: "star", disabled: !isOn(item.name) || p.pos.get(item.name) === 1, onClick: () => reorder(placed(order(), item.name, null), t("order.main_done", { label })) },
      { label: t("menu.move_up"), icon: "arrow-left", disabled: !prev, onClick: () => reorder(placed(order(), item.name, prev)) },
      { label: t("menu.move_down"), icon: "arrow-right", disabled: !next, onClick: () => reorder(placed(order(), item.name, next, true)) },
    );
    if (!sel) {
      items.push(
        { divider: true },
        {
          label: t("menu.edit_meta"),
          icon: "tag",
          onClick: async () => {
            if (await editMeta(ctx, item)) load({ quiet: true });
          },
        },
        {
          label: t("menu.delete"),
          icon: "trash",
          danger: true,
          onClick: async () => {
            if (await deleteMockup(ctx, item)) load({ quiet: true });
          },
        },
      );
    }
    menu(anchor, items, { placement: "bottom-end", width: 230 });
  }

  function scroller() {
    return el.closest(".content") || document.scrollingElement || document.documentElement;
  }

  function clearMarks() {
    for (const c of grid.querySelectorAll(".drop-before, .drop-after")) c.classList.remove("drop-before", "drop-after");
  }

  function endDrag(commit) {
    const d = drag;
    drag = null;
    window.removeEventListener("pointermove", onDragMove);
    window.removeEventListener("pointerup", onDragUp);
    window.removeEventListener("pointercancel", onDragCancel);
    if (!d) return;
    cancelAnimationFrame(d.raf || 0);
    clearMarks();
    if (d.ghost) d.ghost.remove();
    if (d.card) d.card.classList.remove("is-dragging");
    el.classList.remove("is-dragging");
    if (d.started) {
      suppressClick = true;
      setTimeout(() => {
        suppressClick = false;
      }, 0);
    }
    if (commit && d.started && d.target && d.target !== d.name) reorder(placed(order(), d.name, d.target, d.after));
  }

  function onDragMove(e) {
    if (!drag || e.pointerId !== drag.id) return;
    drag.cx = e.clientX;
    drag.cy = e.clientY;
    if (!drag.started) {
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) < 6) return;
      drag.started = true;
      drag.card.classList.add("is-dragging");
      el.classList.add("is-dragging");
      const it = byName.get(drag.name);
      drag.ghost = h("div", { class: "mk-ghost", "aria-hidden": "true" }, it ? h("img", { src: thumbUrl(ctx, it, 160), alt: "" }) : null, h("span", null, it ? itemLabel(t, it) : drag.name));
      document.body.appendChild(drag.ghost);
      const tick = () => {
        if (!drag) return;
        const box = scroller().getBoundingClientRect();
        const edge = 70;
        const top = Math.max(box.top, 0);
        const bottom = Math.min(box.bottom, window.innerHeight);
        let dy = 0;
        if (drag.cy < top + edge) dy = -Math.ceil((top + edge - drag.cy) / 5);
        else if (drag.cy > bottom - edge) dy = Math.ceil((drag.cy - (bottom - edge)) / 5);
        if (dy) {
          scroller().scrollTop += dy;
          track();
        }
        drag.raf = requestAnimationFrame(tick);
      };
      drag.raf = requestAnimationFrame(tick);
    }
    e.preventDefault();
    track();
  }

  function track() {
    if (!drag || !drag.started) return;
    drag.ghost.style.transform = `translate(${drag.cx + 14}px, ${drag.cy + 12}px)`;
    const under = document.elementFromPoint(drag.cx, drag.cy);
    const card = under && under.closest ? under.closest(".mk-card[data-name]") : null;
    clearMarks();
    drag.target = null;
    if (!card || !grid.contains(card) || card.dataset.name === drag.name) return;
    const r = card.getBoundingClientRect();
    drag.after = drag.cx > r.left + r.width / 2;
    drag.target = card.dataset.name;
    card.classList.add(drag.after ? "drop-after" : "drop-before");
  }

  function onDragUp(e) {
    if (drag && e.pointerId === drag.id) endDrag(true);
  }

  function onDragCancel(e) {
    if (drag && e.pointerId === drag.id) endDrag(false);
  }

  grid.addEventListener("pointerdown", (e) => {
    if (!sel || e.button !== 0 || drag) return;
    const card = e.target.closest(".mk-card[data-name]");
    if (!card || e.target.closest("a, button, input, label")) return;
    drag = { name: card.dataset.name, card, id: e.pointerId, x: e.clientX, y: e.clientY, cx: e.clientX, cy: e.clientY, started: false, target: null, after: false };
    window.addEventListener("pointermove", onDragMove);
    window.addEventListener("pointerup", onDragUp);
    window.addEventListener("pointercancel", onDragCancel);
  });
  grid.addEventListener("click", (e) => {
    if (!sel) return;
    if (suppressClick) {
      e.preventDefault();
      return;
    }
    const card = e.target.closest(".mk-card[data-name]");
    if (!card || e.target.closest("a, button, input, label")) return;
    const name = card.dataset.name;
    setMany([name], !sel.on.has(name));
  });
  grid.addEventListener("dragstart", (e) => {
    if (sel) e.preventDefault();
  });
  cleanups.push(() => endDrag(false));

  // ---- data

  function setData(next) {
    data = next;
    byName = new Map(data.items.map((it) => [it.name, it]));
    for (const key of [...images.keys()]) {
      const [name, version] = key.split("|");
      const it = byName.get(name);
      if (!it || it.version !== version) images.delete(key);
    }
    if (sel) {
      // New uploads join the draft at the end; deleted mockups leave it.
      const known = new Set(data.items.map((it) => it.name));
      sel.order = sel.order.filter((n) => known.has(n));
      for (const it of data.items) {
        if (!sel.order.includes(it.name)) {
          sel.order.push(it.name);
          if (it.enabled) sel.on.add(it.name);
        }
      }
      for (const n of [...sel.on]) if (!known.has(n)) sel.on.delete(n);
    }
  }

  function addItem(item) {
    if (!data || !item) return;
    const items = data.items.filter((it) => it.name !== item.name);
    items.push(item);
    setData({ ...data, items });
  }

  function queueUpload(files) {
    const list = (files || []).filter((f) => f && IMAGE_RE.test(f.name || ""));
    for (const file of list) uploads.set(file, uploadingCard(file));
    if (list.length) renderGrid();
    chain = chain
      .then(() =>
        uploadBatch(ctx, files, {
          onProgress: (file, fraction) => {
            const u = uploads.get(file);
            if (u) u.bar.update(fraction);
          },
          onDone: (file, item) => {
            uploads.delete(file);
            addItem(item);
            renderGrid();
          },
          onFail: (file) => {
            uploads.delete(file);
            renderGrid();
          },
        }),
      )
      .then((res) => {
        if (ctx.isActive()) return load({ quiet: true, warnOver: res.added.filter(Boolean).map((it) => it.name) });
        return null;
      })
      .catch((err) => {
        if (!ctx.api.isAbort(err)) console.error("[mockups] upload failed", err);
      });
    return chain;
  }

  async function pickAndUpload() {
    const files = await pickFiles({ multiple: true });
    if (files.length && ctx.isActive()) queueUpload(files);
  }

  async function load({ quiet = false, warnOver = [] } = {}) {
    if (!quiet || !data) renderSkeleton();
    try {
      setData(await ctx.api.get("/api/mockups", null, { signal: ctx.signal }));
    } catch (err) {
      if (ctx.api.isAbort(err)) return;
      if (quiet && data) {
        ctx.toast({ tone: "danger", title: t("load_error"), message: ctx.api.errorText(err, t) });
        return;
      }
      renderError(err);
      return;
    }
    renderGrid();
    // Never silently: a mockup that was just switched on or added but does not fit says so.
    const over = warnOver.map((n) => byName.get(n)).filter((it) => it && it.over_limit);
    if (over.length === 1) ctx.toast({ tone: "warning", title: t("enabled.over_title"), message: t("enabled.over", { max: max(), label: itemLabel(t, over[0]) }), timeout: 8000 });
    else if (over.length) ctx.toast({ tone: "warning", title: t("enabled.over_title_many", { n: over.length }), message: t("enabled.over_many", { max: max() }), timeout: 8000 });
  }

  await load();
  return () => {
    for (const fn of cleanups) {
      try {
        fn();
      } catch {
        /* ignore */
      }
    }
  };
}

// ------------------------------------------------------------------ editor

async function mountEditor(el, ctx, name) {
  const t = ctx.t;
  el.classList.add("mk-page-editor");

  const shopKey = () => {
    const s = ctx.session && ctx.session();
    return `${APPLIED_KEY}:${(s && s.shop_id) || ""}`;
  };
  const applied = new Set(readStored("session", shopKey(), []));
  const storeApplied = () => writeStored("session", shopKey(), [...applied]);

  let list = [];
  let item = null;
  let saved = null;
  let area = null;
  let source = "default";
  let siblings = [];
  let sameSize = false;
  let lastApplied = null;
  let zoom = 1;
  let showDesign = readStored("local", SHOW_KEY, true) !== false;
  let design = readStored("local", DESIGN_KEY, null);
  let designs = null;
  let drag = null;
  let previewSeq = 0;
  let saving = false;
  let chain = Promise.resolve();
  let maxEnabled = 19;
  let noteKey = "";
  const cleanups = [];
  // Declared before anything can call markStale(), which cancels it.
  const schedulePreview = debounce(() => loadPreview(), 350);

  const isDirty = () => !!(saved && area && !sameArea(saved, area));
  const W = () => (item && item.width) || 1;
  const H = () => (item && item.height) || 1;

  const backBtn = iconButton({ icon: "arrow-left", title: t("back"), variant: "ghost", onClick: () => leave(GRID_PATH) });
  const addBtn = button({ label: t("add"), icon: "plus", variant: "primary", onClick: () => pickAndUpload() });
  ctx.setHeader({ actions: [backBtn, addBtn] });

  // ---- library (left)
  const libCount = h("span", { class: "mk-lib-count num" });
  const libList = h("div", { class: "mk-lib-list", role: "list" });
  const lib = h(
    "section",
    { class: "card mk-lib", "aria-label": t("library.title") },
    h("header", { class: "mk-lib-head" }, h("h2", { class: "mk-lib-title" }, t("library.title")), libCount),
    libList,
    h(
      "button",
      { type: "button", class: "mk-lib-new", onClick: () => pickAndUpload() },
      icon("plus", { size: 15 }),
      h("span", { class: "mk-lib-new-label" }, t("library.new")),
      h("span", { class: "mk-lib-new-sub" }, `· ${t("library.new_types")}`),
    ),
  );

  // ---- canvas (centre)
  const baseImg = h("img", { class: "mk-base", alt: "", draggable: "false" });
  let previewImg = h("img", { class: "mk-preview", alt: "", draggable: "false" });
  previewImg.hidden = true;
  const overlayImg = h("img", { class: "mk-rect-design", alt: "", draggable: "false" });
  const sizeChip = h("span", { class: "mk-rect-size num" });
  const rect = h(
    "div",
    { class: "mk-rect", tabindex: "0", role: "group" },
    overlayImg,
    h("span", { class: "mk-rect-label" }, t("editor.area_label")),
    sizeChip,
    HANDLES.map((hd) => h("span", { class: `mk-handle h-${hd}`, dataset: { handle: hd }, "aria-hidden": "true" })),
  );
  const art = h("div", { class: "mk-art" }, baseImg, previewImg, rect);
  const stageSpinner = h("div", { class: "mk-stage-wait" }, spinner({ size: 22, tone: "accent" }));
  const stage = h("div", { class: "mk-stage" }, art);
  const zoomVal = h("span", { class: "num" }, percent(1, 0));
  const zoomBtn = h(
    "button",
    { type: "button", class: "mk-zoom", title: t("editor.zoom"), "aria-label": t("editor.zoom"), onClick: () => openZoomMenu() },
    icon("zoom", { size: 13 }),
    zoomVal,
  );
  const showBtn = h(
    "button",
    { type: "button", class: "mk-show", "aria-pressed": "true", onClick: () => setShowDesign(!showDesign) },
    icon("eye", { size: 13 }),
    h("span", null, t("editor.show_design")),
  );
  const stageWrap = h("div", { class: "mk-stage-wrap" }, stage, stageSpinner, zoomBtn, showBtn);
  const underHost = h("div", { class: "mk-under" });

  // ---- side panel (right)
  const titleEl = h("h2", { class: "mk-side-title" });
  const metaBtn = iconButton({ icon: "edit", title: t("editor.edit_meta"), variant: "ghost", size: "sm", onClick: () => onEditMeta() });
  const sizeEl = h("span", { class: "mk-size-chip num" });
  const stateHost = h("span", { class: "mk-state-host" });
  const disabledHost = h("div", { class: "mk-disabled-host" });
  const resetBtn = button({ label: t("editor.reset_default"), variant: "ghost", size: "sm", icon: "undo", onClick: () => resetArea() });
  const fields = {};
  const fieldEls = [];
  for (const key of ["x", "y", "w", "h"]) {
    const input = textInput({ mono: true, suffix: "0 px", ariaLabel: t(`editor.field_${key}`), inputmode: "decimal", class: "mk-field-input" });
    input.input.addEventListener("input", () => onFieldInput(key, input.input.value, false));
    input.input.addEventListener("change", () => onFieldInput(key, input.input.value, true));
    input.input.addEventListener("blur", () => renderFields(true));
    input.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") onFieldInput(key, input.input.value, true);
      if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        const cur = area ? area[key] * 100 : 0;
        const step = (e.shiftKey ? 1 : 0.1) * (e.key === "ArrowUp" ? 1 : -1);
        onFieldInput(key, String(Math.round((cur + step) * 10) / 10), true);
      }
    });
    fields[key] = input;
    fieldEls.push(field({ label: t(`editor.field_${key}`), input, class: "mk-field" }));
  }
  const designThumb = h("span", { class: "mk-design-thumb checker" });
  const designName = h("span", { class: "mk-design-name ellipsis" });
  const designRow = h(
    "button",
    { type: "button", class: "mk-design-row", onClick: () => pickDesign() },
    designThumb,
    h("span", { class: "mk-design-text" }, designName, h("span", { class: "mk-design-sub" }, t("editor.preview_only"))),
    icon("chevron-right", { size: 16 }),
  );
  const appliedTitle = h("p", { class: "mk-applied-title" });
  // After a save: the next mockup still on the default area, so 37 are done one after another.
  const nextHost = h("div", { class: "mk-next-host" });
  const appliedCard = h(
    "div",
    { class: "mk-applied", role: "status" },
    h("span", { class: "mk-applied-icon" }, icon("check", { size: 15, strokeWidth: 2.8 })),
    h("div", { class: "mk-applied-text" }, appliedTitle, h("p", { class: "mk-applied-sub" }, t("editor.applied_sub")), nextHost),
  );
  appliedCard.hidden = true;
  const sideSkeleton = h(
    "div",
    { class: "mk-side-skeleton", "aria-hidden": "true" },
    skeleton({ lines: 1, height: 22, widths: ["60%"] }),
    skeleton({ lines: 2, height: 11, widths: ["92%", "70%"] }),
    skeleton({ lines: 4, height: 38, gap: 12, widths: ["100%"] }),
  );
  const side = h(
    "aside",
    { class: "mk-side" },
    sideSkeleton,
    h("div", { class: "mk-side-head" }, titleEl, metaBtn),
    h("div", { class: "mk-side-chips" }, sizeEl, stateHost),
    h("p", { class: "mk-helper" }, t("editor.helper")),
    disabledHost,
    h("div", { class: "mk-divider" }),
    sectionTitle(t("editor.section_area"), { actions: resetBtn }),
    h("div", { class: "mk-fields" }, fieldEls),
    sectionTitle(t("editor.section_preview")),
    designRow,
    h("div", { class: "mk-side-fill" }),
    appliedCard,
  );

  // ---- footer
  const sameHost = h("div", { class: "mk-same" });
  const cancelBtn = button({ label: t("common.cancel"), variant: "secondary", onClick: () => cancel() });
  const saveBtn = button({ label: t("common.save"), icon: "check", variant: "primary", onClick: () => save() });
  const foot = h("footer", { class: "mk-main-foot" }, sameHost, h("div", { class: "spacer" }), cancelBtn, saveBtn);

  const main = h(
    "section",
    { class: "card mk-main is-loading" },
    h("div", { class: "mk-main-top" }, h("div", { class: "mk-canvas-col" }, stageWrap, underHost), side),
    foot,
  );
  const shell = h("div", { class: "mk-editor" }, lib, main);
  el.append(shell);
  const offDrop = installPageDrop(el, ctx, (files) => uploadHere(files));
  cleanups.push(offDrop);

  // ---- load
  libCount.textContent = "";
  mount(libList, Array.from({ length: 5 }, () => h("div", { class: "mk-lib-item is-skeleton" }, h("span", { class: "skeleton mk-lib-thumb" }), skeleton({ lines: 2, height: 10, widths: ["70%", "40%"] }))));
  art.classList.add("is-loading");

  let listRes;
  let areaRes = null;
  try {
    listRes = await ctx.api.get("/api/mockups", null, { signal: ctx.signal });
    // Asked only for a mockup that exists, so a stale link does not log a failed request.
    if (listRes.items.some((it) => it.name === name)) {
      areaRes = await ctx.api.get(`/api/mockups/${enc(name)}/area`, null, { signal: ctx.signal }).catch((err) => {
        if (err && err.code === "not_found") return null;
        throw err;
      });
    }
  } catch (err) {
    if (ctx.api.isAbort(err)) return () => {};
    mount(
      el,
      emptyState({
        icon: "alert",
        title: t("load_error"),
        message: ctx.api.errorText(err, t),
        action: button({ label: t("common.retry"), icon: "refresh", onClick: () => ctx.remount() }),
      }),
    );
    return () => offDrop();
  }
  list = listRes.items;
  maxEnabled = listRes.max_enabled || maxEnabled;
  item = list.find((it) => it.name === name) || null;
  if (!item || !areaRes) {
    mount(
      el,
      emptyState({
        icon: "image",
        title: t("editor.not_found"),
        message: t("editor.not_found_msg"),
        action: button({ label: t("back"), icon: "arrow-left", onClick: () => ctx.navigate(GRID_PATH) }),
      }),
    );
    return () => offDrop();
  }
  main.classList.remove("is-loading");
  sideSkeleton.remove();
  saved = { ...areaRes.area };
  area = { ...areaRes.area };
  source = areaRes.source;
  siblings = areaRes.same_size || [];
  sameSize = siblings.length > 0 && !siblings.some((n) => (list.find((it) => it.name === n) || {}).area_source === "own");

  renderLibrary();
  renderSide();
  renderSame();
  renderRect();
  updateState();
  setShowDesign(showDesign, { quiet: true });

  // The base picture: exactly what the compositor draws on (upright, flattened).
  baseImg.addEventListener(
    "load",
    () => {
      art.classList.remove("is-loading");
      stageSpinner.hidden = true;
      layout();
      loadPreview();
    },
    { once: true },
  );
  baseImg.addEventListener(
    "error",
    () => {
      stageSpinner.hidden = true;
      mount(stage, emptyState({ icon: "alert", title: t("editor.unreadable"), message: item.name, compact: true }));
    },
    { once: true },
  );
  baseImg.src = ctx.api.url(`/api/mockups/${enc(name)}/image`, { max: IMAGE_MAX, v: item.version });

  const ro = new ResizeObserver(() => layout());
  ro.observe(stage);
  cleanups.push(() => ro.disconnect());

  // Designs for the preview (the default is the first transparent one, else the sample).
  ctx.api
    .get("/api/mockups/designs", null, { signal: ctx.signal })
    .then((res) => {
      designs = res;
      const known = design && (design.id === SAMPLE.id || res.items.some((d) => d.id === design.id));
      if (!known) {
        const fallback = res.items.find((d) => d.id === res.default);
        setDesign(fallback || SAMPLE, { remember: false });
      } else {
        const fresh = res.items.find((d) => d.id === design.id);
        if (fresh) setDesign(fresh, { remember: false });
      }
    })
    .catch((err) => {
      if (ctx.api.isAbort(err)) return;
      if (!design) setDesign(SAMPLE, { remember: false });
    });
  if (design) setDesign(design, { remember: false });
  else renderDesignRow();

  // ---- guards against losing unsaved work: the app asks before any in-app navigation
  // (library, sidebar, header, Back), a shop switch or a language change; setDirty makes
  // a reload or closing the tab ask as well. A "leave" answer keeps the edits marked as
  // unsaved: the leave can still be called off after it (the quit question, a refused
  // shop switch, a failed language save), and the page is unmounted when it happens.
  ctx.onBeforeLeave(async () => {
    if (!isDirty()) return true;
    return ctx.confirm({ title: t("editor.unsaved_title"), message: t("editor.unsaved_msg"), confirmLabel: t("editor.leave"), danger: true });
  });

  // ---- pointer editing
  art.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !area || art.classList.contains("is-loading")) return;
    const handle = e.target.closest(".mk-handle");
    const inRect = e.target.closest(".mk-rect");
    const r = art.getBoundingClientRect();
    drag = {
      mode: handle ? "resize" : inRect ? "move" : "draw",
      handle: handle ? handle.dataset.handle : null,
      start: { ...area },
      px: e.clientX,
      py: e.clientY,
      fx: (e.clientX - r.left) / r.width,
      fy: (e.clientY - r.top) / r.height,
      width: r.width,
      height: r.height,
      moved: false,
      id: e.pointerId,
    };
    try {
      art.setPointerCapture(e.pointerId);
    } catch {
      /* synthetic events */
    }
    e.preventDefault();
    rect.focus({ preventScroll: true });
  });
  art.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const ddx = e.clientX - drag.px;
    const ddy = e.clientY - drag.py;
    if (!drag.moved && Math.abs(ddx) + Math.abs(ddy) < 3) return;
    if (!drag.moved) {
      drag.moved = true;
      art.classList.add("is-dragging", `drag-${drag.mode}`);
    }
    setArea(computeDrag(drag, ddx / drag.width, ddy / drag.height, e.shiftKey));
  });
  const endDrag = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const was = drag;
    drag = null;
    art.classList.remove("is-dragging", "drag-move", "drag-resize", "drag-draw");
    try {
      art.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
    if (was.moved) schedulePreview();
  };
  art.addEventListener("pointerup", endDrag);
  art.addEventListener("pointercancel", endDrag);
  art.addEventListener("dragstart", (e) => e.preventDefault());

  rect.addEventListener("keydown", (e) => {
    const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const mv = moves[e.key];
    if (!mv || !area) return;
    e.preventDefault();
    const step = e.shiftKey ? 10 : 1;
    setArea({ ...area, x: clamp(area.x + (mv[0] * step) / W(), 0, 1 - area.w), y: clamp(area.y + (mv[1] * step) / H(), 0, 1 - area.h) });
    schedulePreview();
  });

  stage.addEventListener(
    "wheel",
    (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const i = ZOOMS.indexOf(zoom);
      const next = e.deltaY < 0 ? ZOOMS[Math.min(ZOOMS.length - 1, i + 1)] : ZOOMS[Math.max(0, i - 1)];
      if (next !== zoom) setZoom(next);
    },
    { passive: false },
  );

  // ---- functions

  function minW() {
    return Math.min(1, Math.max(12 / W(), 0.01));
  }
  function minH() {
    return Math.min(1, Math.max(12 / H(), 0.01));
  }

  function computeDrag(d, dx, dy, keepRatio) {
    const s = d.start;
    if (d.mode === "move") {
      return { x: clamp(s.x + dx, 0, 1 - s.w), y: clamp(s.y + dy, 0, 1 - s.h), w: s.w, h: s.h };
    }
    if (d.mode === "draw") {
      const ax = clamp(d.fx, 0, 1);
      const ay = clamp(d.fy, 0, 1);
      const bx = clamp(d.fx + dx, 0, 1);
      const by = clamp(d.fy + dy, 0, 1);
      const w = Math.max(Math.abs(bx - ax), minW());
      const hh = Math.max(Math.abs(by - ay), minH());
      return { x: clamp(Math.min(ax, bx), 0, 1 - w), y: clamp(Math.min(ay, by), 0, 1 - hh), w, h: hh };
    }
    const hd = d.handle;
    let left = s.x;
    let right = s.x + s.w;
    let top = s.y;
    let bottom = s.y + s.h;
    if (hd.includes("w")) left = clamp(s.x + dx, 0, right - minW());
    if (hd.includes("e")) right = clamp(s.x + s.w + dx, left + minW(), 1);
    if (hd.includes("n")) top = clamp(s.y + dy, 0, bottom - minH());
    if (hd.includes("s")) bottom = clamp(s.y + s.h + dy, top + minH(), 1);
    if (keepRatio && hd.length === 2) {
      // Keep the rectangle's pixel ratio, anchored at the opposite corner.
      const ratio = (s.w * W()) / (s.h * H());
      let wpx = (right - left) * W();
      let hpx = (bottom - top) * H();
      if (wpx / hpx > ratio) hpx = wpx / ratio;
      else wpx = hpx * ratio;
      let nw = wpx / W();
      let nh = hpx / H();
      const maxW = hd.includes("w") ? s.x + s.w : 1 - s.x;
      const maxH = hd.includes("n") ? s.y + s.h : 1 - s.y;
      const k = Math.min(1, maxW / nw, maxH / nh);
      nw *= k;
      nh *= k;
      left = hd.includes("w") ? s.x + s.w - nw : s.x;
      top = hd.includes("n") ? s.y + s.h - nh : s.y;
      right = left + nw;
      bottom = top + nh;
    }
    return { x: left, y: top, w: right - left, h: bottom - top };
  }

  function setArea(next) {
    const w = clamp(next.w, minW(), 1);
    const hh = clamp(next.h, minH(), 1);
    area = { x: clamp(next.x, 0, 1 - w), y: clamp(next.y, 0, 1 - hh), w, h: hh };
    markStale();
    renderRect();
    updateState();
  }

  function layout() {
    if (!item || !stage.isConnected) return;
    const pad = 36;
    const sw = stage.clientWidth;
    const sh = stage.clientHeight;
    if (!sw || !sh) return;
    const fit = Math.max(0.02, Math.min((sw - pad * 2) / W(), (sh - pad * 2) / H()));
    const scale = fit * zoom;
    art.style.width = `${Math.max(1, Math.round(W() * scale))}px`;
    art.style.height = `${Math.max(1, Math.round(H() * scale))}px`;
    zoomVal.textContent = percent(zoom, 0);
  }

  function setZoom(next) {
    const cx = (stage.scrollLeft + stage.clientWidth / 2) / Math.max(1, stage.scrollWidth);
    const cy = (stage.scrollTop + stage.clientHeight / 2) / Math.max(1, stage.scrollHeight);
    zoom = next;
    layout();
    stage.scrollLeft = cx * stage.scrollWidth - stage.clientWidth / 2;
    stage.scrollTop = cy * stage.scrollHeight - stage.clientHeight / 2;
  }

  function openZoomMenu() {
    menu(
      zoomBtn,
      ZOOMS.map((z) => ({ label: percent(z, 0), checked: z === zoom, onClick: () => setZoom(z) })),
      { placement: "bottom-start", width: 130 },
    );
  }

  function renderRect() {
    if (!area) return;
    rect.style.left = `${area.x * 100}%`;
    rect.style.top = `${area.y * 100}%`;
    rect.style.width = `${area.w * 100}%`;
    rect.style.height = `${area.h * 100}%`;
    sizeChip.textContent = `${Math.round(area.w * W())} × ${Math.round(area.h * H())} px`;
    rect.setAttribute("aria-label", t("editor.area_aria", { x: pct(area.x), y: pct(area.y), w: pct(area.w), h: pct(area.h) }));
    renderFields(false);
  }

  function renderFields(force) {
    if (!area) return;
    const px = { x: area.x * W(), y: area.y * H(), w: area.w * W(), h: area.h * H() };
    for (const key of ["x", "y", "w", "h"]) {
      const input = fields[key];
      if (force || document.activeElement !== input.input) input.value = pct(area[key]);
      input.setSuffix(`${Math.round(px[key])} px`);
    }
  }

  function onFieldInput(key, raw, commit) {
    const v = parsePercent(raw);
    if (v === null || !area) {
      if (commit) renderFields(true);
      return;
    }
    const f = v / 100;
    const next = { ...area };
    if (key === "x") next.x = clamp(f, 0, 1 - area.w);
    if (key === "y") next.y = clamp(f, 0, 1 - area.h);
    if (key === "w") next.w = clamp(f, minW(), 1 - area.x);
    if (key === "h") next.h = clamp(f, minH(), 1 - area.y);
    if (!sameArea(next, area)) {
      setArea(next);
      schedulePreview();
    }
    if (commit) renderFields(true);
  }

  function updateState() {
    const dirty = isDirty();
    ctx.setDirty(dirty);
    let st;
    if (dirty) st = badge({ text: t("editor.unsaved"), tone: "warning", dot: true });
    else if (source === "own") st = badge({ text: t("editor.set"), tone: "success", icon: "check" });
    else if (source === "same_size") st = badge({ text: t("editor.shared"), tone: "accent", title: t("area.same_size_hint") });
    else st = badge({ text: t("editor.default"), tone: "neutral", title: t("area.default_hint") });
    mount(stateHost, st);
    if (!dirty && source === "own") {
      mount(underHost, h("span", { class: "mk-saved-chip" }, icon("check", { size: 13, strokeWidth: 2.6 }), t("editor.saved_chip")));
    } else {
      mount(underHost, h("p", { class: "mk-hint" }, icon("info", { size: 13 }), h("span", null, t("editor.hint"))));
    }
    appliedCard.hidden = !(lastApplied && !dirty);
    resetBtn.hidden = !(source === "own" && !dirty);
    main.classList.toggle("is-dirty", dirty);
    if (item) renderSideNotes();
  }

  function renderSide() {
    titleEl.textContent = itemLabel(t, item);
    titleEl.title = item.name;
    sizeEl.textContent = item.width && item.height ? `${item.width}×${item.height} px` : "–";
    noteKey = "";
    renderSideNotes();
  }

  function renderSideNotes() {
    const key = `${source}|${isDirty()}|${item.enabled}|${item.over_limit}`;
    if (key === noteKey) return;
    noteKey = key;
    const notesList = [];
    if (source === "default" && !isDirty()) {
      // FIXLIST 8: say plainly what "default" means for the drafts.
      notesList.push(infoNote({ tone: "warning", icon: "crop", text: t("editor.default_note") }));
    }
    if (!item.enabled) {
      notesList.push(
        infoNote({
          tone: "warning",
          icon: "eye-off",
          text: t("editor.disabled_note"),
          action: button({
            label: t("editor.enable"),
            size: "sm",
            onClick: async () => {
              const res = await setEnabled(ctx, item, true);
              if (res) {
                Object.assign(item, res);
                renderSide();
                renderLibrary();
              }
            },
          }),
        }),
      );
    } else if (item.over_limit) {
      notesList.push(infoNote({ tone: "warning", icon: "layers", text: t("editor.over_note", { max: maxEnabled }) }));
    }
    mount(disabledHost, notesList);
  }

  function renderSame() {
    const labels = siblings.map((n) => {
      const it = list.find((x) => x.name === n);
      return it ? itemLabel(t, it) : n;
    });
    const ownOnes = siblings.filter((n) => (list.find((x) => x.name === n) || {}).area_source === "own");
    // 36 colour variants must not become one endless line: a few names, then "+33".
    const SHOWN = 3;
    const names = labels.length > SHOWN ? t("editor.same_size_more", { names: labels.slice(0, SHOWN).join(", "), n: labels.length - SHOWN }) : labels.join(", ");
    let sub = siblings.length ? names : t("editor.same_size_none");
    if (ownOnes.length && !lastApplied) sub = t("editor.same_size_own", { names });
    const tog = toggle({
      checked: sameSize && siblings.length > 0,
      disabled: !siblings.length,
      label: t("editor.same_size", { n: siblings.length }),
      sub,
      onChange: (v) => {
        sameSize = v;
      },
    });
    tog.title = siblings.length ? `${labels.join(", ")}\n\n${t("editor.same_size_explain")}` : t("editor.same_size_explain");
    mount(sameHost, tog, siblings.length ? h("p", { class: "mk-same-explain" }, t("editor.same_size_explain")) : null);
  }

  function renderLibrary() {
    libCount.textContent = t("library.count", { n: list.length });
    mount(
      libList,
      list.map((it) => {
        const current = it.name === name;
        const isApplied = applied.has(it.name);
        let mark = null;
        if (current) mark = h("span", { class: "mk-lib-mark is-current" }, icon("arrow-right", { size: 16 }));
        else if (isApplied) mark = h("span", { class: "mk-lib-mark is-applied" }, icon("check", { size: 14, strokeWidth: 2.8 }));
        else if (it.area_source === "own") mark = h("span", { class: "mk-lib-mark is-own", title: t("area.own") }, icon("check", { size: 12, strokeWidth: 2.6 }));
        const img = h("img", { src: thumbUrl(ctx, it, 160), alt: "", loading: "lazy", decoding: "async", draggable: "false" });
        img.addEventListener("error", () => img.replaceWith(icon("image", { size: 18 })), { once: true });
        return h(
          "a",
          {
            class: cx("mk-lib-item", current && "is-current", !current && isApplied && "is-applied", !it.enabled && "is-disabled"),
            href: editorPath(it.name),
            role: "listitem",
            title: it.name,
            "aria-current": current ? "page" : undefined,
          },
          h("span", { class: "mk-lib-thumb" }, img),
          h(
            "span",
            { class: "mk-lib-text" },
            h("span", { class: "mk-lib-label ellipsis" }, itemLabel(t, it)),
            h(
              "span",
              { class: "mk-lib-sub" },
              h("span", { class: "mono" }, sizeText(it)),
              !current && isApplied ? [h("span", { class: "mk-sep", "aria-hidden": "true" }, "·"), h("span", { class: "mk-lib-applied" }, t("library.applied"))] : null,
              !it.enabled ? [h("span", { class: "mk-sep", "aria-hidden": "true" }, "·"), h("span", null, t("unused"))] : null,
              it.enabled && it.over_limit ? [h("span", { class: "mk-sep", "aria-hidden": "true" }, "·"), h("span", { class: "mk-lib-over" }, t("over_limit"))] : null,
            ),
          ),
          mark,
        );
      }),
    );
    const cur = libList.querySelector(".is-current");
    if (cur) requestAnimationFrame(() => cur.scrollIntoView({ block: "nearest" }));
  }

  // ---- preview design

  function renderDesignRow() {
    const d = design || SAMPLE;
    const img = h("img", { src: designUrl(ctx, d, 120), alt: "", draggable: "false" });
    img.addEventListener("error", () => img.replaceWith(icon("image", { size: 16 })), { once: true });
    mount(designThumb, img);
    designName.textContent = designLabel(t, d);
  }

  function setDesign(d, { remember = true } = {}) {
    design = { id: d.id, label: d.label, name: d.name, version: d.version };
    if (remember) writeStored("local", DESIGN_KEY, design);
    overlayImg.src = designUrl(ctx, design, 900);
    renderDesignRow();
    markStale();
    loadPreview();
  }

  function setShowDesign(v, { quiet = false } = {}) {
    showDesign = !!v;
    writeStored("local", SHOW_KEY, showDesign);
    art.classList.toggle("show-design", showDesign);
    showBtn.classList.toggle("is-on", showDesign);
    showBtn.setAttribute("aria-pressed", showDesign ? "true" : "false");
    if (!quiet) {
      markStale();
      loadPreview();
    }
  }

  function markStale() {
    previewSeq += 1;
    previewImg.hidden = true;
    art.classList.remove("has-preview");
    schedulePreview.cancel();
  }

  function loadPreview() {
    if (!showDesign || !design || !area || drag || art.classList.contains("is-loading")) return;
    const seq = ++previewSeq;
    const a = roundArea(area);
    const img = new Image();
    img.className = "mk-preview";
    img.alt = "";
    img.draggable = false;
    img.onload = () => {
      if (seq !== previewSeq || !showDesign || !sameArea(a, roundArea(area)) || !ctx.isActive()) return;
      img.hidden = false;
      previewImg.replaceWith(img);
      previewImg = img;
      art.classList.add("has-preview");
    };
    img.src = ctx.api.url(`/api/mockups/${enc(name)}/preview`, { design: design.id, x: a.x, y: a.y, w: a.w, h: a.h, max: IMAGE_MAX });
  }

  async function pickDesign() {
    if (!designs) {
      try {
        designs = await ctx.api.get("/api/mockups/designs", null, { signal: ctx.signal });
      } catch (err) {
        if (!ctx.api.isAbort(err)) ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t) });
        return;
      }
    }
    const options = [SAMPLE, ...designs.items];
    let m = null;
    const grid = h(
      "div",
      { class: "mk-design-grid" },
      options.map((o) =>
        h(
          "button",
          {
            type: "button",
            class: cx("mk-design-opt", design && design.id === o.id && "is-selected"),
            "aria-pressed": design && design.id === o.id ? "true" : "false",
            title: o.name || designLabel(t, o),
            onClick: () => {
              setDesign(o);
              if (!showDesign) setShowDesign(true);
              if (m) m.close();
            },
          },
          h("span", { class: "mk-design-opt-img checker" }, h("img", { src: designUrl(ctx, o, 240), alt: "", loading: "lazy", draggable: "false" })),
          h("span", { class: "mk-design-opt-label ellipsis" }, designLabel(t, o)),
        ),
      ),
    );
    m = ctx.modal({
      title: t("editor.pick_design"),
      subtitle: t("editor.pick_sub"),
      width: 660,
      body: [designs.items.length ? null : infoNote({ tone: "neutral", icon: "info", text: t("editor.no_designs") }), grid],
    });
  }

  // ---- actions

  async function save() {
    if (saving || !area) return;
    saving = true;
    saveBtn.setLoading(true);
    try {
      const body = { ...roundArea(area), same_size: !!(sameSize && siblings.length) };
      const res = await ctx.api.post(`/api/mockups/${enc(name)}/area`, body, { signal: ctx.signal });
      saved = { ...res.area };
      if (sameArea(saved, area)) area = { ...saved };
      source = res.source;
      siblings = res.same_size || siblings;
      lastApplied = res.applied_to || [name];
      for (const n of lastApplied) applied.add(n);
      storeApplied();
      appliedTitle.textContent = t("editor.applied_title", { n: lastApplied.length });
      for (const it of list) {
        if (lastApplied.includes(it.name)) {
          it.area = { ...saved };
          it.area_source = "own";
        }
      }
      renderLibrary();
      renderSame();
      renderRect();
      updateState();
      refreshList();
    } catch (err) {
      if (!ctx.api.isAbort(err)) ctx.toast({ tone: "danger", title: t("editor.save_failed"), message: ctx.api.errorText(err, t) });
    } finally {
      saving = false;
      saveBtn.setLoading(false);
    }
  }

  function cancel() {
    if (isDirty()) {
      area = { ...saved };
      markStale();
      renderRect();
      updateState();
      loadPreview();
      return;
    }
    ctx.navigate(GRID_PATH);
  }

  async function resetArea() {
    const ok = await ctx.confirm({ title: t("editor.reset_title"), message: t("editor.reset_message"), confirmLabel: t("editor.reset_confirm") });
    if (!ok) return;
    try {
      const res = await ctx.api.del(`/api/mockups/${enc(name)}/area`, null, { signal: ctx.signal });
      saved = { ...res.area };
      area = { ...res.area };
      source = res.source;
      lastApplied = null;
      applied.delete(name);
      storeApplied();
      markStale();
      renderRect();
      renderSame();
      updateState();
      loadPreview();
      refreshList();
    } catch (err) {
      if (!ctx.api.isAbort(err)) ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t) });
    }
  }

  function renderNext() {
    const waiting = list.filter((it) => it.name !== name && it.area_source === "default");
    const next = waiting.find((it) => it.in_use) || waiting[0];
    if (!next) {
      mount(nextHost);
      return;
    }
    mount(
      nextHost,
      h(
        "a",
        { class: "mk-next", href: editorPath(next.name), title: t("editor.next_default_hint", { n: waiting.length }) },
        h("span", null, t("editor.next_default", { label: itemLabel(t, next) })),
        h("span", { class: "mk-next-count num" }, t("editor.next_default_count", { n: waiting.length })),
        icon("arrow-right", { size: 13 }),
      ),
    );
  }

  async function refreshList() {
    try {
      const res = await ctx.api.get("/api/mockups", null, { signal: ctx.signal });
      list = res.items;
      maxEnabled = res.max_enabled || maxEnabled;
      const fresh = list.find((it) => it.name === name);
      if (fresh) item = { ...item, ...fresh, area: item.area };
      renderLibrary();
      renderSame();
      renderSide();
      renderNext();
    } catch (err) {
      if (!ctx.api.isAbort(err)) console.warn("[mockups] could not refresh the library", err);
    }
  }

  async function onEditMeta() {
    const res = await editMeta(ctx, item);
    if (!res) return;
    Object.assign(item, res);
    const it = list.find((x) => x.name === name);
    if (it) Object.assign(it, res);
    renderSide();
    renderLibrary();
    renderSame();
  }

  function leave(path) {
    ctx.navigate(path); // the leave guard above asks when there are unsaved changes
  }

  function uploadHere(files) {
    chain = chain
      .then(() => uploadBatch(ctx, files))
      .then(async (res) => {
        if (!ctx.isActive()) return;
        await refreshList();
        if (res.added.length === 1 && !isDirty()) ctx.navigate(editorPath(res.added[0].name));
      })
      .catch((err) => {
        if (!ctx.api.isAbort(err)) console.error("[mockups] upload failed", err);
      });
    return chain;
  }

  async function pickAndUpload() {
    const files = await pickFiles({ multiple: true });
    if (files.length && ctx.isActive()) uploadHere(files);
  }

  return () => {
    schedulePreview.cancel();
    for (const fn of cleanups) {
      try {
        fn();
      } catch {
        /* ignore */
      }
    }
  };
}
