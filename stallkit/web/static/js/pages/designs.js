// Tasarım Yükle: drop designs, then watch each one become an Etsy draft.
//
// Views: idle (dropzone + setup chips + the six steps, video t210), uploading (per-file
// progress), running (pipeline table + "being prepared" card, t220) and done (success
// banner + table + sample draft, t240). A run is a server job ("designs"); this page only
// renders its state and live events, so navigating away and back restores the view.

import {
  h, cx, mount, card, button, iconButton, badge, infoNote, thumb, dropzone, progressBar,
  stepDots, tagChip, spinner, skeleton,
} from "../ui.js";
import { icon } from "../icons.js";
import { money, duration, relative, bytes, number } from "../format.js";

const STEPS = ["mockup", "research", "title", "tags", "check", "draft"];
const STEP_ICONS = { mockup: "image", research: "search", title: "sparkles", tags: "tag", check: "shield-check", draft: "upload" };
const FINAL = new Set(["ok", "partial", "error", "cancelled", "checked"]);
// Problems whose translated line already says everything (the English detail is left out).
const SELF_EXPLAINED = new Set([
  "junk_name", "too_many_pixels", "too_many_images", "no_images", "invalid_image", "stopped",
  "no_deliverable", "too_many_files", "file_type", "file_too_large", "file_empty", "file_missing", "file_unreadable",
  "no_photos", "nested_files", "download_is_photo",
]);
// A digital product folder keeps what the buyer downloads in one of these subfolders.
const FILES_DIRS = new Set(["dosyalar", "files"]);
const DIGITAL = new Set(["download", "both"]);
const JOB_FINAL = new Set(["done", "error", "cancelled"]);
const ACCEPT = ".png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff";
const MAX_FILES = 500;
const MAX_BYTES = 50 * 1024 * 1024;
const MAX_PHOTOS = 20;
const MAX_DOWNLOADS = 5; // Etsy: download files per listing (client.MAX_LISTING_FILES)
const UPLOAD_PARALLEL = 3;
const VISIBLE_ROWS = 7;
const PENDING_TILES = 18;
const DISMISS_KEY = "stallkit.designs.dismissed";
const SETUP_LINKS = {
  keys: "/kurulum/magaza",
  connect: "/kurulum/magaza",
  reconnect: "/kurulum/magaza",
  bad_keys: "/kurulum/magaza",
  template: "/kurulum/sablon",
  template_invalid: "/kurulum/sablon",
};

function storageGet(key) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key, value) {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    /* private mode: the view simply is not remembered */
  }
}

function randomId() {
  const bytesArr = new Uint8Array(8);
  crypto.getRandomValues(bytesArr);
  return [...bytesArr].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function baseName(path) {
  return String(path || "").split("/").pop();
}

/** "Retro Mountain Sunset Shirt, Vintage Hiking Tee, ..." -> "Retro Mountain Sunset Shirt". */
function shortTitle(title) {
  const text = String(title || "");
  const first = text.split(/\s*[,|]\s*|\s+[-–—]\s+/)[0];
  return first.length >= 8 ? first : text;
}

/**
 * What a dropped path is to a digital template's product: "Planner/dosyalar/planner.pdf"
 * is a download file of the product folder "Planner" ({product, sub, name}); a file deeper
 * down ("Planner/dosyalar/A4/art.pdf") is {nested: true}, which Etsy cannot take (files,
 * not folders). null for anything else, and always for a physical template: there a
 * "dosyalar" / "files" folder is just a folder of designs.
 */
export function downloadOf(path, digital) {
  if (!digital) return null;
  const parts = String(path || "").split("/").filter(Boolean);
  // The last "dosyalar" / "files" with a product folder before it and a file after it.
  for (let i = parts.length - 2; i >= 1; i -= 1) {
    if (!FILES_DIRS.has(parts[i].toLowerCase())) continue;
    if (i === parts.length - 2) return { product: parts[i - 1], sub: parts[i], name: parts[i + 1], nested: false };
    return { nested: true };
  }
  return null;
}

/**
 * Server paths for a drop: "name" for a design, "folder/name" for a photo of a product
 * folder and "folder/dosyalar/name" for a download file of one. The download rows come
 * last, and upload() sends them only once every photo is in, so a product folder is
 * always claimed by its first photo. A download whose product has no photo in this drop
 * is marked `attach`: it goes without the batch and joins the folder of that name
 * already in 2-PRODUCTS (the missing "dosyalar" of a product added afterwards).
 * Download files only travel with "every folder is one product"; files nested deeper in
 * "dosyalar" never go (Etsy takes files, not folders).
 */
export function planUploads(files, mode, digital) {
  const byDir = new Map();
  const downloads = [];
  for (const x of files) {
    const dl = downloadOf(x.path, digital);
    if (dl) {
      if (mode === "products" && !dl.nested) downloads.push({ x, dl });
      continue;
    }
    const parts = (x.path || x.file.name).split("/").filter(Boolean);
    const dir = parts.slice(0, -1).join("/");
    if (!byDir.has(dir)) byDir.set(dir, []);
    byDir.get(dir).push(x);
  }
  const out = [];
  const products = new Set();
  for (const [dir, group] of byDir) {
    const leaf = dir ? dir.split("/").pop() : "";
    const asProduct = mode === "products" && dir && group.length <= MAX_PHOTOS;
    if (asProduct) products.add(leaf.toLowerCase());
    for (const x of group) {
      const name = x.file.name;
      out.push({ file: x.file, path: asProduct ? `${leaf}/${name}` : name, label: x.path || name });
    }
  }
  for (const { x, dl } of downloads) {
    out.push({
      file: x.file,
      path: `${dl.product}/${dl.sub}/${dl.name}`,
      label: x.path,
      deliverable: true,
      attach: !products.has(dl.product.toLowerCase()),
    });
  }
  return out;
}

/** physical | download | both, from whatever the server sent (physical when unknown). */
function listingType(value) {
  return value === "download" || value === "both" ? value : "physical";
}

/** The plain design among a product's images (the rest are mockups). */
function flatPath(item) {
  if (!item) return null;
  if (item.flat) return item.flat;
  // Runs saved before the flat image was marked: the stream names it "<design>--flat".
  if (item.mode !== "composited") return null;
  return (item.images || []).find((p) => /--flat(-\d+)?\.jpg$/i.test(p)) || null;
}

class DesignsPage {
  constructor(el, ctx) {
    this.el = el;
    this.ctx = ctx;
    this.t = ctx.t;
    this.api = ctx.api;
    this.view = null;
    this.pending = null;
    this.pendingExpanded = false;
    this.lastRun = null;
    this.run = null;
    this.selected = null; // item index pinned by a click
    this.expanded = false;
    this.timers = new Set();
    this.rafPending = false;
    this.dirtyRows = new Set();
    this.typing = null;
    this.uploading = null;
    this.statusDebounce = null;
    this.destroyed = false;
  }

  // ------------------------------------------------------------------ lifecycle

  async start() {
    const { ctx } = this;
    ctx.events.on("job-event", (ev) => this.onJobEvent(ev));
    ctx.events.on("job", (job) => this.onJob(job));
    ctx.events.on("reconnect", () => this.onReconnect());
    ctx.onStatus((s, prev) => {
      if (this.view !== "idle") return;
      if (prev && s && s.state === prev.state && s.setup?.designs_pending === prev.setup?.designs_pending) return;
      clearTimeout(this.statusDebounce);
      this.statusDebounce = setTimeout(() => this.loadPending(), 400);
    });
    // Leaving mid-upload (a link, Back, a shop switch, a language change) would cut the
    // upload off and leave half a product folder behind: ask first. setDirty makes a
    // reload or closing the tab ask too.
    ctx.onBeforeLeave(async () => {
      const up = this.uploading;
      if (!up) return true;
      const ok = await ctx.confirm({
        title: this.t("leave.title"),
        message: this.t("leave.msg", { done: up.done, total: up.rows.length }),
        confirmLabel: this.t("leave.confirm"),
        cancelLabel: this.t("leave.stay"),
        danger: true,
      });
      if (ok && this.uploading === up) up.controller.abort();
      return ok;
    });
    this.el.classList.add("dz-page");
    let jobs = [];
    try {
      jobs = await this.api.get("/api/jobs", { kind: "designs" }, { signal: ctx.signal });
    } catch (err) {
      if (this.api.isAbort(err)) return;
      jobs = [];
    }
    const latest = Array.isArray(jobs) && jobs.length ? jobs[0] : null;
    if (latest && (!JOB_FINAL.has(latest.status) || storageGet(DISMISS_KEY) !== latest.id)) {
      await this.showJob(latest.id);
      return;
    }
    await this.showIdle();
  }

  destroy() {
    this.destroyed = true;
    for (const id of this.timers) clearInterval(id);
    this.timers.clear();
    clearTimeout(this.statusDebounce);
    if (this.typing) clearInterval(this.typing.timer);
    if (this.uploading && this.uploading.controller) this.uploading.controller.abort();
  }

  every(ms, fn) {
    const id = setInterval(fn, ms);
    this.timers.add(id);
    return id;
  }

  stopTimers() {
    for (const id of this.timers) clearInterval(id);
    this.timers.clear();
  }

  errorText(err) {
    return this.api.errorText(err, this.t);
  }

  toastError(err) {
    if (this.api.isAbort(err)) return;
    this.ctx.toast({ tone: "danger", title: this.errorText(err) });
  }

  thumbUrl(path, w, v) {
    if (!path) return null;
    return this.api.url("/api/files/thumb", { path, w, v });
  }

  // ------------------------------------------------------------------ texts

  problemText(problem) {
    const { t } = this;
    if (!problem) return "";
    const key = `problem.${problem.code}`;
    if (problem.code === "stopped") {
      const reason = problem.params && problem.params.reason;
      return t("problem.stopped", { reason: this.stoppedReason({ code: reason, params: problem.params }) });
    }
    if (t.has(key)) return t(key, { ...(problem.params || {}), message: problem.message || "" });
    return problem.message || problem.code;
  }

  /** A digital product's download problem in a few words (the pending tiles). */
  fileProblemShort(problem) {
    // no_deliverable: no "dosyalar" folder at all, or one with nothing in it.
    const empty = problem && problem.code === "no_deliverable" && problem.params && problem.params.missing === false;
    const key = empty ? "pending.file.no_deliverable_empty" : `pending.file.${problem && problem.code}`;
    return this.t.has(key) ? this.t(key, (problem && problem.params) || {}) : this.t("pending.file.other");
  }

  /** Whether the library's own (English) words add anything to the translated line. */
  problemDetail(problem) {
    return !!(problem && problem.message && !SELF_EXPLAINED.has(problem.code) && this.t.has(`problem.${problem.code}`));
  }

  warnText(w) {
    const { t } = this;
    const key = `warn.${w.code}`;
    if (t.has(key)) return t(key, { ...(w.params || {}), message: w.message || "" });
    return w.message || w.code;
  }

  stoppedReason(stopped) {
    if (!stopped) return "";
    const key = `stopped.${stopped.code}`;
    return this.t.has(key) ? this.t(key, stopped.params || {}) : stopped.message || stopped.code;
  }

  typeLabel(item) {
    const { t } = this;
    if (!item) return "";
    if (item.mode === "photos" || item.kind === "folder") return t("mode.photos", { n: item.files || item.images.length });
    if (item.mode === "as_is") return t("mode.as_is");
    if (!item.mode) return t("mode.design");
    // A digital download is not the product its mockups show (a shirt, a mug).
    if (this.run && this.run.listingType === "download") return t("mode.digital");
    const primary = this.run && this.run.mockups && this.run.mockups.primary;
    return primary && t.has(`type.${primary}`) ? t(`type.${primary}`) : t("mode.design");
  }

  // ------------------------------------------------------------------ idle view (t210)

  async showIdle() {
    this.view = "idle";
    this.run = null;
    this.selected = null;
    this.expanded = false;
    this.stopTimers();
    this.ctx.setHeader({
      subtitle: this.t("subtitle"),
      actions: [
        iconButton({
          icon: "folder",
          title: this.t("pending.open"),
          variant: "ghost",
          onClick: () => this.openFolder("products"),
        }),
      ],
    });
    this.renderIdle();
    await Promise.all([this.loadPending(), this.loadLastRun()]);
  }

  renderIdle() {
    const { t } = this;
    this.dz = dropzone({
      accept: ACCEPT,
      multiple: true,
      class: "dz-drop",
      title: t("drop.title"),
      subtitle: t("drop.sub"),
      content: this.dropContent(),
      onFiles: (list) => this.handleFiles(list),
      onReject: (list) => this.onRejected(list),
    });
    this.uploadHost = h("div", { class: "dz-upload-host" });
    this.chipsHost = h("div", { class: "dz-status" }, this.statusRow(null));
    this.pendingHost = h("div", { class: "dz-pending-host" });
    this.noteHost = h("div", { class: "dz-note-host" });
    mount(this.el, this.uploadHost, this.dz, this.chipsHost, this.stepsCard(null), this.noteHost, this.pendingHost);
  }

  dropContent(images) {
    const { t } = this;
    const decor = h("div", { class: "dz-decor", "aria-hidden": "true" });
    const spots = ["a", "b", "c", "d", "e", "f", "g"];
    const icons = ["frame", "shirt", "mug", "image", "bag", "phone", "sun"];
    spots.forEach((spot, i) => {
      const src = images && images[i] ? images[i] : null;
      decor.appendChild(
        h("span", { class: cx("dz-float", `dz-float-${spot}`) }, src ? h("img", { src, alt: "", loading: "lazy" }) : icon(icons[i], { size: 26 })),
      );
    });
    this.dzDecor = decor;
    return [
      decor,
      h("span", { class: "dz-icon" }, icon("upload", { size: 30 })),
      h("p", { class: "dz-title" }, t("drop.title")),
      h("p", { class: "dz-sub" }, t("drop.sub")),
      button({
        label: t("drop.pick"),
        variant: "ghost",
        class: "dz-pick",
        onClick: (e) => {
          e.stopPropagation();
          this.dz.open();
        },
      }),
    ];
  }

  setupState(p) {
    if (!p) return { ok: null };
    const setupBlockers = p.blockers.filter((b) => b in SETUP_LINKS);
    return { ok: setupBlockers.length === 0, first: setupBlockers[0] };
  }

  statusRow(p) {
    const { t } = this;
    const chip = (ic, label, value, extra, tone, onClick) =>
      h(
        onClick ? "button" : "div",
        { class: cx("dz-chip", tone && `is-${tone}`), type: onClick ? "button" : undefined, onClick },
        h("span", { class: "dz-chip-icon" }, icon(ic, { size: 15 })),
        label ? h("span", { class: "dz-chip-label" }, label) : null,
        value !== null ? h("strong", { class: "dz-chip-value ellipsis" }, value) : skeleton({ lines: 1, height: 10, widths: ["90px"] }),
        extra || null,
      );
    // "n mockup kullanılacak": the Mockuplar rule (switched on, in order, at most 19), and
    // always a link there, where the seller chooses and orders them.
    const mockups = p ? p.mockups.enabled : null;
    const over = p && p.mockups.over_limit ? h("span", { class: "dz-chip-extra" }, t("chip.mockup_over", { n: p.mockups.switched_on })) : null;
    const mockupChip = chip("image", null, p ? (mockups ? t("chip.mockup_value", { n: mockups }) : t(p.mockups.total ? "chip.mockup_none" : "chip.mockup_empty")) : null, [over, h("span", { class: "dz-chip-go", "aria-hidden": "true" }, icon("arrow-right", { size: 13 }))], p && !mockups ? "warn" : null, () => this.ctx.navigate("/kurulum/mockuplar"));
    mockupChip.title = t("chip.mockup_hint");
    // The template listing's full title is up to 140 characters; the chip shows its
    // first part, as the video does, and the whole title on hover.
    const fullTemplate = p && p.template && p.template.title ? p.template.title : null;
    const template = p ? (fullTemplate && shortTitle(fullTemplate)) || (p.template ? t("chip.template_unnamed") : null) : null;
    const shop = p ? p.shop : null;
    const shopOk = shop && shop.connected;
    const templateChip = chip("file", t("chip.template"), p ? template || t("chip.template_none") : null, null, p && !p.template ? "warn" : null, p && (!p.template || p.blockers.includes("template_invalid")) ? () => this.ctx.navigate("/kurulum/sablon") : null);
    if (fullTemplate) templateChip.title = fullTemplate;
    templateChip.classList.add("dz-chip-template");
    // A digital template's drafts are digital: say so next to the template (a physical
    // one needs no word here, as before).
    const kind = listingType(p && p.template && p.template.listing_type);
    let typeChip = null;
    if (DIGITAL.has(kind)) {
      typeChip = chip("download", t("chip.type"), t(`chip.type_${kind}`), null, null, () => this.ctx.navigate("/kurulum/sablon"));
      typeChip.title = t(`chip.type_${kind}_hint`);
      typeChip.classList.add("dz-chip-type");
    }
    const row = [
      mockupChip,
      templateChip,
      typeChip,
      chip(
        "link",
        t("chip.shop"),
        p ? (shop && shop.name) || (shopOk ? t("chip.shop_connected") : t("chip.shop_none")) : null,
        shopOk ? h("span", { class: "dz-chip-ok" }, icon("check", { size: 11, strokeWidth: 3 })) : null,
        p && !shopOk ? "warn" : null,
        p && !shopOk ? () => this.ctx.navigate("/kurulum/magaza") : null,
      ),
    ];
    const setup = this.setupState(p);
    let right = null;
    if (setup.ok === true) {
      right = h("span", { class: "dz-setup is-ok" }, icon("check", { size: 13 }), t("setup.ok"));
    } else if (setup.ok === false) {
      const link = SETUP_LINKS[setup.first];
      right = h(
        "span",
        { class: "dz-setup is-missing" },
        icon("alert", { size: 13 }),
        t("setup.missing"),
        link ? h("a", { href: link, class: "dz-setup-link" }, t("setup.fix"), icon("arrow-right", { size: 12 })) : null,
      );
    }
    return [h("div", { class: cx("dz-chips", typeChip && "has-type") }, row), h("div", { class: "spacer" }), right];
  }

  stepsCard(p) {
    const { t } = this;
    const n = p ? p.mockups.enabled : null;
    const subs = {
      mockup: n === null ? t("step.mockup_sub", { n: "…" }) : n ? t("step.mockup_sub", { n }) : t("step.mockup_sub_none"),
      research: t("step.research_sub"),
      title: t("step.title_sub"),
      tags: t("step.tags_sub"),
      check: t("step.check_sub"),
      draft: p && p.template && p.template.digital ? t("step.draft_sub_digital") : t("step.draft_sub"),
    };
    const items = [];
    STEPS.forEach((s, i) => {
      if (i) items.push(h("span", { class: "dz-step-arrow", "aria-hidden": "true" }, icon("arrow-right", { size: 14 })));
      items.push(
        h(
          "li",
          { class: "dz-step" },
          h("span", { class: "dz-step-icon" }, icon(STEP_ICONS[s], { size: 16 })),
          h(
            "span",
            { class: "dz-step-text" },
            h("span", { class: "dz-step-name" }, h("span", { class: "dz-step-num num" }, String(i + 1)), t(`step.${s}`)),
            h("span", { class: "dz-step-sub" }, subs[s]),
          ),
        ),
      );
    });
    this.stepsEl = h(
      "section",
      { class: "card dz-steps" },
      h(
        "div",
        { class: "dz-steps-head" },
        h("span", { class: "dz-steps-title" }, t("steps.title")),
        h("span", { class: "dz-steps-note" }, icon("lock", { size: 13 }), t("steps.note")),
      ),
      h("ol", { class: "dz-steps-row" }, items),
    );
    return this.stepsEl;
  }

  async loadPending() {
    if (this.view !== "idle") return null;
    try {
      const p = await this.api.get("/api/designs/pending", null, { signal: this.ctx.signal });
      if (this.view !== "idle" || this.destroyed) return p;
      this.pending = p;
      this.renderPendingParts();
      return p;
    } catch (err) {
      if (this.api.isAbort(err)) return null;
      mount(this.noteHost, infoNote({ tone: "danger", icon: "alert", text: this.errorText(err), action: button({ label: this.t("common.retry"), size: "sm", icon: "refresh", onClick: () => this.loadPending() }) }));
      return null;
    }
  }

  async loadLastRun() {
    try {
      const data = await this.api.get("/api/designs/last", null, { signal: this.ctx.signal });
      this.lastRun = data && data.run ? data.run : null;
      if (this.view === "idle") this.renderPendingParts();
    } catch (err) {
      if (!this.api.isAbort(err)) this.lastRun = null;
    }
  }

  renderPendingParts() {
    const p = this.pending;
    if (!p || this.view !== "idle") return;
    const { t } = this;
    mount(this.chipsHost, this.statusRow(p));
    const steps = this.stepsCard(p);
    const old = this.el.querySelector(".dz-steps");
    if (old) old.replaceWith(steps);
    // Floating decoration: the designs waiting in the folder, else the last run's.
    let srcs = p.items.filter((x) => x.thumb_path).slice(0, 7).map((x) => this.thumbUrl(x.thumb_path, 160, x.mtime));
    if (!srcs.length && this.lastRun && Array.isArray(this.lastRun.items)) {
      const v = (this.lastRun.summary && this.lastRun.summary.batch) || "last";
      srcs = this.lastRun.items.filter((x) => x.thumb_path && x.status !== "error").slice(0, 7).map((x) => this.thumbUrl(x.thumb_path, 160, v));
    }
    if (this.dzDecor && srcs.length) {
      const fresh = this.dropContent(srcs)[0];
      this.el.querySelector(".dz-decor")?.replaceWith(fresh);
    }

    const notes = [];
    if (p.review && p.review.length) {
      notes.push(
        infoNote({
          tone: "warning",
          icon: "alert",
          text: t("review.note", { n: p.review.length }),
          action: button({ label: t("review.open"), size: "sm", onClick: () => this.openReview() }),
        }),
      );
    }
    if (p.blockers.includes("locked")) {
      notes.push(
        infoNote({
          tone: "warning",
          icon: "lock",
          text: this.lockText(p),
          action: button({ label: t("blocked.unlock"), size: "sm", onClick: () => this.unlock() }),
        }),
      );
    }
    mount(this.noteHost, notes);
    mount(this.pendingHost, p.count ? this.pendingCard(p) : null, this.lastRunLine());
  }

  lastRunLine() {
    const run = this.lastRun;
    if (!run || !run.summary) return null;
    const s = run.summary;
    const { t } = this;
    const text = s.dry_run ? t("last.checked", { n: s.checked, when: relative(s.finished_at) }) : t("last.link", { n: s.created, when: relative(s.finished_at) });
    return h(
      "div",
      { class: "dz-last" },
      icon("history", { size: 14 }),
      h("span", null, text),
      button({ label: t("last.view"), size: "sm", variant: "ghost", iconRight: "arrow-right", onClick: () => this.showSaved(run) }),
    );
  }

  pendingCard(p) {
    const { t } = this;
    const items = p.items;
    const shown = this.pendingExpanded ? items : items.slice(0, PENDING_TILES);
    const digital = !!(p.template && p.template.digital);
    const tiles = shown.map((it) => {
      const fileProblem = digital ? it.deliverable_problem : null;
      const bad = it.junk_reason || it.too_many || it.no_photos || fileProblem;
      const downloads = it.deliverables || [];
      let sub;
      if (bad) {
        const why = it.junk_reason ? t("pending.junk") : it.too_many ? t("pending.too_many") : it.no_photos ? t("pending.no_photos") : this.fileProblemShort(fileProblem);
        sub = h("span", { class: "dz-tile-bad" }, icon("alert", { size: 11 }), why);
      } else if (digital && it.kind === "folder") {
        sub = t("pending.folder_files", { n: it.files, m: downloads.length });
      } else if (digital && downloads.length) {
        // (A made-to-order template attaches no loose design: then it is a plain tile.)
        sub = h("span", { class: "dz-tile-dl" }, icon("download", { size: 11 }), bytes(it.size));
      } else {
        sub = it.kind === "folder" ? t("pending.folder", { n: it.files }) : bytes(it.size);
      }
      // Hover: what a buyer would download, or why it cannot go.
      const tip = [it.name];
      if (it.no_photos) tip.push(this.problemText({ code: "no_photos", params: { name: it.name, folder: "dosyalar" } }));
      else if (fileProblem) tip.push(this.problemText({ code: fileProblem.code, params: fileProblem.params }));
      else if (digital && downloads.length) tip.push(`${t("pending.downloads")}: ${downloads.map((d) => d.name).join(", ")}`);
      return h(
        "div",
        { class: cx("dz-tile", bad && "is-bad"), title: tip.join("\n") },
        thumb({ src: this.thumbUrl(it.thumb_path, 200, it.mtime), size: 64, radius: 10, fit: "contain" }),
        h("span", { class: "dz-tile-name ellipsis" }, it.name),
        h("span", { class: "dz-tile-sub" }, sub),
        iconButton({
          icon: "x",
          title: t("pending.remove"),
          variant: "ghost",
          size: "sm",
          class: "dz-tile-x",
          onClick: (e) => {
            e.stopPropagation();
            this.removePending(it);
          },
        }),
      );
    });
    const more = items.length - shown.length;
    if (more > 0 || this.pendingExpanded) {
      tiles.push(
        h(
          "button",
          {
            type: "button",
            class: "dz-tile dz-tile-more",
            onClick: () => {
              this.pendingExpanded = !this.pendingExpanded;
              this.renderPendingParts();
            },
          },
          more > 0 ? t("pending.more", { n: more }) : t("pending.less"),
        ),
      );
    }
    const startBtn = button({ label: t("ready.start"), icon: "zap", variant: "primary", size: "sm", onClick: () => this.openStart() });
    this.startButtons = [startBtn];
    this.syncStartButtons();
    return card({
      class: "dz-pending",
      title: t("pending.title"),
      subtitle: t("pending.sub", { n: p.count }),
      icon: "layers",
      iconTone: "accent",
      actions: [button({ label: t("pending.open"), icon: "folder", variant: "ghost", size: "sm", onClick: () => this.openFolder("products") }), startBtn],
      body: h("div", { class: "dz-tiles" }, tiles),
    });
  }

  /** Başlat waits while files are still uploading: a run started now would miss them. */
  syncStartButtons() {
    const busy = !!this.uploading;
    for (const b of this.startButtons || []) {
      b.setDisabled(busy);
      b.title = busy ? this.t("ready.wait_upload") : "";
    }
  }

  async openFolder(which) {
    try {
      await this.api.post("/api/open-folder", { which });
    } catch (err) {
      this.toastError(err);
    }
  }

  async removePending(it) {
    try {
      await this.api.del(this.api.url("/api/designs/files", { path: it.name }));
      this.ctx.toast({ tone: "info", title: this.t("pending.removed", { name: it.name }) });
    } catch (err) {
      this.toastError(err);
    }
    await this.loadPending();
  }

  // ------------------------------------------------------------------ dropping files

  /** Whether the template makes digital drafts: only then is "dosyalar" a download folder. */
  isDigital() {
    return !!(this.pending && this.pending.template && this.pending.template.digital);
  }

  /**
   * Files the drop zone turned away (it takes images only). A digital product's download
   * files — a PDF or ZIP in a dropped folder's "dosyalar" / "files" subfolder — are kept
   * and go up with the same drop (onFiles follows this call at once); the rest are said.
   * With a physical template a "dosyalar" folder means nothing special: its non-images
   * are turned away like any other.
   */
  onRejected(list) {
    const { t } = this;
    const digital = this.isDigital();
    // Download files, and files nested deeper in "dosyalar" (handleFiles says why those
    // do not go, once, together with any nested images of the same drop).
    const keep = list.filter((x) => x && x.file && downloadOf(x.path, digital));
    const other = list.filter((x) => !(x && x.file && downloadOf(x.path, digital)));
    if (other.length) {
      this.ctx.toast({ tone: "warning", title: t("upload.rejected", { n: other.length }), message: other.slice(0, 3).map((x) => baseName(x.path)).join(", ") });
    }
    if (!keep.length) return;
    this.stashed = keep;
    queueMicrotask(() => {
      // Only download files were dropped (no image came with them): upload them alone.
      if (this.stashed) this.handleFiles([]);
    });
  }

  /** Files deeper inside "dosyalar" than its own files: Etsy takes files, not folders. */
  toastNested(list) {
    this.ctx.toast({ tone: "warning", title: this.t("upload.nested_downloads", { n: list.length }), message: list.slice(0, 3).map((x) => x.path).join(", ") });
  }

  async handleFiles(list) {
    const { t } = this;
    const digital = this.isDigital();
    let files = list.filter((x) => x && x.file);
    if (this.stashed) {
      files = files.concat(this.stashed);
      this.stashed = null;
    }
    if (this.uploading) return;
    // Images nested inside a "dosyalar" subfolder are never a product of their own.
    const nested = files.filter((x) => (downloadOf(x.path, digital) || {}).nested);
    if (nested.length) {
      this.toastNested(nested);
      files = files.filter((x) => !(downloadOf(x.path, digital) || {}).nested);
    }
    if (!files.length) return;
    if (files.length > MAX_FILES) {
      this.ctx.toast({ tone: "warning", title: t("upload.too_many", { n: MAX_FILES }) });
      files = files.slice(0, MAX_FILES);
    }
    const hasFolders = files.some((x) => (x.path || "").includes("/"));
    let mode = "designs";
    if (hasFolders) {
      mode = await this.chooseFolderMode(files);
      if (!mode) return;
    }
    const entries = planUploads(files, mode, digital);
    const skipped = mode === "products" ? 0 : files.filter((x) => downloadOf(x.path, digital)).length;
    if (skipped) this.ctx.toast({ tone: "warning", title: t("upload.downloads_skipped", { n: skipped }) });
    if (!entries.length) return;
    await this.upload(entries);
  }

  chooseFolderMode(files) {
    const { t } = this;
    const digital = this.isDigital();
    const dirs = new Map();
    let downloads = 0;
    for (const x of files) {
      const dl = downloadOf(x.path, digital);
      if (dl) {
        downloads += 1;
        // Its product folder counts as a folder of the drop, photos or not.
        const parts = (x.path || "").split("/").filter(Boolean);
        const dir = parts.slice(0, -2).join("/");
        if (!dirs.has(dir)) dirs.set(dir, 0);
        continue;
      }
      const parts = (x.path || "").split("/").filter(Boolean);
      if (parts.length > 1) {
        const dir = parts.slice(0, -1).join("/");
        dirs.set(dir, (dirs.get(dir) || 0) + 1);
      }
    }
    const big = [...dirs.values()].some((n) => n > MAX_PHOTOS);
    return new Promise((resolve) => {
      // Download files in a "dosyalar" folder only make sense for product folders.
      let choice = downloads ? "products" : "designs";
      let result = null;
      const option = (id, title, sub, ic) =>
        h(
          "label",
          { class: cx("dz-option", id === choice && "is-selected"), dataset: { id } },
          h("input", {
            type: "radio",
            name: "dz-folder-mode",
            value: id,
            checked: id === choice,
            onChange: () => {
              choice = id;
              for (const o of box.querySelectorAll(".dz-option")) o.classList.toggle("is-selected", o.dataset.id === id);
            },
          }),
          h("span", { class: "dz-option-icon" }, icon(ic, { size: 16 })),
          h("span", { class: "dz-option-text" }, h("strong", null, title), h("span", null, sub)),
        );
      const box = h(
        "div",
        { class: "dz-options" },
        option("designs", t("folders.designs"), t("folders.designs_sub"), "image"),
        option("products", t("folders.products"), t("folders.products_sub"), "folder"),
      );
      const m = this.ctx.modal({
        title: t("folders.title"),
        subtitle: t("folders.summary", { folders: dirs.size, files: files.length }),
        width: 500,
        body: [
          box,
          big ? h("p", { class: "dz-modal-hint" }, t("folders.big", { n: MAX_PHOTOS })) : null,
          downloads ? h("p", { class: "dz-modal-hint" }, icon("download", { size: 13 }), " ", t("folders.downloads", { n: downloads })) : null,
        ],
        actions: [
          { label: t("common.cancel"), variant: "secondary" },
          {
            label: t("folders.upload"),
            variant: "primary",
            icon: "upload",
            onClick: ({ close }) => {
              result = choice;
              close();
            },
          },
        ],
        onClose: () => resolve(result),
      });
      return m;
    });
  }

  async upload(entries) {
    const { t } = this;
    const batch = randomId();
    const controller = new AbortController();
    const state = {
      controller,
      rows: entries.map((e) => ({ ...e, status: "queued", fraction: 0, message: "" })),
      done: 0,
    };
    this.uploading = state;
    this.ctx.setDirty(true); // a reload or a closed tab would cut the upload off: ask
    this.syncStartButtons();
    if (this.dz) {
      this.dz.setDisabled(true);
      this.dz.hidden = true; // the upload list takes the drop zone's place meanwhile
    }
    const total = state.rows.length;
    const bar = progressBar({ value: 0, max: total, label: t("upload.title") });
    const countEl = h("span", { class: "dz-up-count num" });
    const listEl = h("div", { class: "dz-up-list" });
    const stopBtn = button({ label: t("upload.cancel"), size: "sm", variant: "secondary", onClick: () => controller.abort() });
    const panel = h(
      "section",
      { class: "card dz-up is-active" },
      h("div", { class: "dz-up-head" }, spinner({ size: 16, tone: "accent" }), h("strong", null, t("upload.title")), countEl, h("span", { class: "spacer" }), stopBtn),
      bar.el,
      listEl,
    );
    mount(this.uploadHost, panel);
    const rowEls = state.rows.map((r) => {
      const fill = h("span", { class: "dz-up-fill" });
      const status = h("span", { class: "dz-up-status" });
      const el = h(
        "div",
        { class: "dz-up-row" },
        icon(r.deliverable ? "download" : "file", { size: 14 }),
        h("span", { class: "dz-up-name ellipsis", title: r.label }, r.label),
        h("span", { class: "dz-up-size num" }, bytes(r.file.size)),
        h("span", { class: "dz-up-bar" }, fill),
        status,
      );
      r.el = el;
      r.fill = fill;
      r.statusEl = status;
      return el;
    });
    // Only the first rows are drawn at once; the rest are appended as their turn comes.
    const INITIAL = 60;
    listEl.append(...rowEls.slice(0, INITIAL));
    let appended = Math.min(INITIAL, rowEls.length);
    const paint = (r) => {
      r.fill.style.width = `${Math.round((r.status === "queued" ? 0 : r.status === "uploading" ? r.fraction : 1) * 100)}%`;
      r.el.className = cx("dz-up-row", `is-${r.status}`);
      const text = {
        queued: "",
        uploading: `${Math.round(r.fraction * 100)}%`,
        done: "",
        duplicate: t("upload.duplicate"),
        replaced: t("upload.replaced"),
        known: t("upload.known"),
        ignored: t("upload.ignored"),
        error: r.message,
        skipped: t("upload.skipped"),
      }[r.status];
      mount(
        r.statusEl,
        r.status === "done" ? icon("check", { size: 13, strokeWidth: 2.6 }) : r.status === "error" ? [icon("alert", { size: 12 }), " ", text] : text,
      );
      r.statusEl.title = r.status === "error" ? r.message : "";
    };
    const refreshHead = () => {
      countEl.textContent = t("upload.progress", { done: state.done, total });
      bar.update(state.done, total);
    };
    refreshHead();
    state.rows.forEach(paint);

    let next = 0;
    const worker = async (limit) => {
      while (next < limit && !controller.signal.aborted && !this.destroyed) {
        const i = next;
        next += 1;
        while (appended <= i + 10 && appended < rowEls.length) {
          listEl.appendChild(rowEls[appended]);
          appended += 1;
        }
        const r = state.rows[i];
        if (r.file.size > MAX_BYTES) {
          r.status = "error";
          r.message = t("upload.too_large", { mb: 50 });
        } else {
          r.status = "uploading";
          paint(r);
          try {
            const res = await this.api.upload("/api/designs/files", r.file, {
              // A download for a product already in the folder goes without the batch.
              query: r.attach ? { path: r.path } : { path: r.path, batch },
              signal: controller.signal,
              onProgress: ({ fraction }) => {
                r.fraction = fraction;
                paint(r);
              },
            });
            r.result = res;
            r.status = res.known ? "known" : res.ignored ? "ignored" : res.duplicate ? "duplicate" : res.replaced ? "replaced" : "done";
          } catch (err) {
            if (this.api.isAbort(err)) {
              r.status = "skipped";
            } else {
              r.status = "error";
              r.message = this.errorText(err);
            }
          }
        }
        state.done += 1;
        paint(r);
        refreshHead();
      }
    };
    // Every photo is in before the first download file starts (planUploads puts them
    // last): a product folder's batch claim is always made by a photo, never by a small
    // PDF that overtook it and would open a "-2" copy of the product.
    const pool = (limit) => Promise.all(Array.from({ length: Math.max(0, Math.min(UPLOAD_PARALLEL, limit - next)) }, () => worker(limit)));
    const firstDownload = state.rows.findIndex((r) => r.deliverable);
    if (firstDownload > 0) await pool(firstDownload);
    await pool(state.rows.length);
    for (const r of state.rows) {
      if (r.status === "queued") {
        r.status = "skipped";
        paint(r);
      }
    }
    this.uploading = null;
    if (this.destroyed) return;
    this.ctx.setDirty(false);
    this.syncStartButtons();
    if (this.view !== "idle") return;
    panel.classList.remove("is-active");
    if (this.dz) {
      this.dz.setDisabled(false);
      this.dz.hidden = false;
    }

    const count = (s) => state.rows.filter((r) => r.status === s).length;
    const failed = count("error");
    const known = count("known");
    const skipped = count("skipped");
    const saved = count("done") + count("duplicate") + count("replaced");
    const head = panel.querySelector(".dz-up-head");
    mount(
      head,
      h("span", { class: cx("dz-up-done", failed && "has-errors") }, icon(failed ? "alert" : "check", { size: 15 })),
      h("strong", null, saved ? t("upload.done", { n: saved }) : t("upload.none")),
      failed ? h("span", { class: "dz-up-failed" }, t("upload.failed", { n: failed })) : null,
      known ? h("span", { class: "dz-up-known" }, t("upload.known_n", { n: known })) : null,
      skipped ? h("span", { class: "dz-up-known" }, t("upload.skipped_n", { n: skipped })) : null,
      h("span", { class: "spacer" }),
      button({ label: t("common.close"), size: "sm", variant: "ghost", onClick: () => mount(this.uploadHost) }),
    );
    if (!failed && !known) {
      // Everything went in: the list has done its job.
      setTimeout(() => {
        if (panel.isConnected) mount(this.uploadHost);
      }, 900);
    }
    const p = await this.loadPending();
    if (p && saved > 0 && !controller.signal.aborted) this.openStart(p);
  }

  // ------------------------------------------------------------------ starting a run

  async openStart(given) {
    const { t } = this;
    if (this.uploading) {
      this.ctx.toast({ tone: "info", title: t("ready.wait_upload") });
      return;
    }
    let p = given;
    if (!p) {
      p = await this.loadPending();
      if (!p) return;
    }
    const blockers = p.blockers;
    if (blockers.length) {
      this.openBlocked(p, blockers);
      return;
    }
    const notes = [];
    const kind = listingType(p.template && p.template.listing_type);
    const digital = DIGITAL.has(kind);
    // What the drafts will be: a digital template's are digital, with a download file
    // each (the design itself; a folder's "dosyalar"). Physical runs read as before.
    if (digital) {
      const folders = p.items.some((x) => x.kind === "folder" && !x.junk_reason && !x.too_many);
      notes.push(
        h(
          "div",
          { class: "dz-modal-type" },
          h("span", { class: "dz-modal-type-icon" }, icon("download", { size: 15 })),
          h(
            "div",
            { class: "dz-modal-type-text" },
            h("span", { class: "dz-modal-type-main" }, h("strong", null, t(`ready.type_${kind}`)), " · ", t(`ready.type_${kind}_sub`)),
            folders ? h("span", { class: "dz-modal-type-more" }, t("ready.type_folders", { max: MAX_DOWNLOADS })) : null,
          ),
        ),
      );
    }
    const junk = p.items.filter((x) => x.junk_reason || x.too_many).length;
    if (junk) notes.push(infoNote({ tone: "warning", icon: "alert", text: t("ready.junk", { n: junk }) }));
    const noPhotos = p.items.filter((x) => x.no_photos && !x.junk_reason).length;
    if (noPhotos) notes.push(infoNote({ tone: "warning", icon: "image", text: t("ready.no_photos", { n: noPhotos }) }));
    const noFiles = digital ? p.items.filter((x) => !x.junk_reason && !x.too_many && !x.no_photos && x.deliverable_problem).length : 0;
    if (noFiles) notes.push(infoNote({ tone: "warning", icon: "download", text: t("ready.deliverables", { n: noFiles }) }));
    if (p.warnings.includes("no_mockups")) notes.push(infoNote({ tone: "warning", icon: "image", text: t("ready.no_mockups") }));
    if (p.warnings.includes("no_shipping_profile")) notes.push(infoNote({ tone: "warning", icon: "truck", text: t("ready.no_shipping") }));
    if (p.warnings.includes("quota")) notes.push(infoNote({ tone: "warning", icon: "clock", text: t("ready.quota") }));
    const estimate = h(
      "p",
      { class: "dz-modal-hint" },
      typeof p.quota_remaining === "number"
        ? t("ready.estimate", { n: number(p.estimate_requests), left: number(p.quota_remaining) })
        : t("ready.estimate_nq", { n: number(p.estimate_requests) }),
    );
    const templateTitle = (p.template && p.template.title && shortTitle(p.template.title)) || t("chip.template_unnamed");
    // Which mockups the drafts get: the same count as the chip, and the way to change it.
    const mk = p.mockups;
    if (mk.enabled) {
      notes.push(
        h(
          "p",
          { class: "dz-modal-mockups" },
          icon("image", { size: 14 }),
          h("span", { class: "dz-modal-mockups-text" }, mk.over_limit ? t("ready.mockups_over", { n: mk.enabled, on: mk.switched_on }) : t("ready.mockups_main", { name: mk.main || "" })),
          h(
            "a",
            {
              href: "/kurulum/mockuplar",
              class: "dz-modal-link",
              onClick: (e) => {
                e.preventDefault();
                m.close();
                this.ctx.navigate("/kurulum/mockuplar");
              },
            },
            t("ready.mockups_link"),
            icon("arrow-right", { size: 12 }),
          ),
        ),
      );
    }
    const m = this.ctx.modal({
      title: t("ready.title", { n: p.runnable ?? p.count }),
      subtitle: t("ready.sub", { n: mk.enabled, mockups: mk.enabled, template: templateTitle }),
      width: 470,
      class: "dz-ready",
      body: [...notes, estimate],
      actions: [
        button({
          label: t("ready.dry"),
          variant: "ghost",
          size: "sm",
          title: t("ready.dry_hint"),
          class: "dz-dry",
          onClick: () => {
            m.close();
            this.startRun(true);
          },
        }),
        button({
          label: t("ready.start"),
          icon: "zap",
          variant: "primary",
          size: "lg",
          onClick: () => {
            m.close();
            this.startRun(false);
          },
        }),
      ],
    });
  }

  openBlocked(p, blockers) {
    const { t } = this;
    let m = null;
    const go = (path) => () => {
      m.close();
      this.ctx.navigate(path);
    };
    const rows = blockers.map((b) => {
      let action = null;
      if (SETUP_LINKS[b]) action = button({ label: t(SETUP_LINKS[b] === "/kurulum/sablon" ? "blocked.go_template" : "blocked.go_connect"), size: "sm", iconRight: "arrow-right", onClick: go(SETUP_LINKS[b]) });
      else if (b === "locked") action = button({ label: t("blocked.unlock"), size: "sm", onClick: () => { m.close(); this.unlock(); } });
      else if (b === "offline") action = button({ label: t("blocked.retry"), size: "sm", icon: "refresh", autoLoading: true, onClick: async () => { await this.ctx.refreshStatus(true).catch(() => null); m.close(); this.openStart(); } });
      else if (b === "running") action = button({ label: t("blocked.show_run"), size: "sm", onClick: async () => { m.close(); await this.showActive(); } });
      const text = b === "template_invalid" ? t("blocked.template_invalid", { message: (p.template && p.template.invalid) || "" }) : b === "locked" ? this.lockText(p) : t(`blocked.${b}`);
      return infoNote({ tone: b === "empty" ? "info" : "warning", icon: b === "empty" ? "info" : "alert", text, action });
    });
    const onlyEmpty = blockers.length === 1 && (blockers[0] === "empty" || blockers[0] === "junk_only");
    m = this.ctx.modal({
      title: onlyEmpty ? t("blocked.empty_title") : t("blocked.title"),
      width: 520,
      body: rows,
      actions: [{ label: t("common.close"), variant: "secondary" }],
    });
  }

  async showActive() {
    try {
      const jobs = await this.api.get("/api/jobs", { kind: "designs", active: 1 });
      if (jobs && jobs.length) await this.showJob(jobs[0].id);
    } catch (err) {
      this.toastError(err);
    }
  }

  /** The lock note: a crashed run's lock, or one a running process still holds. */
  lockText(p) {
    const lock = p && p.lock;
    if (lock && lock.stale) return this.t("blocked.locked_stale");
    if (lock && lock.alive && lock.pid) return this.t("blocked.locked_active", { pid: lock.pid });
    return this.t("blocked.locked");
  }

  async unlock() {
    const { t } = this;
    const lock = this.pending && this.pending.lock;
    const active = !!(lock && lock.alive && lock.pid);
    // A lock whose process still runs gets the stronger question straight away.
    const ok = await this.ctx.confirm(
      active
        ? { title: t("unlock.active_title"), message: t("unlock.active_msg", { pid: lock.pid }), confirmLabel: t("blocked.unlock"), danger: true }
        : { title: t("unlock.title"), message: t("unlock.msg"), confirmLabel: t("blocked.unlock"), danger: true },
    );
    if (!ok) return;
    try {
      try {
        await this.api.post("/api/designs/unlock", active ? { confirm: true, force: true } : { confirm: true });
      } catch (err) {
        if (!err || err.code !== "lock_active") throw err;
        // The process that holds the lock still runs (or its id went to another program).
        const sure = await this.ctx.confirm({ title: t("unlock.active_title"), message: t("unlock.active_msg", { pid: (err.params && err.params.pid) || "?" }), confirmLabel: t("blocked.unlock"), danger: true });
        if (!sure) return;
        await this.api.post("/api/designs/unlock", { confirm: true, force: true });
      }
      this.ctx.toast({ tone: "success", title: t("unlock.done") });
    } catch (err) {
      this.toastError(err);
    } finally {
      await this.loadPending();
    }
  }

  async startRun(dryRun) {
    this.starting = true;
    try {
      const job = await this.api.post("/api/designs/start", { dry_run: !!dryRun });
      await this.showJob(job.id, job);
    } catch (err) {
      if (err && err.code === "setup_incomplete" && this.pending) {
        const blockers = (err.params && err.params.blockers) || [];
        await this.loadPending();
        this.openBlocked(this.pending || { blockers, template: null }, blockers);
        return;
      }
      this.toastError(err);
    } finally {
      this.starting = false;
    }
  }

  // ------------------------------------------------------------------ review (history) items

  openReview() {
    const { t } = this;
    const p = this.pending;
    if (!p || !p.review || !p.review.length) return;
    let m = null;
    const rows = p.review.map((r) => {
      // The library's own words (English) are only the muted detail; what happened is
      // said with a translated code: partial / drafted / uncertain.
      const problem = r.problem || (r.status === "partial" ? "partial" : r.listing_id ? "drafted" : "uncertain");
      const open = () => {
        if (m) m.close();
        this.ctx.navigate(`/ilanlar/${r.listing_id}`);
      };
      // A design with a draft on Etsy is never offered for a retry: that would be a
      // second draft of it. The draft is opened instead, to finish it by hand.
      const action = r.listing_id
        ? button({ label: t("review.open_draft"), size: "sm", variant: "secondary", iconRight: "arrow-right", onClick: open })
        : button({
            label: t("review.retry"),
            size: "sm",
            icon: "refresh",
            onClick: async () => {
              const ok = await this.ctx.confirm({ title: t("review.retry_title", { name: r.name }), message: t("review.retry_msg"), confirmLabel: t("review.retry"), danger: true });
              if (!ok) return;
              try {
                await this.api.post("/api/designs/review/forget", { name: r.name, confirm: true });
                this.ctx.toast({ tone: "success", title: t("review.retried", { name: r.name }) });
                m.close();
                await this.loadPending();
              } catch (err) {
                this.toastError(err);
              }
            },
          });
      return h(
        "div",
        { class: "dz-review-row" },
        h(
          "div",
          { class: "dz-review-text" },
          h("strong", { class: "ellipsis", title: r.name }, r.name),
          h(
            "span",
            { class: "dz-review-meta" },
            badge({ text: t(`review.badge.${problem}`), tone: problem === "uncertain" ? "danger" : "warning", size: "sm" }),
            r.listing_id ? h("span", { class: "dz-review-id num" }, t("review.listing", { id: r.listing_id })) : null,
          ),
          h("span", { class: "dz-review-why" }, t(`review.problem.${problem}`)),
          r.message ? h("span", { class: "dz-review-msg", title: r.message }, r.message) : null,
        ),
        action,
      );
    });
    m = this.ctx.modal({
      title: t("review.title"),
      width: 600,
      body: [h("p", { class: "dz-modal-hint" }, t("review.intro")), h("div", { class: "dz-review" }, rows)],
      actions: [{ label: t("common.close"), variant: "secondary" }],
    });
  }

  // ------------------------------------------------------------------ run view (t220 / t240)

  async showJob(jobId, summary) {
    let job = null;
    this.run = null;
    this.earlyEvents = [];
    try {
      job = await this.api.get(`/api/jobs/${encodeURIComponent(jobId)}`, null, { signal: this.ctx.signal });
    } catch (err) {
      if (this.api.isAbort(err)) return;
      if (summary) job = { ...summary, state: {} };
      else {
        this.earlyEvents = null;
        await this.showIdle();
        return;
      }
    }
    const early = this.earlyEvents || [];
    this.earlyEvents = null;
    if (this.destroyed) return;
    const model = this.modelFromJob(job);
    if (JOB_FINAL.has(model.status) && !model.items.length && !model.error) {
      // A run that had nothing to do (or was stopped before it began): nothing to show.
      storageSet(DISMISS_KEY, job.id);
      await this.showIdle();
      return;
    }
    this.run = model;
    // Events that came while the state was on its way: the newer of the two wins, per
    // product (updated_at), so a product that finished meanwhile is not shown running.
    for (const ev of early) this.onJobEvent(ev);
    this.dirtyRows.clear();
    this.enterRun();
  }

  showSaved(saved) {
    const s = saved.summary || {};
    const items = Array.isArray(saved.items) ? saved.items : [];
    this.run = {
      jobId: null,
      v: s.batch || "saved",
      saved: true,
      status: s.cancelled ? "cancelled" : "done",
      dryRun: !!s.dry_run,
      items,
      counts: this.countsFrom(items, s),
      startedAt: s.started_at,
      finishedAt: s.finished_at,
      result: s,
      template: saved.template || null,
      mockups: saved.mockups || {},
      listingType: listingType(s.listing_type || (saved.template && saved.template.listing_type)),
      concurrency: 3,
      error: null,
    };
    this.enterRun();
  }

  countsFrom(items, s) {
    const c = { total: items.length, done: 0, created: 0, errors: 0, warnings: 0, checked: 0, cancelled_items: 0, current: null };
    for (const it of items) {
      if (FINAL.has(it.status)) c.done += 1;
      if (it.status === "ok" || it.status === "partial") c.created += 1;
      if (it.status === "error") c.errors += 1;
      if (it.status === "checked") c.checked += 1;
      if (it.status === "cancelled") c.cancelled_items += 1;
      c.warnings += (it.warnings || []).length;
    }
    if (s && typeof s.total === "number") c.total = Math.max(c.total, s.total);
    return c;
  }

  modelFromJob(job) {
    const st = job.state || {};
    const items = Array.isArray(st.items) ? st.items : [];
    return {
      jobId: job.id,
      v: st.batch || job.id,
      saved: false,
      status: job.status,
      dryRun: !!(st.dry_run ?? (job.params && job.params.dry_run)),
      items,
      counts: { ...this.countsFrom(items, st.result), total: st.total || items.length, current: st.current ?? null },
      startedAt: job.started_at || st.started_at || job.created_at,
      finishedAt: job.finished_at || st.finished_at,
      result: st.result || job.result || null,
      template: st.template || null,
      mockups: st.mockups || {},
      listingType: listingType(st.listing_type || (st.template && st.template.listing_type)),
      concurrency: st.concurrency || 3,
      error: job.error,
    };
  }

  isRunning() {
    return this.run && !JOB_FINAL.has(this.run.status);
  }

  enterRun() {
    const { t } = this;
    this.view = "run";
    this.stopTimers();
    this.selected = null;
    this.expanded = false;
    this.headKey = null;
    this.pipeEls = null;
    this.headEl = h("div", { class: "dr-head-host" });
    this.pipeHost = h("div", { class: "dr-pipe-host" });
    this.sideHost = h("div", { class: "dr-side-host" });
    mount(this.el, this.headEl, h("div", { class: "dr-grid" }, this.pipeHost, this.sideHost));
    this.renderRun();
    this.every(1000, () => this.tickElapsed());
    if (this.isRunning()) {
      // Belt and braces: re-read the state now and then, in case an event was missed.
      this.every(8000, () => this.refreshJob());
    }
    this.ctx.setHeader({ subtitle: t("subtitle") });
  }

  async refreshJob() {
    if (!this.run || !this.run.jobId || this.destroyed) return;
    try {
      const job = await this.api.get(`/api/jobs/${encodeURIComponent(this.run.jobId)}`, null, { signal: this.ctx.signal });
      if (!this.run || job.id !== this.run.jobId) return;
      const model = this.modelFromJob(job);
      // Live events can be newer than the copied state: keep whichever item moved last.
      model.items = model.items.map((it) => {
        const mine = this.run.items[it.index];
        return mine && (mine.updated_at || 0) > (it.updated_at || 0) ? mine : it;
      });
      const wasRunning = this.isRunning();
      this.run = model;
      if (this.pipeEls) this.pipeEls.order = ""; // redraw the rows from the fresh state
      if (wasRunning && !this.isRunning()) this.onFinished();
      this.renderRun();
    } catch (err) {
      if (!this.api.isAbort(err) && err.code === "not_found") {
        // The app restarted: the job is gone; show what was saved, else the drop zone.
        this.run = null;
        await this.showIdle();
      }
    }
  }

  onReconnect() {
    if (this.view === "run") this.refreshJob();
    else if (this.view === "idle") this.loadPending();
  }

  onJob(job) {
    if (!job || job.kind !== "designs" || this.destroyed) return;
    if (this.view === "idle" && !JOB_FINAL.has(job.status) && !this.uploading && !this.starting) {
      this.showJob(job.id);
      return;
    }
    if (this.view !== "run" || !this.run || job.id !== this.run.jobId) return;
    const was = this.run.status;
    this.run.status = job.status;
    if (job.started_at) this.run.startedAt = job.started_at;
    if (JOB_FINAL.has(job.status) && !JOB_FINAL.has(was)) {
      this.run.finishedAt = job.finished_at;
      this.run.error = job.error;
      this.refreshJob();
    } else {
      this.scheduleRender();
    }
  }

  onFinished() {
    this.stopTimers();
    this.every(1000, () => this.tickElapsed());
  }

  onJobEvent(ev) {
    if (!ev || ev.kind !== "designs") return;
    if (!this.run) {
      // The run is being loaded (GET /api/jobs/{id}): an event now is newer than, or as
      // new as, the state that request returns. Kept, and applied once it has arrived.
      if (this.earlyEvents && this.earlyEvents.length < 2000) this.earlyEvents.push(ev);
      return;
    }
    if (ev.job_id !== this.run.jobId) return;
    const data = ev.data || {};
    if (ev.type === "batch") {
      if (this.run.items.length) return; // once per run: the loaded state already has it
      this.run.items = Array.isArray(data.items) ? data.items : [];
      if (data.state) this.run.counts = { ...this.run.counts, ...data.state };
      this.pipeEls = null;
      this.scheduleRender();
      return;
    }
    if (ev.type === "item" && data.item) {
      const item = data.item;
      if (typeof item.index !== "number") return;
      const mine = this.run.items[item.index];
      if (mine && (mine.updated_at || 0) > (item.updated_at || 0)) return; // older than what is shown
      this.run.items[item.index] = item;
      if (data.state) this.run.counts = { ...this.run.counts, ...data.state };
      if (this.run.status === "queued") this.run.status = "running";
      this.dirtyRows.add(item.index);
      this.scheduleRender();
    }
  }

  scheduleRender() {
    if (this.rafPending) return;
    this.rafPending = true;
    requestAnimationFrame(() => {
      this.rafPending = false;
      if (!this.destroyed && this.view === "run") this.renderRun();
    });
  }

  renderRun() {
    if (!this.run) return;
    const grid = this.el.querySelector(".dr-grid");
    if (grid) {
      grid.hidden = !this.run.items.length && !this.isRunning();
      // The cards share a bottom edge, except when the whole list is shown: the side
      // card then keeps its own height, its "Taslağı aç" in reach.
      grid.classList.toggle("is-expanded", this.expanded);
    }
    this.renderHead();
    this.renderPipe();
    this.renderSide();
    this.renderHeaderBadge();
  }

  runPhase() {
    const r = this.run;
    if (this.isRunning()) return "running";
    if (r.status === "error") return r.items.length ? "stopped" : "failed";
    const res = r.result || {};
    if (res.stopped) return "stopped";
    if (r.status === "cancelled" || res.cancelled) return "cancelled";
    if (r.dryRun) return "checked";
    if (!r.counts.created && r.counts.errors) return "none";
    return "done";
  }

  renderHeaderBadge() {
    const { t } = this;
    const phase = this.runPhase();
    const key = `badge:${phase}`;
    if (this.badgeKey === key) return;
    this.badgeKey = key;
    const res = this.run.result || {};
    const folder =
      phase !== "running" && res.csv && !this.run.saved
        ? iconButton({ icon: "folder", title: t("done.open_drafts"), variant: "ghost", onClick: () => this.openFolder("drafts") })
        : null;
    const spec = {
      running: { text: t("badge.running"), tone: "accent", dot: true },
      done: { text: t("badge.done"), tone: "success", icon: "check" },
      checked: { text: t("badge.checked"), tone: "info", icon: "check" },
      cancelled: { text: t("badge.stopped"), tone: "warning" },
      stopped: { text: t("badge.stopped"), tone: "danger" },
      none: { text: t("badge.failed"), tone: "danger" },
      failed: { text: t("badge.failed"), tone: "danger" },
    }[phase];
    this.ctx.setHeader({ actions: [folder, badge({ ...spec, size: "lg" })].filter(Boolean) });
  }

  /**
   * How far the run is, 0..1: the products finished (the headline's count, plus any
   * that failed), and the images of the draft being sent now. Products being prepared
   * ahead do not count yet, so "1 / 8" never stands next to "%46" (video t220: 18/50, %35).
   */
  progressFraction() {
    const r = this.run;
    const total = Math.max(r.counts.total || r.items.length, 1);
    let done = 0;
    for (const it of r.items) {
      if (!it) continue;
      const parts = (it.images_total || 0) + (it.files_total || 0);
      if (FINAL.has(it.status)) done += 1;
      else if (it.step === "draft" && parts) done += 0.9 * Math.min(1, ((it.images_uploaded || 0) + (it.files_uploaded || 0)) / parts);
    }
    return Math.min(1, done / total);
  }

  elapsedSeconds() {
    const r = this.run;
    const start = Number(r.startedAt) || 0;
    if (!start) return 0;
    const end = this.isRunning() ? Date.now() / 1000 : Number(r.finishedAt) || Date.now() / 1000;
    return Math.max(0, end - start);
  }

  tickElapsed() {
    if (this.elapsedEl) this.elapsedEl.textContent = duration(this.elapsedSeconds());
  }

  renderHead() {
    const { t } = this;
    const r = this.run;
    const phase = this.runPhase();
    const c = r.counts;
    if (phase === "running") {
      if (this.headKey !== "running") {
        this.headKey = "running";
        this.headCount = h("strong", { class: "dr-big num" });
        this.headTotal = h("span", { class: "dr-total num" });
        this.headPct = h("span", { class: "dr-pct num" });
        this.headBar = progressBar({ value: 0, max: 100, size: "md" });
        this.elapsedEl = h("strong", { class: "dr-elapsed-value num" });
        this.stopBtn = button({ label: t("run.stop"), icon: "stop", variant: "secondary", onClick: () => this.stopRun() });
        mount(
          this.headEl,
          h(
            "section",
            { class: "card dr-head is-running" },
            h("span", { class: "dr-tile" }, icon("zap", { size: 22 })),
            h(
              "div",
              { class: "dr-head-text" },
              h("h2", { class: "dr-head-title" }, r.dryRun ? t("run.title_dry") : t("run.title")),
              h("p", { class: "dr-head-sub" }, r.dryRun ? t("run.sub_dry") : t("run.sub", { n: r.concurrency || 3 })),
            ),
            h(
              "div",
              { class: "dr-progress" },
              h("div", { class: "dr-progress-top" }, this.headCount, this.headTotal, h("span", { class: "dr-count-label" }, r.dryRun ? t("run.count_dry") : t("run.count")), h("span", { class: "spacer" }), this.headPct),
              this.headBar.el,
            ),
            h("span", { class: "dr-divider", "aria-hidden": "true" }),
            h("div", { class: "dr-elapsed" }, icon("clock", { size: 16 }), h("div", null, h("span", { class: "dr-elapsed-label" }, t("run.elapsed")), this.elapsedEl)),
            this.stopBtn,
          ),
        );
      }
      const doneCount = r.dryRun ? c.checked || 0 : c.created || 0;
      this.headCount.textContent = String(doneCount);
      this.headTotal.textContent = `/ ${c.total || r.items.length}`;
      const pct = this.progressFraction();
      const shown = Math.floor(pct * 100);
      this.headPct.textContent = this.ctx.lang === "en" ? `${shown}%` : `%${shown}`;
      this.headBar.update(Math.round(pct * 1000) / 10, 100);
      this.tickElapsed();
      return;
    }
    const key = `final:${phase}:${c.created}:${c.errors}:${c.warnings}`;
    if (this.headKey === key) {
      this.tickElapsed();
      return;
    }
    this.headKey = key;
    this.elapsedEl = h("span", { class: "num" });
    const res = r.result || {};
    const left = Math.max(0, (c.total || r.items.length) - (c.created || 0) - (c.errors || 0) - (c.checked || 0));
    let tone = "success";
    let tileIcon = "check";
    let title;
    let sub;
    let actions;
    const newBtn = button({ label: t("done.new"), icon: "upload", variant: "secondary", onClick: () => this.dismissRun() });
    const viewBtn = button({ label: t("done.view"), icon: "list", variant: "primary", onClick: () => this.ctx.navigate("/ilanlar?tab=draft") });
    if (phase === "done") {
      title = t("done.title", { n: c.created });
      sub = t("done.sub");
      actions = [newBtn, viewBtn];
    } else if (phase === "checked") {
      tone = "info";
      title = t("done.dry_title", { n: c.checked });
      sub = t("done.dry_sub");
      actions = [newBtn, button({ label: t("done.dry_start"), icon: "zap", variant: "primary", onClick: () => this.openStartFromRun() })];
    } else if (phase === "cancelled") {
      tone = "warning";
      tileIcon = "pause";
      title = t("done.cancelled_title", { n: c.created });
      sub = t("done.cancelled_sub", { left });
      actions = [newBtn, button({ label: t("done.resume"), icon: "zap", variant: "secondary", onClick: () => this.openStartFromRun() }), viewBtn];
    } else if (phase === "stopped") {
      tone = "danger";
      tileIcon = "alert";
      const reason = res.stopped ? this.stoppedReason(res.stopped) : this.errorText(r.error || {});
      title = t("done.stopped_title", { reason });
      sub = t("done.stopped_sub", { n: c.created, left });
      actions = [newBtn, button({ label: t("done.resume"), icon: "refresh", variant: "secondary", onClick: () => this.openStartFromRun() }), viewBtn];
    } else if (phase === "none") {
      tone = "danger";
      tileIcon = "x";
      title = t("done.none_title");
      sub = t("done.none_sub");
      actions = [newBtn];
    } else {
      tone = "danger";
      tileIcon = "alert";
      title = t("done.failed_title");
      sub = r.error ? this.errorText(r.error) : "";
      actions = [newBtn, button({ label: t("common.retry"), icon: "refresh", variant: "primary", onClick: () => this.openStartFromRun() })];
      const fix = r.error && ({ template_gone: "/kurulum/sablon", setup_needed: "/kurulum/magaza", reconnect: "/kurulum/magaza", bad_keys: "/kurulum/magaza" })[r.error.code];
      if (fix) actions.splice(1, 0, button({ label: t(fix === "/kurulum/sablon" ? "done.go_template" : "blocked.go_connect"), variant: "secondary", iconRight: "arrow-right", onClick: () => this.ctx.navigate(fix) }));
    }
    const chips = [];
    if (phase !== "failed") {
      chips.push(badge({ text: t("done.errors", { n: c.errors || 0 }), tone: c.errors ? "danger" : "success", icon: c.errors ? "x" : "check" }));
      chips.push(badge({ text: t("done.warnings", { n: c.warnings || 0 }), tone: c.warnings ? "warning" : "neutral", icon: "alert" }));
      chips.push(h("span", { class: "badge tone-neutral dr-duration" }, icon("clock", { size: 12 }), t("done.duration"), " ", this.elapsedEl));
    }
    mount(
      this.headEl,
      h(
        "section",
        { class: cx("card dr-head is-final", `tone-${tone}`) },
        h("span", { class: "dr-tile" }, icon(tileIcon, { size: 22, strokeWidth: 2.4 })),
        h("div", { class: "dr-head-text" }, h("h2", { class: "dr-head-title" }, title), sub ? h("p", { class: "dr-head-sub" }, sub) : null),
        h("div", { class: "dr-chips" }, chips),
        h("span", { class: "spacer" }),
        h("div", { class: "dr-actions" }, actions),
      ),
    );
    this.tickElapsed();
  }

  async stopRun() {
    const { t } = this;
    if (!this.run || !this.run.jobId) return;
    const ok = await this.ctx.confirm({ title: t("run.stop_title"), message: t("run.stop_msg"), confirmLabel: t("run.stop_confirm"), danger: true });
    if (!ok || !this.isRunning()) return;
    try {
      if (this.stopBtn) {
        this.stopBtn.setLabel(t("run.stopping"));
        this.stopBtn.setLoading(true);
      }
      await this.api.post(`/api/jobs/${encodeURIComponent(this.run.jobId)}/cancel`, {});
    } catch (err) {
      if (this.stopBtn) this.stopBtn.setLoading(false);
      this.toastError(err);
    }
  }

  dismissRun() {
    if (this.run && this.run.jobId) storageSet(DISMISS_KEY, this.run.jobId);
    this.showIdle();
  }

  async openStartFromRun() {
    if (this.run && this.run.jobId) storageSet(DISMISS_KEY, this.run.jobId);
    await this.showIdle();
    this.openStart();
  }

  // --- pipeline table

  visibleIndices() {
    const r = this.run;
    const n = r.items.length;
    if (this.expanded || n <= VISIBLE_ROWS) return r.items.map((_, i) => i);
    if (this.isRunning()) {
      let first = r.items.findIndex((it) => it && !FINAL.has(it.status));
      if (first < 0) first = n - 1;
      const start = Math.max(0, Math.min(first - 1, n - VISIBLE_ROWS));
      return Array.from({ length: VISIBLE_ROWS }, (_, i) => start + i);
    }
    // Finished: problems first, so a failed product is never hidden behind "+ 43 more".
    const errors = r.items.filter((it) => it && it.status === "error").map((it) => it.index);
    const rest = r.items.map((_, i) => i).filter((i) => !errors.includes(i));
    return [...errors, ...rest].slice(0, VISIBLE_ROWS);
  }

  renderPipe() {
    const { t } = this;
    const r = this.run;
    if (!this.pipeEls) {
      const legend = h(
        "div",
        { class: "dr-legend" },
        h("span", { class: "dr-legend-item is-done" }, h("i"), t("legend.done")),
        h("span", { class: "dr-legend-item is-running" }, h("i"), t("legend.running")),
        h("span", { class: "dr-legend-item is-todo" }, h("i"), t("legend.todo")),
      );
      const countEl = h("span", { class: "dr-pipe-count" });
      const head = h(
        "div",
        { class: "dr-thead", role: "row" },
        h("span", { role: "columnheader" }, t("pipe.file")),
        ...STEPS.map((s) => h("span", { class: "dr-th-step", role: "columnheader" }, t(`step.${s}`))),
      );
      const body = h("div", { class: "dr-tbody", role: "rowgroup" });
      const foot = h("div", { class: "dr-tfoot" });
      mount(
        this.pipeHost,
        h(
          "section",
          { class: "card card-flush dr-pipe" },
          h("header", { class: "dr-pipe-head" }, h("h2", { class: "dr-pipe-title" }, t("pipe.title")), countEl, h("span", { class: "spacer" }), legend),
          h("div", { class: "dr-table", role: "table", "aria-label": t("pipe.title") }, head, body),
          foot,
        ),
      );
      this.pipeEls = { countEl, body, foot, rows: new Map(), order: "" };
      this.dirtyRows.clear();
    }
    const els = this.pipeEls;
    els.countEl.textContent = `· ${t("pipe.count", { n: r.counts.total || r.items.length })}`;
    const indices = this.visibleIndices().filter((i) => r.items[i]);
    const selectedIndex = this.sideIndex();
    if (!indices.length) {
      // The run is starting: the product list arrives with the first event.
      if (els.order !== "empty") {
        els.order = "empty";
        els.rows.clear();
        mount(els.body, Array.from({ length: 4 }, () => h("div", { class: "dr-row is-skeleton" }, skeleton({ lines: 2, height: 10, widths: ["40%", "22%"] }))));
      }
      this.dirtyRows.clear();
      this.renderFoot(indices);
      return;
    }
    const order = `${indices.join(",")}|${selectedIndex}`;
    if (order !== els.order) {
      els.order = order;
      els.rows.clear();
      mount(
        els.body,
        indices.map((i) => {
          const row = this.rowEl(r.items[i], i === selectedIndex);
          els.rows.set(i, row);
          return row;
        }),
      );
    } else {
      for (const i of this.dirtyRows) {
        const old = els.rows.get(i);
        if (!old || !r.items[i]) continue;
        const row = this.rowEl(r.items[i], i === selectedIndex);
        old.replaceWith(row);
        els.rows.set(i, row);
      }
    }
    this.dirtyRows.clear();
    this.renderFoot(indices);
  }

  rowStatus(it) {
    const { t } = this;
    const warns = (it.warnings || []).length;
    switch (it.status) {
      case "queued":
        return { text: t("row.queued"), tone: "muted" };
      case "running":
        if (it.step === "draft" && it.files_total && (it.images_uploaded || 0) >= (it.images_total || 0)) return { text: t("row.draft_files", { n: it.files_uploaded || 0, total: it.files_total }), tone: "accent" };
        if (it.step === "draft" && it.images_total) return { text: t("row.draft_images", { n: it.images_uploaded || 0, total: it.images_total }), tone: "accent" };
        return { text: t(`row.${it.step || "mockup"}`), tone: "accent" };
      case "waiting":
        return { text: this.run.dryRun ? t("row.check") : t("row.waiting"), tone: "accent" };
      case "ok":
        return warns ? { text: `${t("row.ok")} · ${t("row.warnings", { n: warns })}`, tone: "warning" } : { text: t("row.ok"), tone: "success" };
      case "checked":
        return warns ? { text: `${t("row.checked")} · ${t("row.warnings", { n: warns })}`, tone: "warning" } : { text: t("row.checked"), tone: "success" };
      case "partial":
        return { text: t("row.partial"), tone: "warning" };
      case "cancelled":
        return { text: it.error ? this.problemText(it.error) : t("row.cancelled"), tone: "muted" };
      case "error":
        return { text: it.error ? this.problemText(it.error) : t("row.error"), tone: "danger" };
      default:
        return { text: "", tone: "muted" };
    }
  }

  rowEl(it, current) {
    const { t } = this;
    const st = this.rowStatus(it);
    const states = STEPS.map((s) => it.steps[s] || "todo");
    const dots = stepDots({ states, labels: STEPS.map((s) => t(`step.${s}`)) });
    [...dots.children].forEach((node, i) => {
      const s = STEPS[i];
      const notes = [];
      if (states[i] === "warn") for (const w of it.warnings || []) if (w.step === s) notes.push(this.warnText(w));
      if (states[i] === "error" && it.error) notes.push(this.problemText(it.error) + (this.problemDetail(it.error) ? ` — ${it.error.message}` : ""));
      if (notes.length) node.title = `${t(`step.${s}`)}: ${notes.join(" · ")}`;
    });
    return h(
      "div",
      {
        class: cx("dr-row", current && "is-current", `is-${it.status}`),
        role: "row",
        tabindex: "0",
        dataset: { index: String(it.index) },
        onClick: () => this.select(it.index),
        onKeydown: (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            this.select(it.index);
          }
        },
      },
      h(
        "div",
        { class: "dr-file", role: "cell" },
        thumb({ src: this.thumbUrl(it.thumb_path, 96, this.run.v), size: 36, radius: 8, fit: "contain" }),
        h(
          "div",
          { class: "dr-file-text" },
          h("span", { class: "dr-name ellipsis", title: it.name }, it.name),
          h("span", { class: cx("dr-state ellipsis", `is-${st.tone}`), title: st.tone === "danger" && it.error ? it.error.message || st.text : st.text }, st.text),
        ),
      ),
      h("div", { class: "dr-dots", role: "cell" }, dots),
    );
  }

  renderFoot(indices) {
    const { t } = this;
    const r = this.run;
    const total = r.items.length;
    const hidden = r.items.filter((_, i) => !indices.includes(i));
    const els = this.pipeEls;
    const doneFrac = total ? r.items.filter((it) => it && FINAL.has(it.status)).length / total : 0;
    const running = this.isRunning();
    const phase = this.runPhase();
    const stopped = phase === "cancelled" || phase === "stopped";
    const key = `${hidden.length}|${Math.round(doneFrac * 200)}|${running}|${this.expanded}|${phase}`;
    if (els.footKey === key) return;
    els.footKey = key;
    if (!hidden.length && !this.expanded) {
      mount(els.foot);
      els.foot.hidden = true;
      return;
    }
    els.foot.hidden = false;
    const stack = h(
      "span",
      { class: "dr-stack" },
      hidden.slice(0, 3).map((it) => thumb({ src: this.thumbUrl(it.thumb_path, 64, this.run.v), size: 28, radius: 7, fit: "contain" })),
    );
    const line = h("span", { class: cx("dr-foot-line", !running && (stopped ? "is-stopped" : "is-done")) }, h("span", { style: { width: `${Math.round(doneFrac * 100)}%` } }));
    mount(
      els.foot,
      h(
        "button",
        {
          type: "button",
          class: "dr-more",
          onClick: () => {
            this.expanded = !this.expanded;
            this.pipeEls.order = "";
            this.pipeEls.footKey = "";
            this.el.querySelector(".dr-grid")?.classList.toggle("is-expanded", this.expanded);
            this.renderPipe();
          },
          title: this.expanded ? t("pipe.show_less") : t("pipe.show_all"),
        },
        this.expanded ? null : stack,
        this.expanded ? t("pipe.show_less") : t("pipe.more", { n: hidden.length }),
      ),
      line,
      h("span", { class: cx("dr-foot-note", !running && !stopped && "is-done") }, running ? t("pipe.parallel") : stopped ? t("pipe.stopped") : t("pipe.all_done")),
    );
  }

  select(index) {
    this.selected = this.selected === index ? null : index;
    if (this.pipeEls) this.pipeEls.order = "";
    this.renderPipe();
    this.renderSide();
  }

  // --- the "being prepared" / "sample draft" card

  sideIndex() {
    const r = this.run;
    if (!r || !r.items.length) return null;
    if (this.selected !== null && r.items[this.selected]) return this.selected;
    if (this.isRunning()) {
      const cur = r.counts.current;
      if (typeof cur === "number" && r.items[cur]) return cur;
      const running = r.items.find((it) => it && it.status === "running");
      if (running) return running.index;
      const lastDone = [...r.items].reverse().find((it) => it && FINAL.has(it.status));
      return lastDone ? lastDone.index : 0;
    }
    const good = r.items.find((it) => it && (it.status === "ok" || it.status === "checked"));
    const any = good || r.items.find((it) => it && it.status === "partial") || r.items[0];
    return any ? any.index : null;
  }

  renderSide() {
    const { t } = this;
    const r = this.run;
    const index = this.sideIndex();
    const it = index === null ? null : r.items[index];
    const live = this.isRunning() && this.selected === null;
    const key = it ? `${index}|${it.updated_at}|${it.status}|${live}|${this.selected}` : "none";
    if (this.sideKey === key) return;
    const prevIndex = this.sideIndexShown;
    const prevTitle = this.sideTitleShown;
    this.sideKey = key;
    this.sideIndexShown = index;
    if (!it) {
      mount(this.sideHost, h("section", { class: "card dr-side" }, h("p", { class: "dr-side-empty" }, t("side.empty"))));
      return;
    }
    const final = FINAL.has(it.status);
    let headLabel;
    if (live) headLabel = [h("span", { class: "dr-live-dot", "aria-hidden": "true" }), t("side.current")];
    else if (this.selected !== null) headLabel = [t("side.selected")];
    else headLabel = [icon("check", { size: 16, strokeWidth: 2.6 }), r.dryRun ? t("side.sample_dry") : t("side.sample")];
    let statusBadge = null;
    if (it.status === "ok") statusBadge = badge({ text: t("side.ready"), tone: "success", icon: "check" });
    else if (it.status === "partial") statusBadge = badge({ text: t("side.partial"), tone: "warning", icon: "alert" });
    else if (it.status === "error") statusBadge = badge({ text: t("side.failed"), tone: "danger", icon: "x" });
    else if (it.status === "checked") statusBadge = badge({ text: t("side.checked"), tone: "info", icon: "check" });
    else if (it.status === "cancelled") statusBadge = badge({ text: t("row.cancelled"), tone: "neutral" });
    const follow =
      this.isRunning() && this.selected !== null
        ? button({ label: t("side.follow"), size: "sm", variant: "ghost", icon: "play", onClick: () => this.select(this.selected) })
        : null;

    // Mockups: for a design, only the composites count and show here (the plain design
    // goes up too, but it is not a mockup: "6/6" with 6 mockups, as in the video t240);
    // a ready photo or a folder of photos shows its photos under "Görseller".
    const composited = it.mode === "composited" || (!it.mode && it.kind !== "folder");
    const flat = flatPath(it);
    const images = (it.images || []).filter((p) => p !== flat);
    const expected = composited && this.run.mockups && typeof this.run.mockups.enabled === "number" ? this.run.mockups.enabled : images.length;
    const mockupDone = it.steps.mockup === "done" || it.steps.mockup === "warn";
    const grid = h("div", { class: "dr-mockups" });
    // No mockup switched on: the plain design is all there is to show.
    const pictures = images.length || !flat ? images : [flat];
    const shown = pictures.slice(0, 4);
    shown.forEach((path, i) => {
      const extra = i === 3 && pictures.length > 4 ? pictures.length - 4 : 0;
      grid.appendChild(
        h(
          "a",
          { class: "dr-mockup", href: this.api.url("/api/files/workspace", { path, v: this.run.v }), target: "_blank", rel: "noopener", title: baseName(path) },
          h("img", { src: this.thumbUrl(path, 320, this.run.v), alt: "", loading: "lazy" }),
          extra ? h("span", { class: "dr-mockup-more num" }, `+${extra}`) : null,
        ),
      );
    });
    for (let i = shown.length; i < 4 && !mockupDone && !final; i += 1) grid.appendChild(h("span", { class: "dr-mockup is-empty" }, i === shown.length && it.steps.mockup === "running" ? spinner({ size: 18 }) : null));
    const short = composited && images.length < expected;
    const mockCount = mockupDone && (images.length || expected)
      ? h("span", { class: cx("dr-count num", short ? "is-warn" : "is-ok") }, icon(short ? "alert" : "check", { size: 12, strokeWidth: 2.6 }), `${images.length}/${composited ? expected : images.length}`)
      : images.length
        ? h("span", { class: "dr-count num" }, `${images.length}/${expected}`)
        : null;

    // Title
    const titleText = it.title || "";
    const titleBox = h("div", { class: cx("dr-titlebox", live && it.steps.title === "running" && "is-live") });
    if (titleText) {
      const animate = live && prevIndex === index && !prevTitle && titleText;
      if (animate) this.typeTitle(titleBox, titleText);
      else titleBox.textContent = titleText;
    } else if (!final) {
      titleBox.appendChild(skeleton({ lines: 2, height: 11, widths: ["92%", "58%"] }));
    } else {
      titleBox.appendChild(h("span", { class: "muted" }, "–"));
    }
    this.sideTitleShown = titleText;
    const titleCount = titleText ? h("span", { class: "dr-count is-ok num" }, icon("check", { size: 12, strokeWidth: 2.6 }), `${titleText.length}/140`) : null;

    // Tags
    const tags = it.tags || [];
    const tagsEl = h("div", { class: "dr-tags" }, tags.map((tag) => tagChip({ text: tag })));
    if (!final && tags.length < 13) for (let i = tags.length; i < 13; i += 1) tagsEl.appendChild(tagChip({ dashed: true }));
    const tagsCount = tags.length
      ? h("span", { class: cx("dr-count num", tags.length === 13 ? "is-ok" : "is-warn") }, icon(tags.length === 13 ? "check" : "alert", { size: 12, strokeWidth: 2.4 }), `${tags.length}/13`)
      : null;

    // Download files (digital templates): found at Kontrol, sent in the Taslak step after
    // the images. Before Kontrol only their number is known.
    let filesSection = null;
    if (DIGITAL.has(this.run.listingType) || it.files_total || (it.deliverables || []).length) {
      const paths = it.deliverables || [];
      const total = paths.length || it.files_total || 0;
      const sent = it.files_uploaded || 0;
      const draftDone = it.steps.draft === "done" || it.steps.draft === "warn";
      const sending = it.status === "running" && it.step === "draft";
      let filesCount = null;
      if (total && (draftDone || sending)) {
        const whole = sent >= total;
        filesCount = h("span", { class: cx("dr-count num", draftDone && (whole ? "is-ok" : "is-warn")) }, draftDone ? icon(whole ? "check" : "alert", { size: 12, strokeWidth: 2.6 }) : null, `${sent}/${total}`);
      } else if (total) {
        filesCount = h("span", { class: "dr-count num" }, t("side.files_n", { n: total }));
      }
      const list = paths.length
        ? h(
            "ul",
            { class: "dr-files" },
            paths.map((path, i) =>
              h(
                "li",
                { class: cx("dr-file-item", i < sent && "is-sent") },
                icon(i < sent ? "check" : "file", { size: 12, strokeWidth: i < sent ? 2.6 : 2 }),
                h("span", { class: "ellipsis", title: path }, baseName(path)),
              ),
            ),
          )
        : h("p", { class: "muted dr-none" }, final ? "–" : t("side.files_pending"));
      // One line when the files fit beside the heading (they usually do: one design file).
      filesSection = h(
        "div",
        { class: "dr-section dr-files-section" },
        h("div", { class: "dr-section-head" }, icon("download", { size: 14 }), h("span", { class: "dr-files-label" }, t("side.files")), list, h("span", { class: "spacer" }), filesCount),
      );
    }

    // Problems of this product
    const notes = [];
    if (it.error) notes.push(infoNote({ tone: it.status === "cancelled" ? "neutral" : "danger", icon: "alert", text: [h("b", null, this.problemText(it.error)), this.problemDetail(it.error) ? h("span", { class: "dr-detail" }, ` ${it.error.message}`) : null] }));
    const warns = it.warnings || [];
    if (warns.length) {
      notes.push(
        h(
          "ul",
          { class: "dr-warns" },
          warns.map((w) => h("li", { title: w.message || "" }, icon("alert", { size: 12 }), this.warnText(w))),
        ),
      );
    }

    // From the template
    const tpl = this.run.template || {};
    const fromTpl = [];
    if (tpl.price !== undefined && tpl.price !== null) fromTpl.push(h("span", { class: "dr-tpl-chip num" }, t("side.price", { price: money(tpl.price, tpl.currency || "USD") })));
    if (tpl.description) fromTpl.push(h("span", { class: "dr-tpl-chip" }, t("side.description")));
    if (this.run.listingType === "download") fromTpl.push(h("span", { class: "dr-tpl-chip" }, t("side.digital")));
    else fromTpl.push(tpl.shipping_profile ? h("span", { class: "dr-tpl-chip" }, t("side.shipping")) : h("span", { class: "dr-tpl-chip is-warn" }, t("side.no_shipping")));

    const openDraft =
      it.listing_id && (it.status === "ok" || it.status === "partial")
        ? h("a", { class: "dr-open", href: `/ilanlar/${it.listing_id}` }, t("side.open_draft"), icon("arrow-right", { size: 13 }))
        : null;

    mount(
      this.sideHost,
      h(
        "section",
        { class: cx("card dr-side", live && "is-live") },
        h(
          "header",
          { class: "dr-side-head" },
          thumb({ src: this.thumbUrl(it.thumb_path, 120, this.run.v), size: 42, radius: 9, fit: "contain" }),
          h(
            "div",
            { class: "dr-side-titles" },
            h("h2", { class: cx("dr-side-title", !live && this.selected === null && "is-sample") }, headLabel),
            h("p", { class: "dr-side-sub ellipsis", title: it.name }, `${it.name} · ${this.typeLabel(it)}`),
          ),
          follow || statusBadge,
        ),
        h(
          "div",
          { class: "dr-section" },
          h("div", { class: "dr-section-head" }, icon("image", { size: 14 }), h("span", null, composited ? t("side.mockups") : t("side.images")), h("span", { class: "spacer" }), mockCount),
          pictures.length || !final ? grid : h("p", { class: "muted dr-none" }, "–"),
        ),
        h(
          "div",
          { class: "dr-section" },
          h("div", { class: "dr-section-head" }, icon("sparkles", { size: 14 }), h("span", null, t("side.title")), h("span", { class: "spacer" }), titleCount),
          titleBox,
        ),
        h(
          "div",
          { class: "dr-section" },
          h("div", { class: "dr-section-head" }, icon("tag", { size: 14 }), h("span", null, t("side.tags")), h("span", { class: "spacer" }), tagsCount),
          tagsEl,
        ),
        filesSection,
        notes.length ? h("div", { class: "dr-section dr-notes" }, notes) : null,
        h(
          "footer",
          { class: "dr-side-foot" },
          icon("file", { size: 13 }),
          h("span", { class: "dr-tpl-label" }, t("side.from_template")),
          fromTpl,
          h("span", { class: "spacer" }),
          openDraft,
        ),
      ),
    );
  }

  typeTitle(box, text) {
    if (this.typing) clearInterval(this.typing.timer);
    const caret = h("span", { class: "dr-caret", "aria-hidden": "true" });
    const textNode = document.createTextNode("");
    box.append(textNode, caret);
    box.setAttribute("aria-label", text);
    let i = 0;
    const step = Math.max(2, Math.ceil(text.length / 30));
    const timer = setInterval(() => {
      i = Math.min(text.length, i + step);
      textNode.data = text.slice(0, i);
      if (i >= text.length) {
        clearInterval(timer);
        this.typing = null;
        setTimeout(() => caret.remove(), 900);
      }
    }, 24);
    this.typing = { timer };
  }
}

export default {
  async mount(el, ctx) {
    const page = new DesignsPage(el, ctx);
    await page.start();
    return () => page.destroy();
  },
};
