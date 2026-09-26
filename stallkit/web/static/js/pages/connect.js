// Mağaza Bağlantısı (/kurulum/magaza): the Etsy app keys, connecting the shop, the
// connection's status. Also the landing page of the consent tab (/oauth-done): Etsy
// sends that tab to the callback listener, which sends it here.
//
// The left card follows the setup: keys missing or refused -> how to create the app and
// the key form; keys fine -> "Etsy mağazanızı bağlayın" (frame t160); connected -> the
// granted permissions. The right card shows the connection as a diagram and 3 steps.

import { icon, logoMark } from "../icons.js";
import {
  badge,
  button,
  copyField,
  copyText,
  cx,
  field,
  h,
  iconButton,
  infoNote,
  mount,
  progressBar,
  sectionTitle,
  skeleton,
  spinner,
  stepper,
  textInput,
} from "../ui.js";

const SELLER_APP_URL = "https://www.etsy.com/developers/register-seller-app";
const DASHBOARD_URL = "https://www.etsy.com/developers/your-apps";
const DEFAULT_CALLBACK = "http://localhost:3003/oauth/redirect";
const DEFAULT_SCOPES = ["shops_r", "listings_r", "listings_w", "transactions_r", "transactions_w"];

// What each scope lets stallkit do, in the words of the consent card. listings_r and
// listings_w are one line; a scope not listed here is shown by its raw name.
const PERMISSIONS = [
  { scopes: ["listings_r", "listings_w"], key: "perm.listings" },
  { scopes: ["transactions_r"], key: "perm.orders" },
  { scopes: ["transactions_w"], key: "perm.tracking", note: "perm.tracking_note" },
  { scopes: ["shops_r"], key: "perm.shop" },
];
const WORDED = new Set(PERMISSIONS.flatMap((p) => p.scopes));

// The connect job's phases, in order, and how far along the bar each one is.
const PHASES = ["starting", "opened", "code_received", "fetching_shop", "done"];
const PHASE_PCT = { starting: 5, opened: 16, code_received: 74, fetching_shop: 88, done: 100 };
const CREEP_TO = 62; // while waiting for the person, the bar creeps towards this
const TERMINAL = new Set(["done", "error", "cancelled"]);

export default {
  async mount(el, ctx) {
    if (ctx.path === "/oauth-done") return mountDone(el, ctx);
    return mountConnect(el, ctx);
  },
};

// ------------------------------------------------------------------ shared pieces

function permissionRows(t, scopes, extra) {
  const set = new Set(scopes);
  const rows = [];
  for (const p of PERMISSIONS) {
    if (p.scopes.some((s) => set.has(s))) rows.push({ text: t(p.key), note: p.note ? t(p.note) : null, isNew: p.scopes.some((s) => extra.includes(s)) });
  }
  for (const s of scopes) if (!WORDED.has(s)) rows.push({ raw: s, isNew: extra.includes(s) });
  return rows;
}

function permList(t, rows) {
  return h(
    "ul",
    { class: "cx-perms" },
    rows.map((r) =>
      h(
        "li",
        { class: cx("cx-perm", r.isNew && "is-new") },
        h("span", { class: "cx-perm-check", "aria-hidden": "true" }, icon("check", { size: 13, strokeWidth: 2.4 })),
        h(
          "span",
          { class: "cx-perm-text" },
          r.raw ? h("code", { class: "cx-scope mono" }, r.raw) : r.text,
          r.note ? h("span", { class: "cx-perm-note" }, r.note) : null,
        ),
        r.isNew ? badge({ text: t("connect.extra_badge"), tone: "accent", size: "sm" }) : null,
      ),
    ),
  );
}

function safeNote(t) {
  return infoNote({
    icon: "shield-check",
    tone: "info",
    text: [h("b", null, t("perm.safe_strong")), " ", t("perm.safe_rest")],
  });
}

function extLink(href, label, variant = "secondary") {
  return h(
    "a",
    { href, target: "_blank", rel: "noopener noreferrer", class: cx("btn", `btn-${variant}`, "btn-sm", "cx-ext") },
    h("span", { class: "btn-label" }, label),
    icon("external", { size: 13 }),
  );
}

function head(iconName, title, tone = "accent") {
  return h(
    "div",
    { class: "cx-head" },
    h("span", { class: cx("cx-tile", `tone-${tone}`), "aria-hidden": "true" }, icon(iconName, { size: 20 })),
    h("h2", { class: "cx-title" }, title),
  );
}

// ------------------------------------------------------------------ /kurulum/magaza

async function mountConnect(el, ctx) {
  const t = ctx.t;
  const s = {
    status: ctx.status(),
    info: null,
    infoError: null,
    mode: null,
    editKeys: false,
    keyResult: null, // the last POST /api/connect/keys answer, shown once
    conn: null, // a connect in progress: {jobId, phase, pct, url, popup, blocked}
    connError: null, // why the last connect failed (ApiError or job.error)
    extra: /^[a-z_]{3,32}$/.test(ctx.query.scope || "") ? ctx.query.scope : null,
  };
  let creepTimer = null;
  let pollTimer = null;
  let form = null; // the key form's nodes, kept so typing survives re-renders
  const live = {}; // nodes the connect progress updates in place

  // ---- layout
  const steps = stepper({ steps: [] });
  steps.classList.add("cx-stepper");
  steps.addEventListener("click", (e) => {
    const li = e.target.closest(".step[data-href]");
    if (li) ctx.navigate(li.dataset.href);
  });
  steps.addEventListener("keydown", (e) => {
    const li = e.target.closest(".step[data-href]");
    if (li && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      ctx.navigate(li.dataset.href);
    }
  });
  const main = h("section", { class: "card cx-main" });
  const side = buildSide();
  const grid = h("div", { class: "cx-grid" }, main, side.el);
  el.append(steps, grid);

  // ---- state helpers
  const state = () => (s.status && s.status.state) || "checking";
  const setup = () => (s.status && s.status.setup) || {};
  const connecting = () => !!(s.conn && s.conn.active);

  function computeMode() {
    if (connecting()) return "connect";
    const st = state();
    if (!s.info) return s.infoError ? "error" : "loading";
    if (s.editKeys) return "keys";
    if (st === "keys" || st === "bad_keys" || !s.info.keys) return "keys";
    if (st === "connected") return "connected";
    if (st === "disconnected" || st === "reconnect") return "connect";
    // checking / offline / error: go by what is saved on this computer
    return s.info.connected ? "connected" : "connect";
  }

  function shopName() {
    return (s.status && s.status.shop && s.status.shop.name) || (s.info && s.info.shop_name) || null;
  }

  // ---- stepper
  function stepperSteps() {
    const st = state();
    const su = setup();
    const known = st !== "checking" || !!s.info;
    const keysOk = known && (s.info ? s.info.keys : su.keys) && st !== "keys" && st !== "bad_keys";
    const shopOk = st === "connected" || (["checking", "offline", "error"].includes(st) && !!(s.info && s.info.connected));
    const mockups = su.mockups || 0;
    const list = [
      { label: t("steps.account"), state: st === "bad_keys" ? "error" : keysOk ? "done" : "todo", doneSub: t("steps.done"), errSub: t("steps.bad_keys") },
      { label: t("steps.shop"), state: st === "reconnect" ? "error" : shopOk ? "done" : "todo", doneSub: t("steps.done"), errSub: t("steps.reconnect") },
      { label: t("steps.mockups"), state: mockups > 0 ? "done" : "todo", doneSub: t("steps.mockups_n", { n: mockups }) },
      { label: t("steps.upload"), state: su.template ? "done" : "todo", doneSub: t("steps.ready") },
    ];
    const current = list.findIndex((x) => x.state !== "done");
    if (current >= 0 && list[current].state === "todo") list[current].state = "current";
    return list.map((x) => ({
      label: x.label,
      state: x.state,
      sub: x.state === "done" ? x.doneSub : x.state === "error" ? x.errSub : x.state === "current" ? t("steps.current") : t("steps.todo"),
    }));
  }

  function renderSteps() {
    steps.update(stepperSteps());
    const links = [null, null, "/kurulum/mockuplar", setup().template ? "/tasarim-yukle" : "/kurulum/sablon"];
    steps.querySelectorAll(".step").forEach((li, i) => {
      if (!links[i]) return;
      li.dataset.href = links[i];
      li.classList.add("is-link");
      li.tabIndex = 0;
      li.setAttribute("role", "link");
    });
  }

  // ---- the right card
  function buildSide() {
    const badgeSlot = h("span", { class: "cx-side-badge" });
    const lock = h("span", { class: "cx-link-lock", title: t("diagram.lock") });
    const nameEl = h("b", { class: "cx-node-name" });
    const diagram = h(
      "div",
      { class: "cx-diagram" },
      h(
        "div",
        { class: "cx-node" },
        h("span", { class: "cx-node-tile is-app" }, logoMark({ size: 50 })),
        h("b", { class: "cx-node-name" }, t("app.name")),
        h("span", { class: "cx-node-sub" }, t("diagram.app_sub")),
      ),
      h(
        "div",
        { class: "cx-link", "aria-hidden": "true" },
        h("span", { class: "cx-link-dot" }),
        h("span", { class: "cx-link-line" }),
        lock,
        h("span", { class: "cx-link-line" }),
      ),
      h(
        "div",
        { class: "cx-node" },
        h("span", { class: "cx-node-tile is-shop" }, icon("store", { size: 26 })),
        nameEl,
        h("span", { class: "cx-node-sub" }, t("diagram.shop_sub")),
      ),
    );
    const flow = h("ol", { class: "cx-flow" });
    const card = h(
      "section",
      { class: "card cx-side" },
      h("header", { class: "cx-side-head" }, h("h2", { class: "cx-side-title" }, t("status.title")), badgeSlot),
      diagram,
      flow,
      h("footer", { class: "cx-side-foot" }, icon("info", { size: 14 }), h("span", null, t("status.footer"))),
    );
    return { el: card, badgeSlot, lock, nameEl, diagram, flow };
  }

  function flowStates() {
    if (connecting()) {
      const p = s.conn.phase;
      if (p === "starting") return ["running", "todo", "todo"];
      if (p === "opened") return ["done", "running", "todo"];
      if (p === "done") return ["done", "done", "done"];
      return ["done", "done", "running"];
    }
    if (state() === "connected" || computeMode() === "connected") return ["done", "done", "done"];
    return ["todo", "todo", "todo"];
  }

  function renderSide() {
    const st = state();
    let b;
    if (connecting()) b = badge({ text: t("status.connecting", { pct: Math.round(s.conn.pct) }), tone: "accent" });
    else if (st === "connected") b = badge({ text: t("status.connected"), tone: "success", dot: true });
    else if (st === "reconnect") b = badge({ text: t("status.reconnect"), tone: "warning", dot: true });
    else if (st === "bad_keys") b = badge({ text: t("status.bad_keys"), tone: "danger", dot: true });
    else if (st === "offline" || st === "error") b = badge({ text: t("status.offline"), tone: "warning", dot: true });
    else if (st === "checking") b = badge({ text: t("status.checking"), tone: "neutral" });
    else b = badge({ text: t("status.not_connected"), tone: "neutral", dot: true });
    mount(side.badgeSlot, b);

    const linked = st === "connected" && !connecting();
    side.diagram.className = cx("cx-diagram", connecting() && "is-connecting", linked && "is-connected");
    mount(side.lock, icon(linked ? "check" : "lock", { size: 13, strokeWidth: linked ? 2.4 : 1.8 }));
    side.nameEl.textContent = shopName() || t("diagram.shop");
    side.nameEl.title = side.nameEl.textContent;

    const states = flowStates();
    mount(
      side.flow,
      [1, 2, 3].map((n, i) => {
        const st2 = states[i];
        const marker =
          st2 === "running"
            ? h("span", { class: "cx-flow-ring", role: "img", "aria-label": t("common.loading") })
            : st2 === "done"
              ? icon("check", { size: 12, strokeWidth: 2.6 })
              : String(n);
        return h(
          "li",
          { class: cx("cx-flow-step", `is-${st2}`) },
          h("span", { class: "cx-flow-mark num" }, marker),
          h("span", { class: "cx-flow-text" }, h("b", null, t(`flow.${n}.title`)), h("span", null, t(`flow.${n}.sub`))),
        );
      }),
    );
  }

  // ---- the left card
  function statusNotes() {
    const st = state();
    const retry = () =>
      button({
        label: t("retry"),
        icon: "refresh",
        size: "sm",
        autoLoading: true,
        onClick: () => ctx.refreshStatus(true).catch((err) => ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t) })),
      });
    if (st === "offline") return [infoNote({ icon: "alert", tone: "warning", text: t("offline.note"), action: retry() })];
    if (st === "error") return [infoNote({ icon: "alert", tone: "warning", text: t("error.note", { detail: s.status.detail || "" }), action: retry() })];
    return [];
  }

  function keyResultNotes() {
    const r = s.keyResult;
    if (!r) return [];
    const out = [];
    if (r.check === "ok") out.push(infoNote({ icon: "check-circle", tone: "success", text: t("keys.result.ok") }));
    if (r.token_cleared) out.push(infoNote({ icon: "alert", tone: "warning", text: t("keys.token_cleared") }));
    return out;
  }

  function connErrorNote() {
    const err = s.connError;
    if (!err) return null;
    let action = null;
    if (err.code === "callback_not_local" || err.code === "bad_redirect") {
      action = button({
        label: t("connect.fix_callback"),
        size: "sm",
        onClick: () => {
          s.editKeys = true;
          s.connError = null;
          const suggested = (err.params && err.params.suggested) || DEFAULT_CALLBACK;
          render(true);
          if (form) {
            form.cb.value = suggested;
            form.adv.open = true;
            form.cb.focus();
          }
        },
      });
    }
    return infoNote({ icon: "alert-circle", tone: "danger", text: ctx.api.errorText(err, t), action });
  }

  function loadingView() {
    return [
      h(
        "div",
        { class: "cx-head" },
        h("span", { class: "cx-tile", "aria-hidden": "true" }, icon("link", { size: 20 })),
        h("div", { class: "cx-head-skel" }, skeleton({ lines: 1, height: 22, widths: ["60%"] })),
      ),
      h("div", { class: "cx-loading" }, skeleton({ lines: 5, height: 14 })),
    ];
  }

  function errorView() {
    return [
      head("link", t("title")),
      infoNote({
        icon: "alert-circle",
        tone: "danger",
        text: [t("info.error"), " ", ctx.api.errorText(s.infoError, t)],
        action: button({ label: t("retry"), icon: "refresh", size: "sm", autoLoading: true, onClick: () => loadInfo() }),
      }),
    ];
  }

  // keys: how to make the app + the form
  function howStep(n, title, body) {
    return h(
      "li",
      { class: "cx-how" },
      h("span", { class: "cx-how-num num", "aria-hidden": "true" }, String(n)),
      h("div", { class: "cx-how-body" }, h("h3", { class: "cx-how-title" }, title), body),
    );
  }

  function whyBlock() {
    const text = t("keys.why");
    const copy = button({
      label: t("common.copy"),
      icon: "copy",
      size: "sm",
      onClick: async () => {
        const ok = await copyText(text);
        copy.setLabel(ok ? t("common.copied") : t("common.copy_failed"));
        copy.classList.toggle("is-done", ok);
        setTimeout(() => {
          copy.setLabel(t("common.copy"));
          copy.classList.remove("is-done");
        }, 1600);
      },
    });
    return h("div", { class: "cx-why" }, h("p", { class: "cx-why-text", lang: "en", "aria-label": t("keys.why_label") }, text), copy);
  }

  function howto() {
    const callback = (s.info && s.info.redirect_uri) || DEFAULT_CALLBACK;
    return h(
      "ol",
      { class: "cx-howto" },
      howStep(1, t("keys.step1.title"), [h("p", null, t("keys.step1.text")), extLink(SELLER_APP_URL, t("keys.step1.button"))]),
      howStep(2, t("keys.step2.title"), [h("p", null, t("keys.step2.text")), whyBlock()]),
      howStep(3, t("keys.step3.title"), [
        h("p", null, t("keys.step3.text")),
        copyField({ value: callback, ariaLabel: t("keys.callback") }),
        h("p", null, t("keys.step3.after")),
        extLink(DASHBOARD_URL, t("keys.step3.button")),
      ]),
    );
  }

  function buildForm() {
    const key = textInput({ mono: true, placeholder: t("keys.keystring_ph") });
    const secret = textInput({ type: "password", mono: true, placeholder: t("keys.secret_ph"), autocomplete: "off" });
    const eye = iconButton({
      icon: "eye",
      variant: "ghost",
      size: "sm",
      title: t("keys.show"),
      onClick: () => {
        const show = secret.type === "password";
        secret.type = show ? "text" : "password";
        mount(eye, icon(show ? "eye-off" : "eye", { size: 14 }));
        eye.title = t(show ? "keys.hide" : "keys.show");
        eye.setAttribute("aria-label", eye.title);
      },
    });
    const secretBox = h("div", { class: "input-affix cx-secret" }, secret, eye);
    const cb = textInput({ mono: true, value: (s.info && s.info.redirect_uri) || DEFAULT_CALLBACK });
    const keyField = field({ label: t("keys.keystring"), hint: t("keys.combined_hint"), input: key });
    const secretField = field({ label: t("keys.secret"), input: secretBox });
    const cbField = field({ label: t("keys.callback"), hint: t("keys.callback_hint", { url: DEFAULT_CALLBACK }), input: cb });
    const adv = h(
      "details",
      { class: "cx-adv" },
      h("summary", null, icon("chevron-right", { size: 14 }), h("span", null, t("keys.advanced"))),
      h("div", { class: "cx-adv-body" }, cbField),
    );
    const result = h("div", { class: "cx-result", role: "status" });
    const save = button({ label: t("keys.save"), variant: "primary", size: "lg", icon: "check", type: "submit" });
    return { key, secret, cb, keyField, secretField, cbField, adv, result, save };
  }

  function renderKeyResult() {
    if (!form) return;
    const r = s.keyResult;
    if (!r || r.check === "ok") {
      mount(form.result);
      return;
    }
    const map = {
      rejected: ["danger", "x-circle", t("keys.result.rejected", { reason: r.reason || "" })],
      offline: ["warning", "alert", t("keys.result.offline")],
      unknown: ["warning", "alert", t("keys.result.unknown", { reason: r.reason || "" })],
    };
    const [tone, ic, text] = map[r.check] || map.unknown;
    mount(form.result, infoNote({ tone, icon: ic, text }));
  }

  function keysView() {
    const info = s.info || {};
    if (!form) form = buildForm();
    form.key.placeholder = info.keys ? t("keys.saved_ph", { prefix: info.keystring_prefix }) : t("keys.keystring_ph");
    form.secret.placeholder = info.keys ? t("keys.secret_saved_ph", { n: info.secret_length }) : t("keys.secret_ph");
    renderKeyResult();
    const kids = [head("key", t("keys.title")), h("p", { class: "cx-lead" }, t("keys.lead"))];
    if (state() === "bad_keys" && !(s.keyResult && s.keyResult.check === "rejected")) {
      kids.push(
        infoNote({
          icon: "alert-circle",
          tone: "danger",
          text: [h("b", null, t("keys.bad")), " ", s.status.detail ? h("span", { class: "mono cx-detail" }, s.status.detail) : null, " ", t("keys.bad_help")],
        }),
      );
    }
    if (s.editKeys && info.keys) {
      kids.push(
        infoNote({
          icon: "key",
          tone: "neutral",
          text: t("keys.editing", { prefix: info.keystring_prefix, n: info.secret_length }),
          action: button({
            label: t("keys.cancel_edit"),
            size: "sm",
            variant: "ghost",
            onClick: () => {
              s.editKeys = false;
              render(true);
            },
          }),
        }),
      );
    }
    kids.push(...statusNotes());
    if (info.keys) {
      kids.push(h("details", { class: "cx-howto-wrap" }, h("summary", null, icon("chevron-right", { size: 14 }), h("span", null, t("keys.howto_again"))), howto()));
    } else {
      kids.push(sectionTitle(t("keys.howto")), howto());
    }
    kids.push(
      sectionTitle(t("keys.form")),
      h(
        "form",
        {
          class: "cx-form",
          autocomplete: "off",
          onSubmit: (e) => {
            e.preventDefault();
            saveKeys();
          },
        },
        h("div", { class: "cx-form-grid" }, form.keyField, form.secretField),
        form.adv,
        form.result,
        h("div", { class: "cx-actions" }, form.save, h("span", { class: "cx-local" }, icon("lock", { size: 13 }), t("keys.local"))),
      ),
      infoNote({ icon: "info", tone: "neutral", text: t("keys.one_app") }),
    );
    return kids;
  }

  async function saveKeys() {
    if (!form) return;
    for (const f of [form.keyField, form.secretField, form.cbField]) f.setError("");
    const body = { keystring: form.key.value.trim(), shared_secret: form.secret.value.trim(), redirect_uri: form.cb.value.trim() };
    form.save.setLoading(true);
    try {
      const res = await ctx.api.post("/api/connect/keys", body, { signal: ctx.signal });
      form.key.value = "";
      form.secret.value = ""; // the secret never stays in the page
      s.keyResult = res;
      if (res.status) s.status = res.status;
      if (res.check === "ok") {
        s.editKeys = false;
        ctx.toast({ tone: "success", title: t("keys.saved_toast"), message: t("keys.result.ok") });
      }
      await loadInfo();
    } catch (err) {
      if (ctx.api.isAbort(err)) return;
      const which = err.params && err.params.field;
      const target = which === "keystring" ? form.keyField : which === "shared_secret" ? form.secretField : which === "redirect_uri" ? form.cbField : null;
      if (target) {
        if (target === form.cbField) form.adv.open = true;
        target.setError(ctx.api.errorText(err, t));
      } else {
        mount(form.result, infoNote({ tone: "danger", icon: "x-circle", text: ctx.api.errorText(err, t) }));
      }
    } finally {
      form.save.setLoading(false);
    }
  }

  // connect: the t160 card
  function connectView() {
    const info = s.info || {};
    const kids = [head("link", t("connect.title")), h("p", { class: "cx-lead" }, t("connect.lead"))];
    if (state() === "reconnect" && !connecting()) kids.push(infoNote({ icon: "alert", tone: "warning", text: t("connect.reconnect_note") }));
    kids.push(...statusNotes(), ...keyResultNotes());
    const errNote = connErrorNote();
    if (errNote) kids.push(errNote);
    const scopes = [...(info.scopes_requested || DEFAULT_SCOPES)];
    const extra = [];
    if (s.extra && !scopes.includes(s.extra)) {
      scopes.push(s.extra);
      extra.push(s.extra);
    }
    kids.push(sectionTitle(t("connect.perms")), permList(t, permissionRows(t, scopes, extra)), safeNote(t), connectRow());
    return kids;
  }

  function phaseText() {
    const p = s.conn ? s.conn.phase : "starting";
    if (p === "starting") return t("connect.phase.starting");
    if (p === "opened") return s.conn && s.conn.blocked && !s.conn.restored ? t("connect.popup_blocked") : t("connect.phase.opened");
    if (p === "done") return t("connect.phase.done");
    return t("connect.phase.fetching");
  }

  function connectRow() {
    const running = connecting();
    const btn = button({
      label: running ? t("connect.connecting") : s.connError ? t("connect.retry") : t("connect.button"),
      loading: running,
      variant: "primary",
      size: "lg",
      icon: "link",
      class: "cx-connect-btn",
      onClick: () => startConnect(),
    });
    let aside;
    if (running) {
      const prog = progressBar({ value: s.conn.pct, tone: "accent", size: "sm", label: phaseText() });
      const text = h("span", { class: "cx-phase", role: "status" }, phaseText());
      const links = h("span", { class: "cx-progress-links" });
      live.prog = prog;
      live.text = text;
      live.links = links;
      aside = h("div", { class: "cx-progress" }, h("div", { class: "cx-progress-top" }, text, links), prog.el);
      renderLinks();
    } else {
      live.prog = null;
      aside = h("p", { class: "cx-hint" }, t("connect.hint"));
    }
    const edit = running
      ? null
      : button({
          label: t("keys.edit"),
          variant: "ghost",
          size: "sm",
          icon: "key",
          onClick: () => {
            s.editKeys = true;
            s.keyResult = null;
            render(true);
          },
        });
    return h("div", { class: "cx-actions cx-connect-row" }, btn, aside, h("span", { class: "spacer" }), edit);
  }

  function renderLinks() {
    if (!live.links || !s.conn) return;
    const kids = [];
    if (s.conn.url && (s.conn.blocked || s.conn.restored)) {
      kids.push(h("a", { href: s.conn.url, target: "_blank", rel: "noopener noreferrer", class: "cx-link-btn" }, t("connect.reopen"), icon("external", { size: 12 })));
    }
    if (s.conn.jobId) {
      kids.push(h("button", { type: "button", class: "cx-link-btn", onClick: cancelConnect }, t("connect.cancel")));
    }
    mount(live.links, kids);
  }

  // connected
  function connectedView() {
    const info = s.info || {};
    const name = shopName();
    const title = head("check", name || t("connected.title_plain"), "success");
    if (state() === "connected") title.append(badge({ text: t("status.connected_long"), tone: "success", dot: true }));
    const kids = [title, h("p", { class: "cx-lead" }, t("connected.lead"))];
    kids.push(...statusNotes(), ...keyResultNotes());
    const errNote = connErrorNote();
    if (errNote) kids.push(errNote);
    const granted = info.scopes_granted && info.scopes_granted.length ? info.scopes_granted : (s.status && s.status.scopes) || [];
    const missing = info.missing_scopes || [];
    if (missing.length) {
      kids.push(
        infoNote({
          icon: "alert",
          tone: "warning",
          text: t("connected.missing", { scopes: missing.join(", ") }),
          action: button({ label: t("connected.reconnect"), size: "sm", icon: "refresh", onClick: () => startConnect() }),
        }),
      );
    }
    if (s.extra && !granted.includes(s.extra)) {
      kids.push(
        infoNote({
          icon: "key",
          tone: "accent",
          text: t("connected.extra", { scope: s.extra }),
          action: button({ label: t("connected.extra_button"), size: "sm", variant: "primary", onClick: () => startConnect() }),
        }),
      );
    }
    kids.push(sectionTitle(t("connected.perms")), permList(t, permissionRows(t, granted, [])), safeNote(t));
    kids.push(
      h(
        "div",
        { class: "cx-actions cx-connect-row" },
        button({ label: t("connected.next"), variant: "primary", size: "lg", iconRight: "arrow-right", onClick: () => ctx.navigate("/kurulum/mockuplar") }),
        button({ label: t("connected.disconnect"), variant: "ghost", icon: "logout", class: "cx-danger-ghost", onClick: () => disconnect() }),
        h("span", { class: "spacer" }),
        button({
          label: t("keys.edit"),
          variant: "ghost",
          size: "sm",
          icon: "key",
          onClick: () => {
            s.editKeys = true;
            s.keyResult = null;
            render(true);
          },
        }),
      ),
    );
    if (info.keys) kids.push(h("p", { class: "cx-meta" }, t("connected.meta", { prefix: info.keystring_prefix, url: info.redirect_uri })));
    return kids;
  }

  async function disconnect() {
    const ok = await ctx.confirm({
      title: t("disconnect.title"),
      message: t("disconnect.message"),
      confirmLabel: t("disconnect.confirm"),
      danger: true,
      icon: "logout",
    });
    if (!ok) return;
    try {
      const res = await ctx.api.post("/api/connect/disconnect", {});
      if (res.status) s.status = res.status;
      s.keyResult = null;
      s.connError = null;
      ctx.toast({ tone: "info", title: t("disconnect.done") });
      await loadInfo();
    } catch (err) {
      ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t) });
    }
  }

  // ---- connecting
  function openBlank() {
    let w = null;
    try {
      w = window.open("", "_blank");
    } catch {
      w = null;
    }
    if (!w) return null;
    try {
      w.opener = null; // Etsy's page gets no handle on this app
    } catch {
      /* ignore */
    }
    try {
      const d = w.document;
      d.title = "Etsy";
      d.body.style.cssText =
        "margin:0;height:100vh;display:grid;place-items:center;background:#0b0c0f;color:#b6b9c4;font:15px system-ui,sans-serif";
      d.body.textContent = t("popup.opening");
    } catch {
      /* not ours to write into: fine */
    }
    return w;
  }

  // Only a tab still on this origin (the blank tab, or /oauth-done) may be closed from
  // here: once it shows Etsy's page it is no longer ours to close (its opener was cut).
  function closePopup(w) {
    if (!w || w.closed) return;
    let ours = false;
    try {
      ours = w.location.origin === location.origin;
    } catch {
      ours = false;
    }
    if (ours) w.close();
  }

  function startConnect() {
    if (connecting()) return;
    // Opened here, inside the click, or the browser blocks it; pointed at Etsy below.
    const popup = openBlank();
    const extra = s.extra && !((s.info && s.info.scopes_requested) || []).includes(s.extra) ? [s.extra] : [];
    s.connError = null;
    s.keyResult = null;
    s.conn = { active: true, phase: "starting", pct: PHASE_PCT.starting, jobId: null, url: null, popup, blocked: !popup, restored: false };
    render(true);
    startTimers();
    ctx.api
      .post("/api/connect/start", { extra_scopes: extra })
      .then((res) => {
        const conn = s.conn;
        if (!conn || conn.popup !== popup) return;
        conn.jobId = res.job_id;
        conn.url = res.url;
        if (popup && !popup.closed) {
          try {
            popup.location.replace(res.url);
          } catch {
            conn.blocked = true;
          }
        } else {
          conn.blocked = true;
        }
        advance("opened");
        renderLinks();
      })
      .catch((err) => {
        closePopup(popup);
        if (ctx.api.isAbort(err)) return;
        stopTimers();
        s.conn = null;
        s.connError = err;
        render(true);
      });
  }

  async function cancelConnect() {
    const conn = s.conn;
    if (!conn || !conn.jobId) return;
    closePopup(conn.popup);
    try {
      await ctx.api.post(`/api/jobs/${encodeURIComponent(conn.jobId)}/cancel`, {});
    } catch (err) {
      ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t) });
    }
  }

  function advance(phase) {
    if (!s.conn || PHASES.indexOf(phase) <= PHASES.indexOf(s.conn.phase)) return;
    s.conn.phase = phase;
    s.conn.pct = Math.max(s.conn.pct, PHASE_PCT[phase] || 0);
    updateLive();
  }

  function updateLive() {
    if (live.prog && s.conn) {
      live.prog.update(s.conn.pct);
      live.text.textContent = phaseText();
    }
    renderSide();
  }

  function finish(job) {
    const conn = s.conn;
    if (!conn) return;
    stopTimers();
    s.conn = null;
    // The consent tab closes itself on /oauth-done; this is the fallback if it is still
    // there (on another site it is left alone, see closePopup).
    if (conn.popup) setTimeout(() => closePopup(conn.popup), 1600);
    if (job.status === "done") {
      s.connError = null;
      s.editKeys = false;
      if (s.extra) s.extra = null;
      const shop = job.result && job.result.shop_name;
      ctx.toast({ tone: "success", title: t("connect.done_toast"), message: shop || undefined });
      ctx.refreshStatus(false).catch(() => {});
    } else if (job.status === "error") {
      s.connError = job.error || { code: "internal" };
    }
    loadInfo();
  }

  function startTimers() {
    stopTimers();
    creepTimer = setInterval(() => {
      if (!s.conn || s.conn.phase !== "opened" || s.conn.pct >= CREEP_TO) return;
      s.conn.pct = Math.min(CREEP_TO, s.conn.pct + Math.max(0.25, (CREEP_TO - s.conn.pct) * 0.025));
      updateLive();
    }, 900);
    // Events can be missed (a reconnecting stream); ask now and then as well.
    pollTimer = setInterval(async () => {
      const conn = s.conn;
      if (!conn || !conn.jobId) return;
      try {
        const job = await ctx.api.get(`/api/jobs/${encodeURIComponent(conn.jobId)}`, null, { signal: ctx.signal });
        if (s.conn !== conn) return;
        if (job.state && job.state.phase) advance(job.state.phase);
        if (TERMINAL.has(job.status)) finish(job);
      } catch {
        /* next time */
      }
    }, 2500);
  }

  function stopTimers() {
    clearInterval(creepTimer);
    clearInterval(pollTimer);
    creepTimer = null;
    pollTimer = null;
  }

  ctx.events.on("job-event", (ev) => {
    if (!ev || ev.kind !== "connect" || ev.type !== "phase" || !s.conn) return;
    if (s.conn.jobId && ev.job_id !== s.conn.jobId) return;
    advance(ev.data && ev.data.phase);
  });
  ctx.events.on("job", (job) => {
    if (!job || job.kind !== "connect" || !s.conn || job.id !== s.conn.jobId) return;
    if (TERMINAL.has(job.status)) finish(job);
  });

  // ---- data
  async function loadInfo() {
    try {
      s.info = await ctx.api.get("/api/connect/info", null, { signal: ctx.signal });
      s.infoError = null;
    } catch (err) {
      if (ctx.api.isAbort(err)) return;
      s.infoError = err;
    }
    if (s.extra && s.info && !(s.info.known_scopes || []).includes(s.extra)) s.extra = null;
    const job = s.info && s.info.job;
    if (job && !s.conn && !TERMINAL.has(job.status)) {
      // A connect started earlier (another visit, another tab) is still waiting.
      const phase = (job.state && job.state.phase) || "opened";
      s.conn = { active: true, phase, pct: Math.max(PHASE_PCT[phase] || 16, 20), jobId: job.id, url: job.state && job.state.url, popup: null, blocked: false, restored: true };
      startTimers();
    }
    render(true);
  }

  function render(force) {
    renderSteps();
    const mode = computeMode();
    if (force || mode !== s.mode) {
      const previous = s.mode;
      s.mode = mode;
      const views = { loading: loadingView, error: errorView, keys: keysView, connect: connectView, connected: connectedView };
      const modeChanged = previous !== null && previous !== "loading" && mode !== previous;
      mount(main, (views[mode] || loadingView)());
      if (modeChanged) {
        const scroller = el.closest(".content");
        if (scroller) scroller.scrollTop = 0;
      }
      main.className = cx("card", "cx-main", `is-${mode}`);
      grid.className = cx("cx-grid", `mode-${mode}`);
    }
    renderSide();
  }

  ctx.onStatus((st, prev) => {
    s.status = st;
    if (!prev || prev.state !== st.state || (prev.setup && st.setup && prev.setup.connected !== st.setup.connected)) {
      loadInfo();
    } else {
      render(false);
    }
  });

  render(true);
  await loadInfo();
  return () => stopTimers();
}

// ------------------------------------------------------------------ /oauth-done

async function mountDone(el, ctx) {
  const t = ctx.t;
  const box = h("div", { class: "cx-done-box", role: "status" });
  el.append(h("div", { class: "cx-done" }, box));
  let pollTimer = null;
  let closeTimer = null;
  let giveUpTimer = null;
  let finished = false;
  let jobId = null;

  const back = () => button({ label: t("done.back"), variant: "secondary", onClick: () => ctx.navigate("/kurulum/magaza") });

  function show(tone, title, message, action) {
    const glyph =
      tone === "working"
        ? spinner({ size: 26, tone: "accent" })
        : icon(tone === "success" ? "check" : tone === "danger" ? "x" : "info", { size: 26, strokeWidth: 2.4 });
    mount(
      box,
      h("span", { class: cx("cx-done-tile", `tone-${tone === "working" ? "accent" : tone}`) }, glyph),
      h("h2", { class: "cx-done-title" }, title),
      message ? h("p", { class: "cx-done-msg" }, message) : null,
      action ? h("div", { class: "cx-done-actions" }, action) : null,
    );
  }

  function settle(job) {
    if (finished) return;
    finished = true;
    clearInterval(pollTimer);
    clearTimeout(giveUpTimer);
    const st = ctx.status();
    if ((job && job.status === "done") || (!job && st && st.state === "connected")) {
      const shop = (job && job.result && job.result.shop_name) || (st && st.shop && st.shop.name);
      show("success", t("done.ok"), shop ? t("done.ok_shop", { shop }) : null, back());
      // Only a tab a script opened may close itself; otherwise the message stays.
      closeTimer = setTimeout(() => {
        try {
          window.close();
        } catch {
          /* ignore */
        }
      }, 1000);
    } else if (job && job.status === "error") {
      show("danger", t("done.failed"), ctx.api.errorText(job.error || {}, t), back());
    } else {
      show("info", t("done.none"), null, back());
    }
  }

  async function check() {
    try {
      const jobs = await ctx.api.get("/api/jobs", { kind: "connect" }, { signal: ctx.signal });
      const job = Array.isArray(jobs) ? jobs[0] : null;
      if (!job) {
        settle(null);
        return;
      }
      jobId = job.id;
      if (TERMINAL.has(job.status)) settle(job);
    } catch (err) {
      if (ctx.api.isAbort(err)) return;
      /* try again on the next tick */
    }
  }

  show("working", t("done.working"), t("done.working_sub"));
  ctx.events.on("job", (job) => {
    if (job && job.kind === "connect" && (!jobId || job.id === jobId) && TERMINAL.has(job.status)) settle(job);
  });
  await check();
  if (!finished) {
    pollTimer = setInterval(check, 1500);
    giveUpTimer = setTimeout(() => settle(null), 90000);
  }
  return () => {
    clearInterval(pollTimer);
    clearTimeout(closeTimer);
    clearTimeout(giveUpTimer);
  };
}
