// Panel (/panel): greeting, the "Nasıl çalışıyor?" setup tiles while setup is incomplete,
// six stat tiles read from Etsy, recent activity and quick actions.
//
// GET /api/dashboard is local and instant (setup, recent, the last numbers); the numbers
// themselves come from GET /api/dashboard/stats, which asks Etsy (cached server-side).

import { button, card, cx, debounce, emptyState, h, infoNote, mount, progressBar, scoreRing, skeleton, spinner } from "../ui.js";
import { icon } from "../icons.js";
import { locale, loadNamespaces, translate } from "../i18n.js";
import { money, monthName, number, relative } from "../format.js";

const STEP_ICON = { account: "user", shop: "link", mockups: "image", designs: "upload" };
const STEP_LINK = { account: "/kurulum/magaza", shop: "/kurulum/magaza", mockups: "/kurulum/mockuplar", designs: "/tasarim-yukle" };
const NOTE_ICON = { success: "check", warning: "alert", danger: "alert-circle", info: "info" };
// Jobs whose end changes the numbers on this page.
// Job kinds (as the server starts them) whose end changes the numbers on the tiles.
const STAT_JOBS = new Set(["designs", "publish", "listings-import", "orders"]);

export default {
  async mount(el, ctx) {
    const t = ctx.t;
    let overview = null;
    let stats = null;
    let statsLoading = false;
    let statsSeq = 0;
    const timers = new Set();

    // ------------------------------------------------------------------ layout
    const refreshBtn = button({
      label: t("refresh"),
      icon: "refresh",
      variant: "secondary",
      autoLoading: true,
      // The setup state too (keys, connection, folders): a problem fixed elsewhere clears here.
      onClick: () => Promise.all([loadOverview(), loadStats(true), ctx.refreshStatus(true).catch(() => {})]),
    });
    ctx.setHeader({ actions: [refreshBtn] });

    const helloEl = h("section", { class: "dash-hello" });
    const setupEl = h("div", { class: "dash-setup-slot" });
    const noteEl = h("div", { class: "dash-note-slot" });
    const statsEl = h("section", { class: "dash-stats", "aria-label": t("stats.label") });
    const recentBody = h("div", { class: "dash-recent" });
    const quickBody = h("div", { class: "dash-quick" });
    const recentCard = card({ title: t("recent.title"), subtitle: t("recent.subtitle"), icon: "clock", iconTone: "neutral", body: recentBody, class: "dash-recent-card" });
    const quickCard = card({ title: t("quick.title"), subtitle: t("quick.subtitle"), icon: "zap", body: quickBody, class: "dash-quick-card" });
    el.append(helloEl, setupEl, noteEl, statsEl, h("div", { class: "dash-bottom" }, recentCard, quickCard));

    const tiles = buildStatTiles();
    mount(statsEl, tiles.map((x) => x.el));
    renderHello();
    mount(recentBody, skeleton({ lines: 4, height: 14, gap: 16 }));
    renderQuick();

    // ------------------------------------------------------------------ data
    async function loadOverview() {
      try {
        overview = await ctx.api.get("/api/dashboard", null, { signal: ctx.signal });
      } catch (err) {
        if (ctx.api.isAbort(err)) return;
        mount(noteEl, infoNote({ tone: "danger", icon: "alert", text: [h("b", null, t("error.load")), " ", ctx.api.errorText(err, t)] }));
        return;
      }
      if (!ctx.isActive()) return;
      if (!stats && overview.stats) applyStats({ available: true, error: null, stats: overview.stats }, true);
      renderHello();
      renderSetup();
      await renderRecent();
      renderQuick();
    }

    async function loadStats(refresh = false) {
      const seq = ++statsSeq;
      statsLoading = true;
      if (!stats || refresh) tiles.forEach((x) => x.loading(!stats));
      try {
        const data = await ctx.api.get("/api/dashboard/stats", refresh ? { refresh: 1 } : null, { signal: ctx.signal });
        if (seq !== statsSeq || !ctx.isActive()) return;
        statsLoading = false;
        applyStats(data, false);
      } catch (err) {
        if (ctx.api.isAbort(err) || seq !== statsSeq) return;
        statsLoading = false;
        const error = { code: err.code, message: err.message, params: err.params };
        applyStats({ available: true, error, stats: {} }, false);
      }
    }

    function applyStats(data, fromCache) {
      stats = data;
      const all = data.stats || {};
      for (const tile of tiles) tile.render(all[tile.id] || { value: null, error: data.error });
      if (!fromCache) renderStatsNote();
      renderHello();
      renderQuick();
    }

    // ------------------------------------------------------------------ greeting
    function renderHello() {
      const st = ctx.status() || {};
      const name = st.shop && st.shop.name;
      let today = "";
      try {
        today = new Intl.DateTimeFormat(locale(), { weekday: "long", day: "numeric", month: "long" }).format(new Date());
      } catch {
        today = "";
      }
      const state = st.state || "checking";
      const tone = state === "connected" ? "success" : ["bad_keys"].includes(state) ? "danger" : ["reconnect", "offline", "error"].includes(state) ? "warning" : "muted";
      const cachedAt = stats && stats.stats ? newest(stats.stats) : null;
      mount(
        helloEl,
        h(
          "div",
          { class: "dash-hello-text" },
          h("h2", { class: "dash-hello-title" }, name ? t("hello_name", { name }) : t("hello")),
          h(
            "p",
            { class: "dash-hello-sub" },
            today ? h("span", { class: "dash-today" }, today) : null,
            h("span", { class: cx("dash-state", `tone-${tone}`) }, h("span", { class: "dot", "aria-hidden": "true" }), t(`status.${state}`)),
          ),
        ),
        h(
          "p",
          { class: "dash-updated muted" },
          statsLoading && stats ? [spinner({ size: 12 }), " ", t("updating")] : cachedAt ? t("updated", { time: relative(cachedAt) }) : null,
        ),
      );
    }

    function newest(all) {
      let best = null;
      for (const s of Object.values(all)) if (s && s.cached_at && (!best || s.cached_at > best)) best = s.cached_at;
      return best;
    }

    // ------------------------------------------------------------------ setup tiles (slide t150)
    function renderSetup() {
      const setup = overview && overview.setup;
      if (!setup || setup.complete || setup.checking) {
        mount(setupEl);
        return;
      }
      const tilesEls = setup.steps.map((step, i) => setupTile(step, i, setup));
      const body = h(
        "div",
        { class: "setup-flow" },
        h(
          "div",
          { class: "setup-brackets", "aria-hidden": "true" },
          h("div", { class: "setup-bracket is-once" }, h("span", null, t("setup.once"))),
          h("div", { class: "setup-bracket is-every" }, h("span", null, t("setup.every"))),
        ),
        h("div", { class: "setup-tiles" }, tilesEls),
      );
      mount(setupEl, card({ title: t("setup.title"), subtitle: t("setup.subtitle"), icon: "sparkles", body, class: "dash-setup" }));
    }

    function setupTile(step, i, setup) {
      let sub = t(`setup.${step.id}_sub`);
      if (step.id === "mockups" && setup.mockups > 0) sub = t("setup.mockups_n", { n: setup.mockups });
      if (step.id === "designs") sub = setup.template_title ? t("setup.designs_ready", { title: setup.template_title }) : t("setup.designs_template");
      if (step.state !== "done" && step.id === "account" && setup.problem === "bad_keys") sub = t("setup.bad_keys");
      if (step.state !== "done" && step.id === "shop" && setup.problem === "reconnect") sub = t("setup.reconnect");
      let href = STEP_LINK[step.id];
      if (step.id === "designs" && !setup.template_title && step.state !== "done") href = "/kurulum/sablon";
      const stateLabel = h(
        "span",
        { class: cx("setup-state", `is-${step.state}`) },
        step.state === "done" ? icon("check", { size: 12, strokeWidth: 2.6 }) : null,
        // The fourth step is done every time: "done" there means ready to upload.
        t(step.id === "designs" && step.state === "done" ? "setup.state.ready" : `setup.state.${step.state}`),
      );
      return h(
        "a",
        { class: cx("setup-tile", `is-${step.state}`, step.id === "designs" && "is-every"), href, "aria-current": step.state === "current" ? "step" : undefined },
        h(
          "span",
          { class: "setup-tile-top" },
          h("span", { class: cx("icon-tile", step.state === "done" ? "tone-success" : step.state === "current" ? "tone-accent" : "tone-neutral") }, icon(step.state === "done" ? "check" : STEP_ICON[step.id], { size: 16 })),
          h("span", { class: "setup-num num", "aria-hidden": "true" }, String(i + 1)),
        ),
        h("span", { class: "setup-tile-title" }, t(`setup.${step.id}`)),
        h("span", { class: "setup-tile-sub" }, sub),
        h("span", { class: "setup-tile-foot" }, stateLabel, step.state === "current" ? h("span", { class: "setup-go" }, t("setup.go"), icon("arrow-right", { size: 13 })) : null),
      );
    }

    // ------------------------------------------------------------------ stat tiles
    function buildStatTiles() {
      const defs = [
        { id: "active", icon: "list", tone: "accent", href: "/ilanlar?tab=active" },
        { id: "draft", icon: "file", tone: "neutral", href: "/ilanlar?tab=draft" },
        { id: "to_ship", icon: "truck", tone: "warning", href: "/siparisler" },
        { id: "seo", icon: "search", tone: "info", href: "/seo" },
        { id: "revenue", icon: "dollar", tone: "success", href: "/kar-zarar" },
        { id: "quota", icon: "zap", tone: "neutral", href: "/kurulum/magaza" },
      ];
      return defs.map((d) => {
        const valueEl = h("p", { class: "stat-value num" });
        const subEl = h("p", { class: "stat-sub" });
        const ring = d.id === "seo" ? scoreRing({ score: null, size: 38 }) : null;
        const el = h(
          "a",
          { class: cx("stat", "dash-stat", `dash-stat-${d.id}`), href: d.href },
          h(
            "div",
            { class: "stat-head" },
            h("span", { class: cx("icon-tile", "icon-tile-sm", `tone-${d.tone}`) }, icon(d.icon, { size: 15 })),
            h("span", { class: "stat-label" }, t(`stat.${d.id}`)),
            h("span", { class: "dash-stat-go", "aria-hidden": "true" }, icon("arrow-right", { size: 14 })),
          ),
          h("div", { class: "dash-stat-row" }, valueEl, ring),
          subEl,
        );
        const tile = {
          id: d.id,
          el,
          loading(show) {
            if (!show) return;
            mount(valueEl, h("span", { class: "skeleton", style: { width: d.id === "revenue" ? 120 : 64, height: 26 } }));
            mount(subEl, h("span", { class: "skeleton", style: { width: "70%", height: 10 } }));
            if (ring) ring.update(null);
          },
          render(s) {
            el.classList.remove("has-error", "is-alert", "is-idle");
            el.removeAttribute("title");
            if (ring) ring.hidden = false;
            if (!s || s.value === null || s.value === undefined) {
              valueEl.textContent = "–";
              if (ring) ring.hidden = true;
              const err = s && s.error;
              if (err && err.code === "setup_needed") el.classList.add("is-idle");
              if (d.id === "seo" && s && !err && s.sample === 0) {
                mount(subEl, t("stat.seo_none"));
              } else if (!err) {
                mount(subEl, t(`stat.${d.id}_sub`));
              } else if (err.code === "setup_needed") {
                mount(subEl, t("stat.needs_connect"));
              } else {
                el.classList.add("has-error");
                el.title = ctx.api.errorText(err, t);
                mount(subEl, h("span", { class: "dash-stat-error" }, icon("alert", { size: 12 }), t("stat.error")));
              }
              return;
            }
            if (d.id === "revenue") {
              valueEl.textContent = money(s.value, s.currency);
              const m = Number(String(s.month || "").split("-")[1]);
              const month = m ? monthName(m - 1) : "";
              mount(subEl, t("stat.revenue_sub", { n: s.orders, month }), s.partial ? ` ${t("stat.revenue_partial")}` : "");
            } else if (d.id === "seo") {
              valueEl.textContent = String(s.value);
              ring.update(s.value);
              mount(subEl, t("stat.seo_sub", { n: s.sample }), s.low ? h("span", { class: "dash-stat-warn" }, ` · ${t("stat.seo_low", { n: s.low })}`) : null);
            } else if (d.id === "to_ship") {
              valueEl.textContent = number(s.value);
              el.classList.toggle("is-alert", s.value > 0);
              mount(subEl, s.value > 0 ? t("stat.to_ship_sub") : t("stat.to_ship_none"));
            } else {
              valueEl.textContent = number(s.value);
              mount(subEl, t(`stat.${d.id}_sub`));
            }
          },
        };
        tile.loading(true);
        return tile;
      });
    }

    function renderStatsNote() {
      const err = stats && stats.error;
      if (!err || err.code === "setup_needed") {
        mount(noteEl);
        return;
      }
      const retry = button({ label: t("common.retry"), icon: "refresh", size: "sm", autoLoading: true, onClick: () => loadStats(true) });
      mount(noteEl, infoNote({ tone: "warning", icon: "alert", text: t("stats.error_all", { reason: ctx.api.errorText(err, t) }), action: retry }));
    }

    // ------------------------------------------------------------------ recent activity
    async function renderRecent() {
      const items = (overview && overview.recent) || [];
      const namespaces = new Set();
      for (const it of items) {
        if (it.type === "notification" && it.ns) namespaces.add(it.ns);
        const m = it.type === "job" && /^([a-z][a-z0-9-]*):/.exec(it.title_key || "");
        if (m) namespaces.add(m[1]);
      }
      namespaces.delete("common");
      if (namespaces.size) await loadNamespaces([...namespaces]);
      if (!ctx.isActive()) return;
      if (!items.length) {
        mount(recentBody, emptyState({ icon: "clock", title: t("recent.empty"), message: t("recent.empty_msg"), compact: true }));
        return;
      }
      mount(recentBody, h("ul", { class: "recent-list" }, items.map(recentItem)));
    }

    function recentItem(it) {
      if (it.type === "job") {
        const p = it.progress || {};
        const bar = progressBar({ value: p.total ? p.done : null, max: p.total || 100, size: "sm" });
        const label = it.title_key ? translate(ctx.page, it.title_key, it.params || {}) : it.kind;
        return h(
          "li",
          { class: "recent-item is-job" },
          h("span", { class: "icon-tile icon-tile-sm tone-accent" }, it.status === "running" ? spinner({ size: 14, tone: "accent" }) : icon("clock", { size: 14 })),
          h(
            "span",
            { class: "recent-text" },
            h("span", { class: "recent-title" }, label),
            h("span", { class: "recent-meta" }, t(`recent.${it.status}`), p.total ? h("span", { class: "num" }, ` · ${p.done}/${p.total}`) : null),
            it.status === "running" ? bar.el : null,
          ),
          h("span", { class: "recent-time" }, relative(it.at)),
        );
      }
      const tone = it.tone || "info";
      const text = translate(it.ns || "common", `${it.ns || "common"}:${it.key}`, it.params || {});
      const inner = [
        h("span", { class: cx("icon-tile", "icon-tile-sm", `tone-${tone}`) }, icon(NOTE_ICON[tone] || "info", { size: 14 })),
        h("span", { class: "recent-text" }, h("span", { class: "recent-title" }, text)),
        h("span", { class: "recent-time" }, relative(it.at)),
      ];
      if (it.link) return h("li", { class: "recent-item" }, h("a", { class: "recent-link", href: it.link }, inner));
      return h("li", { class: "recent-item" }, inner);
    }

    // ------------------------------------------------------------------ quick actions
    function renderQuick() {
      const setup = overview && overview.setup;
      const pending = setup ? setup.designs_pending : 0;
      const toShip = stats && stats.stats && stats.stats.to_ship ? stats.stats.to_ship.value : null;
      const pin = overview && overview.pinterest;
      const rows = [
        quickRow({ href: "/tasarim-yukle", icon: "upload", primary: true, label: t("quick.upload"), sub: pending > 0 ? t("quick.upload_pending", { n: pending }) : t("quick.upload_sub") }),
        quickRow({ href: "/siparisler", icon: "truck", label: t("quick.orders"), sub: toShip > 0 ? t("quick.orders_waiting", { n: toShip }) : t("quick.orders_sub"), alert: toShip > 0 }),
        quickRow({ href: "/seo", icon: "search", label: t("quick.seo"), sub: t("quick.seo_sub") }),
      ];
      if (pin && (pin.due > 0 || pin.attention > 0)) {
        rows.push(
          quickRow({
            href: "/pinterest",
            icon: "pin",
            label: t("quick.pinterest"),
            sub: pin.attention > 0 ? t("quick.pinterest_attention", { n: pin.attention }) : t("quick.pinterest_due", { n: pin.due }),
            alert: pin.attention > 0,
          }),
        );
      }
      mount(quickBody, rows);
    }

    function quickRow({ href, icon: ic, label, sub, primary, alert }) {
      return h(
        "a",
        { class: cx("quick-action", primary && "is-primary", alert && "is-alert"), href },
        h("span", { class: cx("icon-tile", primary ? "tone-accent" : "tone-neutral") }, icon(ic, { size: 16 })),
        h("span", { class: "quick-text" }, h("span", { class: "quick-label" }, label), h("span", { class: "quick-sub" }, sub)),
        icon("chevron-right", { size: 16 }),
      );
    }

    // ------------------------------------------------------------------ live updates
    const reloadOverview = debounce(() => loadOverview(), 250);
    const reloadStats = debounce(() => loadStats(true), 1500);
    ctx.events.on("notification", () => reloadOverview());
    ctx.events.on("job", (job) => {
      reloadOverview();
      if (job && STAT_JOBS.has(job.kind) && ["done", "error", "cancelled"].includes(job.status)) reloadStats();
    });
    ctx.onStatus((s, prev) => {
      renderHello();
      reloadOverview();
      const was = prev && prev.state;
      if (s.state === "connected" && was !== "connected" && !statsLoading) loadStats(was === "reconnect" || was === "offline");
    });
    // Relative times ("5 dk önce") age while the page stays open.
    const tick = setInterval(() => {
      if (!ctx.isActive()) return;
      renderHello();
      if (overview) renderRecent();
    }, 60000);
    timers.add(tick);

    await loadOverview();
    loadStats(false);

    return () => {
      reloadOverview.cancel();
      reloadStats.cancel();
      for (const id of timers) clearInterval(id);
    };
  },
};
