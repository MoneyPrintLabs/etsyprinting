// App shell: boot, router, layout (sidebar, top bar, bell, shop card).
//
// Page modules live in /js/pages/<page>.js and export
//   export default { async mount(el, ctx) { ...; return () => { /* cleanup */ }; } };
// Before a page mounts, /i18n/<page>.json and /css/pages/<page>.css are loaded once.

import { api, errorText, isAbort, onNoSession } from "./api.js";
import { events } from "./events.js";
import * as i18n from "./i18n.js";
import { relative } from "./format.js";
import { icon, logoMark } from "./icons.js";
import {
  badge,
  button,
  closeModals,
  closePopovers,
  confirm,
  cx,
  emptyState,
  h,
  iconButton,
  infoNote,
  menu,
  modal,
  mount,
  popover,
  spinner,
  toast,
} from "./ui.js";

// ------------------------------------------------------------------ routes & navigation

/** Route table. `nav` = which sidebar item is active (defaults to `page`). */
export const ROUTES = [
  { path: "/panel", page: "panel" },
  { path: "/tasarim-yukle", page: "designs" },
  { path: "/ilanlar", page: "listings" },
  { path: "/ilanlar/:id", page: "listing-detail", nav: "listings" },
  { path: "/seo", page: "seo" },
  { path: "/siparisler", page: "orders" },
  { path: "/kar-zarar", page: "profit" },
  { path: "/pinterest", page: "pinterest" },
  { path: "/kurulum/mockuplar", page: "mockups" },
  { path: "/kurulum/mockuplar/:name", page: "mockups" },
  { path: "/kurulum/sablon", page: "template" },
  { path: "/kurulum/magaza", page: "connect" },
  { path: "/oauth-done", page: "connect", nav: "connect" },
  { path: "/ayarlar", page: "settings" },
];

const NAV = [
  {
    group: "workflow",
    items: [
      { page: "panel", path: "/panel", icon: "home" },
      { page: "designs", path: "/tasarim-yukle", icon: "upload" },
      { page: "listings", path: "/ilanlar", icon: "list" },
      { page: "seo", path: "/seo", icon: "search" },
      { page: "orders", path: "/siparisler", icon: "truck" },
      { page: "profit", path: "/kar-zarar", icon: "chart" },
      { page: "pinterest", path: "/pinterest", icon: "pin" },
    ],
  },
  {
    group: "setup",
    items: [
      { page: "mockups", path: "/kurulum/mockuplar", icon: "image" },
      { page: "template", path: "/kurulum/sablon", icon: "file" },
      { page: "connect", path: "/kurulum/magaza", icon: "link" },
    ],
  },
];

const PROBLEM_STATES = new Set(["bad_keys", "reconnect", "offline", "error"]);
const STATE_TONE = {
  connected: "success",
  checking: "muted",
  keys: "muted",
  disconnected: "muted",
  bad_keys: "danger",
  reconnect: "warning",
  offline: "warning",
  error: "warning",
};
const NOTIF_ICON = { success: "check-circle", warning: "alert", danger: "alert-circle", info: "info" };

const state = {
  session: null,
  status: null,
  notifications: [],
  unread: 0,
  current: null,
  mountSeq: 0,
  mountedShopId: null,
  connected: true,
  lostTimer: null,
  showLost: false,
  stopped: false,
};
const statusListeners = new Set();
const els = { nav: new Map() };
let root = null;
let notifPop = null;
let shopChain = Promise.resolve();
const t = i18n.t;

function currentPath() {
  const p = location.pathname.replace(/\/+$/, "");
  return p || "/";
}

function matchRoute(path) {
  const segs = path.split("/").filter(Boolean);
  for (const r of ROUTES) {
    const rs = r.path.split("/").filter(Boolean);
    if (rs.length !== segs.length) continue;
    const params = {};
    let ok = true;
    for (let i = 0; i < rs.length; i += 1) {
      if (rs[i].startsWith(":")) {
        try {
          params[rs[i].slice(1)] = decodeURIComponent(segs[i]);
        } catch {
          params[rs[i].slice(1)] = segs[i];
        }
      } else if (rs[i] !== segs[i]) {
        ok = false;
        break;
      }
    }
    if (ok) return { route: r, params };
  }
  return null;
}

/** navigate(path, {replace}) - client-side navigation (same origin only). */
export function navigate(path, { replace = false } = {}) {
  if (state.stopped) return;
  const url = new URL(path, location.origin);
  if (url.origin !== location.origin) {
    window.open(url.href, "_blank", "noopener");
    return;
  }
  const target = url.pathname + url.search + url.hash;
  const now = location.pathname + location.search + location.hash;
  if (target === now && !replace) {
    if (els.content) els.content.scrollTop = 0;
    return;
  }
  history[replace ? "replaceState" : "pushState"]({}, "", target);
  route();
}

function interceptLinks(e) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest && e.target.closest("a[href]");
  if (!a) return;
  if ((a.target && a.target !== "_self") || a.hasAttribute("download") || a.dataset.external !== undefined) return;
  const href = a.getAttribute("href") || "";
  if (!href || href.startsWith("#")) return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return;
  // A file link (/js/x.js, /favicon.svg) is left to the browser; app routes whose last
  // segment is a file name (/kurulum/mockuplar/<name.png>) are still app routes.
  if (/\.[a-z0-9]+$/i.test(url.pathname) && !matchRoute(url.pathname.replace(/\/+$/, ""))) return;
  e.preventDefault();
  navigate(url.pathname + url.search + url.hash);
}

async function route() {
  if (state.stopped) return;
  const path = currentPath();
  if (path === "/" || path === "/index.html") {
    // Only a finished check that found no keys sends a newcomer to the setup page;
    // "checking" also reports setup.keys = false, and must not.
    const st = state.status;
    const noKeys = !!st && st.state === "keys" && st.setup && st.setup.keys === false;
    const target = noKeys ? "/kurulum/magaza" : "/panel";
    history.replaceState({}, "", target);
    return route();
  }
  const m = matchRoute(path);
  if (!m) {
    history.replaceState({}, "", "/panel");
    return route();
  }
  const query = Object.fromEntries(new URLSearchParams(location.search));
  return mountPage(m.route, m.params, query);
}

function remount() {
  return route();
}

// ------------------------------------------------------------------ page mounting

const cssLoads = new Map();
function loadPageCss(page) {
  if (cssLoads.has(page)) return cssLoads.get(page);
  const p = new Promise((resolve) => {
    const link = h("link", { rel: "stylesheet", href: `/css/pages/${page}.css`, dataset: { page } });
    const done = () => resolve();
    link.addEventListener("load", done, { once: true });
    link.addEventListener(
      "error",
      () => {
        console.warn(`[app] missing stylesheet for page "${page}"`);
        resolve();
      },
      { once: true },
    );
    setTimeout(done, 3000);
    document.head.appendChild(link);
  });
  cssLoads.set(page, p);
  return p;
}

function runCleanup(fn) {
  if (typeof fn !== "function") return;
  try {
    fn();
  } catch (err) {
    console.error("[app] page cleanup failed", err);
  }
}

function unmountCurrent() {
  const cur = state.current;
  if (!cur) return;
  state.current = null;
  try {
    cur.controller.abort();
  } catch {
    /* ignore */
  }
  for (const off of cur.subs) {
    try {
      off();
    } catch {
      /* ignore */
    }
  }
  runCleanup(cur.cleanup);
  closePopovers();
  closeModals();
}

function errorView(message, onRetry) {
  return emptyState({
    icon: "alert",
    title: t("page.error_title"),
    message,
    action: onRetry ? button({ label: t("common.retry"), icon: "refresh", onClick: onRetry }) : null,
  });
}

async function mountPage(routeDef, params, query) {
  const page = routeDef.page;
  const seq = ++state.mountSeq;
  unmountCurrent();
  setActiveNav(routeDef.nav || page);
  state.currentPage = page;
  renderBanner();
  setHeader({ actions: [] });

  const host = els.pageHost;
  const slow = setTimeout(() => {
    if (seq === state.mountSeq) mount(host, h("div", { class: "page-loading" }, spinner({ size: 22, tone: "accent" })));
  }, 160);

  let mod;
  try {
    const results = await Promise.all([i18n.loadNamespace(page), loadPageCss(page), import(`./pages/${page}.js`)]);
    mod = results[2];
  } catch (err) {
    clearTimeout(slow);
    if (seq !== state.mountSeq) return;
    console.error(`[app] could not load page "${page}"`, err);
    setHeader({ title: t(`nav.${routeDef.nav || page}`), subtitle: "" });
    mount(host, errorView(t("page.load_error"), () => remount()));
    return;
  }
  clearTimeout(slow);
  if (seq !== state.mountSeq) return;

  const pt = i18n.translator(page);
  const cur = {
    page,
    route: routeDef,
    params,
    query,
    t: pt,
    controller: new AbortController(),
    subs: [],
    cleanup: null,
  };
  state.current = cur;
  state.mountedShopId = state.session ? state.session.shop_id : null;
  setHeader({
    title: pt.has("title") ? pt("title") : t(`nav.${routeDef.nav || page}`),
    subtitle: pt.has("subtitle") ? pt("subtitle") : "",
    actions: [],
  });
  const el = h("div", { class: `page page-${page}` });
  mount(host, el);
  if (els.content) els.content.scrollTop = 0;

  const ctx = makeCtx(cur);
  try {
    const impl = mod && mod.default;
    if (!impl || typeof impl.mount !== "function") throw new Error(`page "${page}" has no mount()`);
    const cleanup = await impl.mount(el, ctx);
    if (state.current !== cur) {
      runCleanup(cleanup);
      return;
    }
    cur.cleanup = cleanup;
  } catch (err) {
    if (state.current !== cur || isAbort(err)) return;
    console.error(`[app] page "${page}" failed to mount`, err);
    mount(el, errorView(errorText(err, pt), () => remount()));
  }
}

function makeCtx(cur) {
  const scopedEvents = {
    on(topic, cb) {
      const off = events.on(topic, cb);
      cur.subs.push(off);
      return off;
    },
    once(topic, cb) {
      const off = events.once(topic, cb);
      cur.subs.push(off);
      return off;
    },
    get connected() {
      return events.connected;
    },
  };
  return {
    page: cur.page,
    path: location.pathname,
    params: cur.params,
    get query() {
      return cur.query;
    },
    t: cur.t,
    lang: i18n.getLang(),
    api,
    events: scopedEvents,
    signal: cur.controller.signal,
    setHeader(opts) {
      if (state.current === cur) setHeader(opts || {});
    },
    navigate,
    setQuery(q, { replace = true } = {}) {
      if (state.current !== cur) return;
      const sp = new URLSearchParams(location.search);
      for (const [k, v] of Object.entries(q || {})) {
        if (v === undefined || v === null || v === "") sp.delete(k);
        else sp.set(k, String(v));
      }
      const qs = sp.toString();
      history[replace ? "replaceState" : "pushState"]({}, "", location.pathname + (qs ? `?${qs}` : ""));
      cur.query = Object.fromEntries(sp);
    },
    toast,
    confirm,
    modal,
    status: () => state.status,
    onStatus(cb) {
      statusListeners.add(cb);
      const off = () => statusListeners.delete(cb);
      cur.subs.push(off);
      return off;
    },
    session: () => state.session,
    refreshStatus,
    setLanguage,
    remount: () => {
      if (state.current === cur) remount();
    },
    isActive: () => state.current === cur,
  };
}

// ------------------------------------------------------------------ header

function setHeader(opts) {
  if (!els.title) return;
  if ("title" in opts) {
    mount(els.title, opts.title ?? "");
    const plain = typeof opts.title === "string" ? opts.title : els.title.textContent;
    document.title = plain ? `${plain} · stallkit` : "stallkit";
  }
  if ("subtitle" in opts) {
    mount(els.subtitle, opts.subtitle ?? "");
    els.subtitle.hidden = !els.subtitle.textContent && !els.subtitle.firstElementChild;
  }
  if ("actions" in opts) {
    mount(els.pageActions, opts.actions || []);
  }
}

function setActiveNav(page) {
  for (const [p, a] of els.nav) {
    if (p === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
}

// ------------------------------------------------------------------ status, shop card, banner

function setStatus(s) {
  if (!s || typeof s !== "object" || state.stopped) return;
  const prev = state.status;
  state.status = s;
  renderShop();
  renderBanner();
  for (const cb of [...statusListeners]) {
    try {
      cb(s, prev);
    } catch (err) {
      console.error("[app] status listener failed", err);
    }
  }
}

/** POST /api/status/refresh (debounced server-side unless force) -> the new status. */
async function refreshStatus(force = false) {
  const s = await api.post("/api/status/refresh", force ? { force: true } : {});
  setStatus(s);
  return s;
}

/** Save the UI language and reload the app in it. */
async function setLanguage(lang) {
  await api.post("/api/prefs", { language: lang });
  location.reload();
}

function shopLabel(shop, index) {
  if (shop && shop.name) return shop.name;
  if (shop && shop.label) return shop.label; // server-made label ("Mağaza 2"), when it sends one
  return index > 0 ? t("shop.unnamed", { n: index + 1 }) : t("shop.default_name");
}

function renderShop() {
  if (!els.shopName) return;
  const sess = state.session || {};
  const shops = sess.shops || [];
  const idx = shops.findIndex((s) => s.id === sess.shop_id);
  const st = (state.status && state.status.state) || "checking";
  const statusName = state.status && state.status.shop && state.status.shop.name;
  const name = statusName || shopLabel(shops[idx], Math.max(idx, 0));
  els.shopName.textContent = name;
  els.shopName.title = name;
  els.shopState.className = cx("shop-state", `tone-${STATE_TONE[st] || "muted"}`);
  mount(els.shopState, h("span", { class: "dot", "aria-hidden": "true" }), t(`status.${st}`));
}

function renderBanner() {
  if (!els.banner) return;
  if (state.showLost) {
    mount(els.banner, infoNote({ icon: "alert", tone: "warning", text: t("connection.lost"), action: spinner({ size: 14 }) }));
    return;
  }
  const st = state.status && state.status.state;
  const page = state.currentPage;
  if (!PROBLEM_STATES.has(st) || page === "connect" || page === "settings") {
    mount(els.banner);
    return;
  }
  const action =
    st === "offline" || st === "error"
      ? button({
          label: t("status.banner.retry"),
          icon: "refresh",
          size: "sm",
          autoLoading: true,
          onClick: () => refreshStatus(true).catch((err) => toast({ tone: "danger", title: errorText(err) })),
        })
      : button({ label: t("status.banner.action"), iconRight: "arrow-right", size: "sm", onClick: () => navigate("/kurulum/magaza") });
  mount(
    els.banner,
    infoNote({ icon: st === "bad_keys" ? "key" : "alert", tone: st === "bad_keys" ? "danger" : "warning", text: t(`status.banner.${st}`), action }),
  );
}

function onConnection({ connected }) {
  state.connected = connected;
  clearTimeout(state.lostTimer);
  if (connected) {
    if (state.showLost) {
      state.showLost = false;
      renderBanner();
    }
    return;
  }
  state.lostTimer = setTimeout(() => {
    if (!state.connected && !state.stopped) {
      state.showLost = true;
      renderBanner();
    }
  }, 2500);
}

async function refreshSession() {
  const s = await api.get("/api/session");
  if (state.session && s.language && s.language !== state.session.language) {
    location.reload();
    return false;
  }
  state.session = s;
  return true;
}

async function applyShopChange() {
  try {
    if (!(await refreshSession())) return;
  } catch (err) {
    if (err.code !== "no_session") console.warn("[app] session refresh failed", err);
    return;
  }
  try {
    setStatus(await api.get("/api/status"));
  } catch {
    /* status arrives by SSE anyway */
  }
  loadNotifications();
  if (state.session.shop_id !== state.mountedShopId) await remount();
  else renderShop();
}

function queueShopChange() {
  shopChain = shopChain.then(applyShopChange, applyShopChange);
  return shopChain;
}

async function switchShop(id) {
  if (!state.session || id === state.session.shop_id) return;
  try {
    await api.post("/api/shops/switch", { id });
  } catch (err) {
    toast({ tone: "danger", title: errorText(err) });
    return;
  }
  await queueShopChange();
}

async function addShop() {
  try {
    await api.post("/api/shops/add", {});
  } catch (err) {
    toast({ tone: "danger", title: errorText(err) });
    return;
  }
  try {
    await refreshSession();
    setStatus(await api.get("/api/status"));
  } catch {
    /* ignore */
  }
  loadNotifications();
  navigate("/kurulum/magaza");
  toast({ tone: "success", title: t("shop.added"), message: t("shop.added_msg") });
}

function openShopMenu() {
  const sess = state.session || {};
  const shops = sess.shops || [];
  const items = [];
  if (shops.length) {
    items.push({ header: t("shop.shops") });
    shops.forEach((s, i) =>
      items.push({
        label: shopLabel(s, i),
        icon: "store",
        checked: s.id === sess.shop_id,
        hint: s.connected ? undefined : t("shop.not_connected_hint"),
        onClick: () => switchShop(s.id),
      }),
    );
  }
  items.push({ label: t("shop.add"), icon: "plus", onClick: addShop });
  items.push({ divider: true });
  items.push({ label: t("shop.settings"), icon: "settings", onClick: () => navigate("/ayarlar") });
  items.push({ label: t("shop.quit"), icon: "power", danger: true, onClick: quitApp });
  menu(els.shopCard, items, { placement: "top-start", width: Math.max(230, els.shopCard.offsetWidth) });
}

async function quitApp() {
  const ok = await confirm({ title: t("quit.title"), message: t("quit.message"), confirmLabel: t("quit.confirm"), danger: true, icon: "power" });
  if (!ok) return;
  try {
    await api.post("/api/quit", {});
  } catch (err) {
    if (err.code === "busy") {
      const force = await confirm({
        title: t("quit.busy_title"),
        message: t("quit.busy_message"),
        confirmLabel: t("quit.force"),
        danger: true,
        icon: "power",
      });
      if (!force) return;
      try {
        await api.post("/api/quit", { force: true });
      } catch (err2) {
        if (err2.code !== "network") {
          toast({ tone: "danger", title: errorText(err2) });
          return;
        }
      }
    } else if (err.code !== "network") {
      // A network error here usually means the server shut down before answering.
      toast({ tone: "danger", title: errorText(err) });
      return;
    }
  }
  showQuitPage();
}

// ------------------------------------------------------------------ notifications

async function loadNotifications() {
  try {
    const r = await api.get("/api/notifications");
    state.notifications = (r && r.items) || [];
    state.unread = (r && r.unread) || 0;
  } catch {
    return;
  }
  updateBell();
  if (notifPop) renderNotifList();
}

function updateBell() {
  if (!els.bell) return;
  els.bell.setBadge(state.unread);
  const label = state.unread
    ? `${t("notifications.open")} (${t("notifications.unread", { n: state.unread })})`
    : t("notifications.open");
  els.bell.setAttribute("aria-label", label);
  els.bell.title = label;
}

function notifText(n) {
  const ns = n.ns || "common";
  return i18n.translate(ns, `${ns}:${n.key}`, n.params || {});
}

function renderNotifList() {
  if (!notifPop) return;
  const list = notifPop.el.querySelector(".notif-list");
  if (!list) return;
  if (!state.notifications.length) {
    mount(list, h("div", { class: "notif-empty" }, emptyState({ icon: "bell", title: t("notifications.empty"), message: t("notifications.empty_msg"), compact: true })));
    return;
  }
  mount(
    list,
    state.notifications.map((n) => {
      const tone = n.tone || "info";
      const unread = notifPop.unreadIds.has(n.id);
      const inner = [
        h("span", { class: cx("icon-tile", `tone-${tone}`) }, icon(NOTIF_ICON[tone] || "info", { size: 14 })),
        h("span", { class: "notif-text" }, notifText(n), h("span", { class: "notif-time" }, relative(n.at))),
        unread ? h("span", { class: "notif-dot", "aria-hidden": "true" }) : null,
      ];
      const cls = cx("notif-item", unread && "is-unread");
      if (n.link) {
        return h(
          "button",
          {
            type: "button",
            class: cls,
            onClick: () => {
              if (notifPop) notifPop.close();
              navigate(n.link);
            },
          },
          inner,
        );
      }
      return h("div", { class: cls }, inner);
    }),
  );
}

async function toggleNotifications() {
  if (els.bell.__popover) {
    els.bell.__popover.close();
    return;
  }
  const namespaces = state.notifications.map((n) => n.ns).filter((ns) => ns && ns !== "common");
  await i18n.loadNamespaces(namespaces);
  if (els.bell.__popover) return;
  const unreadIds = new Set(state.notifications.filter((n) => !n.read).map((n) => n.id));
  const head = h(
    "div",
    { class: "notif-head" },
    h("h2", null, t("notifications.title")),
    state.unread ? badge({ text: t("notifications.unread", { n: state.unread }), tone: "accent", size: "sm" }) : null,
  );
  const pop = popover(els.bell, [head, h("div", { class: "notif-list" })], {
    placement: "bottom-end",
    width: 360,
    class: "notif-pop",
    role: "dialog",
    onClose: () => {
      notifPop = null;
    },
  });
  if (!pop) return;
  pop.el.setAttribute("aria-label", t("notifications.title"));
  pop.unreadIds = unreadIds;
  notifPop = pop;
  renderNotifList();
  pop.reposition();
  pop.el.focus();
  if (state.unread > 0) {
    try {
      await api.post("/api/notifications/read", {});
      state.unread = 0;
      state.notifications = state.notifications.map((n) => ({ ...n, read: true }));
      updateBell();
    } catch {
      /* try again next time */
    }
  }
}

async function onNotification(n) {
  if (!n || typeof n !== "object") return;
  state.notifications = [n, ...state.notifications.filter((x) => x.id !== n.id)].slice(0, 50);
  if (!n.read) state.unread += 1;
  updateBell();
  if (n.ns && n.ns !== "common") await i18n.loadNamespace(n.ns);
  if (notifPop) {
    if (!n.read) notifPop.unreadIds.add(n.id);
    renderNotifList();
  }
}

async function onReconnect() {
  try {
    if (!(await refreshSession())) return;
  } catch {
    return;
  }
  loadNotifications();
  if (state.session.shop_id !== state.mountedShopId) queueShopChange();
  else renderShop();
}

// ------------------------------------------------------------------ layout

function navLink(item) {
  const a = h(
    "a",
    { href: item.path, class: "nav-item", dataset: { page: item.page } },
    icon(item.icon, { size: 17 }),
    h("span", null, t(`nav.${item.page}`)),
  );
  els.nav.set(item.page, a);
  return a;
}

function renderShell() {
  const groups = NAV.map((g) => {
    const labelId = `nav-${g.group}`;
    return h(
      "nav",
      { class: cx("nav", `nav-${g.group}`), "aria-labelledby": labelId },
      h("p", { class: "nav-label", id: labelId }, t(`nav.group.${g.group}`)),
      h("div", { class: "nav-list" }, g.items.map(navLink)),
    );
  });

  els.shopName = h("span", { class: "shop-name" });
  els.shopState = h("span", { class: "shop-state" });
  els.shopCard = h(
    "button",
    { type: "button", class: "shop-card", title: t("shop.menu_label"), "aria-haspopup": "menu", "aria-expanded": "false", onClick: openShopMenu },
    h("span", { class: "shop-icon" }, icon("box", { size: 15 })),
    h("span", { class: "shop-text" }, els.shopName, els.shopState),
    h("span", { class: "shop-chev" }, icon("chevrons-up-down", { size: 14 })),
  );

  const sidebar = h(
    "aside",
    { class: "sidebar" },
    h(
      "a",
      { class: "brand", href: "/panel" },
      logoMark({ size: 28 }),
      h("span", { class: "brand-text" }, h("span", { class: "brand-name" }, t("app.name")), h("span", { class: "brand-tag" }, t("app.tagline"))),
    ),
    groups,
    h("div", { class: "shop-area" }, els.shopCard, h("p", { class: "trademark", lang: "en" }, t("app.trademark"))),
  );

  els.title = h("h1", { class: "page-title" });
  els.subtitle = h("p", { class: "page-subtitle" });
  els.pageActions = h("div", { class: "page-actions" });
  els.bell = iconButton({ icon: "bell", iconSize: 15, title: t("notifications.open"), onClick: toggleNotifications });
  els.bell.setAttribute("aria-haspopup", "dialog");
  els.bell.setAttribute("aria-expanded", "false");
  const topbar = h(
    "header",
    { class: "topbar" },
    h("div", { class: "topbar-titles" }, els.title, els.subtitle),
    h("div", { class: "topbar-actions" }, els.pageActions, els.bell),
  );
  els.banner = h("div", { class: "banner-slot", role: "status" });
  els.pageHost = h("div", { class: "page-host" });
  els.content = h("main", { class: "content", id: "content", tabindex: "-1" }, els.banner, els.pageHost);

  mount(
    root,
    h("a", { class: "skip-link", href: "#content", "data-external": "" }, t("common.skip_to_content")),
    h("div", { class: "app" }, sidebar, h("div", { class: "main" }, topbar, els.content)),
  );
  updateBell();
  renderShop();
}

// ------------------------------------------------------------------ full-page states

function fullPage(...children) {
  state.stopped = true;
  events.close();
  unmountCurrent();
  closeModals();
  closePopovers();
  document.querySelectorAll(".toasts, .popover").forEach((n) => n.remove());
  mount(root, h("div", { class: "fullpage" }, h("div", { class: "fullpage-box" }, logoMark({ size: 48 }), children)));
}

function showNoSession() {
  const msg = i18n.allLanguages("fullpage.no_session");
  document.title = "stallkit";
  fullPage(
    h("p", { class: "fullpage-msg", lang: "tr" }, msg.tr),
    h("p", { class: "fullpage-msg", lang: "en" }, msg.en),
  );
}

function showQuitPage() {
  document.title = "stallkit";
  fullPage(h("p", { class: "fullpage-msg" }, t("fullpage.quit")));
}

function showUnreachable() {
  document.title = "stallkit";
  fullPage(
    h("p", { class: "fullpage-title" }, t("fullpage.unreachable_title")),
    h("p", { class: "fullpage-msg" }, t("fullpage.unreachable")),
    button({ label: t("common.retry"), icon: "refresh", variant: "secondary", onClick: () => location.reload() }),
  );
}

// ------------------------------------------------------------------ boot

async function boot() {
  root = document.getElementById("app");
  onNoSession(showNoSession);
  await i18n.loadNamespace("common");

  let session;
  try {
    session = await api.get("/api/session");
  } catch (err) {
    if (err.code === "no_session") showNoSession();
    else showUnreachable(err);
    return;
  }
  state.session = session;
  i18n.setLang(session.language);

  try {
    state.status = await api.get("/api/status");
  } catch (err) {
    if (err.code === "no_session") return;
    state.status = null;
  }
  if (state.stopped) return;

  renderShell();
  events.on("status", setStatus);
  events.on("shop", () => queueShopChange());
  events.on("notification", onNotification);
  events.on("connection", onConnection);
  events.on("reconnect", onReconnect);
  events.connect();
  loadNotifications();

  window.addEventListener("popstate", () => route());
  document.addEventListener("click", interceptLinks);
  // A file dropped outside a dropzone must not make the browser open it (and leave the app).
  for (const type of ["dragover", "drop"]) {
    window.addEventListener(type, (e) => {
      const target = e.target && e.target.closest ? e.target.closest(".dropzone, [data-drop-target]") : null;
      if (target) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "none";
    });
  }
  await route();
}

boot().catch((err) => {
  console.error("[app] boot failed", err);
  if (root && !state.stopped) showUnreachable(err);
});

// Exposed for debugging from the browser console only.
export const _debug = { state, navigate, remount, refreshStatus, setHeader };
