// Siparişler (/siparisler): paid orders, the carrier and tracking number of each, and
// the upload to Etsy (a confirmed job: Etsy e-mails every buyer, which cannot be undone).
//
// Server: GET /api/orders, /api/orders/summary, /api/orders/carriers,
// POST /api/orders/country, /api/orders/ship, /api/orders/export.csv,
// /api/orders/import-tracking. The ship job is kind "orders"; each row it finishes
// arrives as a job-event of type "row".

import {
  badge,
  button,
  card,
  cx,
  emptyState,
  h,
  infoNote,
  menu,
  mount,
  pagination,
  searchInput,
  spinner,
  table,
  tabs,
  thumb,
} from "../ui.js";
import { icon } from "../icons.js";
import { date, money, relative, time } from "../format.js";

const TABS = ["unshipped", "shipped", "delivered", "all"];
const SETUP_STATES = new Set(["keys", "bad_keys", "disconnected"]);
const ACTIVE = new Set(["queued", "running"]);
const OTHER = "other";
const SOLD_URL = "https://www.etsy.com/your/orders/sold";
// Ship-from countries offered in the selector (plus the shop's own, whatever it is).
const COUNTRIES = ["TR", "US", "GB", "CA", "AU", "DE", "FR", "NL", "IT", "ES", "PL", "IE", "BE", "AT",
  "SE", "DK", "FI", "NO", "PT", "GR", "UA", "IN", "JP", "MX", "NZ"];
const ROW_H = 57;
const CONFIRM_PREVIEW = 8;

/** Rows per page: as many as fit without scrolling, 8 at least (the video's 790 px). */
function pageSize() {
  const hgt = window.innerHeight || 800;
  return Math.max(8, Math.min(25, Math.floor((hgt - 334) / ROW_H)));
}

/** "Retro Mountain Sunset Shirt, Vintage Hiking Tee, ..." -> "Retro Mountain Sunset Shirt". */
function shortTitle(title) {
  const text = String(title || "");
  const first = text.split(/\s*[,|]\s*|\s+[-–—]\s+/)[0];
  return first.length >= 8 ? first : text;
}

function today() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Put nodes where "{name}" placeholders are in a translated string. */
function rich(text, nodes) {
  const out = [];
  let rest = text;
  for (;;) {
    const m = /\{([a-z]+)\}/.exec(rest);
    if (!m) break;
    if (m.index) out.push(rest.slice(0, m.index));
    out.push(nodes[m[1]] !== undefined ? nodes[m[1]] : m[0]);
    rest = rest.slice(m.index + m[0].length);
  }
  if (rest) out.push(rest);
  return out;
}

export default {
  async mount(el, ctx) {
    const t = ctx.t;
    const q0 = ctx.query || {};
    const S = {
      tab: TABS.includes(q0.tab) ? q0.tab : "unshipped",
      q: String(q0.q || ""),
      page: Math.max(1, parseInt(q0.page, 10) || 1),
      perPage: pageSize(),
      data: null,
      rows: [],
      known: new Map(), // receipt_id -> row, from every page loaded
      edits: new Map(), // receipt_id -> {carrier_name, tracking_code, note_to_buyer}
      selected: new Set(),
      results: new Map(), // receipt_id -> the ship job's answer for that row
      summary: null,
      carriers: null,
      defaultCarrier: "",
      job: null,
      jobIds: [],
      finished: new Set(), // ids of ship jobs already wrapped up
      lastDone: null,
      loadSeq: 0,
      setup: false,
      waiting: null, // every receipt id of the waiting list, in order (from the last full read)
    };

    // --------------------------------------------------------------- setup gate

    function renderSetup() {
      S.setup = true;
      ctx.setHeader({ subtitle: t("subtitle"), actions: [] });
      mount(
        el,
        card({
          class: "o-setup",
          body: emptyState({
            icon: "truck",
            title: t("setup.title"),
            message: t("setup.msg"),
            action: button({
              label: t("setup.action"),
              variant: "primary",
              iconRight: "arrow-right",
              onClick: () => ctx.navigate("/kurulum/magaza"),
            }),
          }),
        }),
      );
    }

    ctx.onStatus((s, prev) => {
      if (S.setup && s && s.state === "connected" && (!prev || prev.state !== "connected")) ctx.remount();
    });

    const st = ctx.status();
    if (st && SETUP_STATES.has(st.state)) {
      renderSetup();
      return () => {};
    }

    // --------------------------------------------------------------- carriers

    const carrierNames = () => ((S.carriers && S.carriers.carriers) || []).map((c) => c.name);

    function canonicalCarrier(name) {
      const value = String(name || "").trim();
      if (!value) return "";
      const folded = value.toLowerCase();
      if (folded === OTHER) return OTHER;
      return carrierNames().find((n) => n.toLowerCase() === folded) || value;
    }

    function carrierKnown(name) {
      if (!name) return false;
      if (!S.carriers || !S.carriers.carriers.length) return true; // no list: Etsy decides
      const folded = name.toLowerCase();
      return folded === OTHER || carrierNames().some((n) => n.toLowerCase() === folded);
    }

    const carrierFor = (id) => {
      const e = S.edits.get(id);
      return (e && e.carrier_name) || S.defaultCarrier || "";
    };
    const trackingFor = (id) => {
      const e = S.edits.get(id);
      return ((e && e.tracking_code) || "").trim();
    };

    function isEditable(row) {
      const r = S.results.get(row.receipt_id);
      return row.status === "unshipped" && !(r && r.status === "ok");
    }

    const inFlight = (id) => !!S.job && S.jobIds.includes(id) && !S.results.has(id);

    function isReady(id) {
      const row = S.known.get(id);
      if (row && !isEditable(row)) return false;
      const carrier = carrierFor(id);
      return !!trackingFor(id) && !!carrier && carrierKnown(carrier);
    }

    const readyIds = () => [...S.selected].filter((id) => isReady(id));

    function regionName(code) {
      try {
        return new Intl.DisplayNames([ctx.lang === "en" ? "en" : "tr"], { type: "region" }).of(code) || code;
      } catch {
        return code;
      }
    }

    function countryOptions(current) {
      const codes = new Set(COUNTRIES);
      if (current) codes.add(current);
      return [...codes]
        .map((code) => ({ value: code, label: regionName(code) }))
        .sort((a, b) => a.label.localeCompare(b.label, ctx.lang === "en" ? "en" : "tr"));
    }

    // --------------------------------------------------------------- header

    const sendBtn = button({
      label: t("send"),
      icon: "send",
      variant: "primary",
      disabled: true,
      onClick: () => send(),
    });
    const stopBtn = button({ label: t("send.stop"), icon: "stop", variant: "ghost", onClick: () => stopJob() });
    stopBtn.hidden = true;
    const csvBtn = button({
      label: t("csv.button"),
      icon: "file",
      iconRight: "chevron-down",
      variant: "secondary",
      onClick: () => openCsvMenu(),
    });
    ctx.setHeader({ actions: [stopBtn, csvBtn, sendBtn] });

    function syncSend() {
      if (S.job) {
        const p = S.job.progress || {};
        sendBtn.setLabel(t("send.progress", { done: p.done || 0, total: p.total || S.jobIds.length || "?" }));
        sendBtn.setCount(null);
        sendBtn.setLoading(true);
        stopBtn.hidden = !S.job.cancellable;
        return;
      }
      stopBtn.hidden = true;
      sendBtn.setLoading(false);
      sendBtn.setLabel(t("send"));
      const n = readyIds().length;
      sendBtn.setCount(n > 0 ? n : null);
      sendBtn.setDisabled(n === 0);
      sendBtn.title = n ? "" : t("send.hint");
    }

    function updateSubtitle() {
      let sub = t("subtitle");
      const sm = S.summary;
      if (S.lastDone && S.lastDone.sent > 0) sub = t("subtitle.shipped", { n: S.lastDone.sent });
      else if (sm) {
        const waiting = sm.counts.unshipped;
        if (waiting > 0) sub = t("subtitle.waiting", { n: waiting });
        else if (sm.shipped_month > 0) sub = t("subtitle.month", { n: sm.shipped_month });
        else sub = t("subtitle.none");
      }
      ctx.setHeader({ subtitle: sub });
    }

    // --------------------------------------------------------------- toolbar

    function countFor(id) {
      if (S.data && S.data.tab === id && !S.data.q) return S.data.total;
      return S.summary ? S.summary.counts[id] : undefined;
    }

    const tabItems = () =>
      TABS.map((id) => ({
        id,
        label: t(`tab.${id}`),
        // Like the video: the waiting count always, the others on their own tab.
        count: id === "unshipped" || id === S.tab ? countFor(id) : undefined,
      }));

    const tabsC = tabs({
      items: tabItems(),
      value: S.tab,
      ariaLabel: t("tabs.label"),
      onChange: (id) => {
        S.tab = id;
        S.page = 1;
        ctx.setQuery({ tab: id === "unshipped" ? null : id, page: null });
        tabsC.update(tabItems());
        syncCountry();
        load();
      },
    });

    // The ship-from country decides Etsy's carrier list: a quiet button, a menu of countries.
    const countryBtn = button({
      label: "",
      icon: "globe",
      iconRight: "chevron-down",
      variant: "ghost",
      size: "sm",
      class: "o-country",
      onClick: () => openCountryMenu(),
    });
    countryBtn.hidden = true;

    const countryLabel = (code) => {
      const name = regionName(code);
      return name.length <= 14 ? name : code;
    };

    function syncCountry() {
      const code = S.carriers && S.carriers.country;
      countryBtn.hidden = !code || !(S.tab === "unshipped" || S.tab === "all");
      if (!code) return;
      countryBtn.setLabel([h("span", { class: "o-country-prefix" }, `${t("country.prefix")} `), countryLabel(code)]);
      countryBtn.title = `${t("country.label")}: ${regionName(code)}`;
      countryBtn.setAttribute("aria-label", countryBtn.title);
    }

    function openCountryMenu() {
      const current = S.carriers && S.carriers.country;
      menu(
        countryBtn,
        [
          { header: t("country.label") },
          ...countryOptions(current).map((o) => ({
            label: o.label,
            hint: o.value,
            checked: o.value === current,
            onClick: () => {
              if (o.value !== current) changeCountry(o.value);
            },
          })),
        ],
        { placement: "bottom-start", width: 280, class: "o-country-menu" },
      );
    }

    const doneEl = h("div", { class: "o-done", role: "status" });
    doneEl.hidden = true;

    const search = searchInput({
      placeholder: t("search"),
      value: S.q,
      debounce: 300,
      class: "o-search",
      onInput: (v) => {
        S.q = v.trim();
        S.page = 1;
        ctx.setQuery({ q: S.q || null, page: null });
        load();
      },
    });

    const bar = h("div", { class: "o-bar" }, tabsC.el, countryBtn, h("div", { class: "spacer" }), doneEl, search);
    const alerts = h("div", { class: "o-alerts" });

    // --------------------------------------------------------------- table

    function renderOrder(row) {
      return h(
        "div",
        { class: "o-cell" },
        h("span", { class: "o-no num" }, `#${row.receipt_id}`),
        h("span", { class: "o-sub num" }, row.created ? `${date(row.created)} · ${time(row.created)}` : "–"),
      );
    }

    function renderBuyer(row) {
      const place = [row.city, row.state || row.country].filter(Boolean).join(", ") || row.country || "";
      return h(
        "div",
        { class: "o-cell" },
        h("span", { class: "o-name ellipsis" }, row.buyer || "–", row.is_gift ? h("span", { class: "o-gift" }, t("gift")) : null),
        place ? h("span", { class: "o-sub ellipsis" }, place) : null,
      );
    }

    function renderProduct(row) {
      const first = row.items[0];
      if (!first) return h("span", { class: "muted" }, "–");
      const parts = first.variations.map((v) => v.value || v.name).filter(Boolean);
      if (first.quantity > 1) parts.push(t("qty", { n: first.quantity }));
      const more = row.items.length - 1;
      const sub =
        parts.length || more > 0
          ? h(
              "span",
              { class: "o-sub o-variant" },
              parts.length ? h("span", { class: "ellipsis" }, parts.join(" · ")) : null,
              more > 0 ? h("span", { class: "o-more" }, t("more_items", { n: more })) : null,
            )
          : null;
      const titles = row.items.map((it) => (it.quantity > 1 ? `${it.title} (${it.quantity})` : it.title)).join("\n");
      return h(
        "div",
        { class: "o-product", title: titles },
        thumb({ src: first.thumb, size: 36, icon: "box", alt: "" }),
        h("div", { class: "o-cell" }, h("span", { class: "o-title ellipsis" }, shortTitle(first.title)), sub),
      );
    }

    function renderTotal(row) {
      return h("span", { class: "o-total num" }, row.total ? money(row.total) : "–");
    }

    const lastShipment = (row) => (row.shipments && row.shipments.length ? row.shipments[row.shipments.length - 1] : null);

    function carrierPill(name) {
      return h("span", { class: "o-carrier", title: name }, icon("truck", { size: 13 }), h("span", { class: "o-carrier-name" }, name));
    }

    function carrierWrapClass(wrap, value) {
      wrap.className = cx("o-carrier", "is-edit", !value && "is-empty", value && !carrierKnown(value) && "is-bad");
      wrap.title = value && !carrierKnown(value) ? t("carrier.unknown", { name: value }) : value || t("carrier.placeholder");
    }

    function carrierOptions(value) {
      const opts = [h("option", { value: "" }, t("carrier.placeholder"))];
      for (const name of carrierNames()) opts.push(h("option", { value: name }, name));
      opts.push(h("option", { value: OTHER }, t("carrier.other")));
      if (value && !carrierKnown(value)) opts.push(h("option", { value }, value));
      return opts;
    }

    function renderCarrier(row) {
      const id = row.receipt_id;
      if (!isEditable(row)) {
        const s = lastShipment(row);
        return s && s.carrier_name ? carrierPill(s.carrier_name) : h("span", { class: "muted" }, "–");
      }
      const value = canonicalCarrier(carrierFor(id));
      const sel = h("select", {
        class: "o-carrier-select",
        "aria-label": t("carrier.label"),
        disabled: !!S.job,
        dataset: { id: String(id) },
      }, carrierOptions(value));
      sel.value = value;
      const wrap = h("label", { class: "o-carrier is-edit" }, icon("truck", { size: 13 }), sel, h("span", { class: "o-chev" }, icon("chevron-down", { size: 12 })));
      carrierWrapClass(wrap, value);
      sel.addEventListener("change", () => {
        const edit = { ...(S.edits.get(id) || {}), carrier_name: sel.value };
        S.edits.set(id, edit);
        carrierWrapClass(wrap, sel.value);
        clearResult(id);
        if (!S.defaultCarrier && sel.value) {
          S.defaultCarrier = sel.value;
          refreshDefaultCarriers();
        }
        if (trackingFor(id)) ensureSelected(sel, id);
        syncSend();
      });
      return wrap;
    }

    /** Rows without their own carrier follow the default: update them in place (keeps focus). */
    function refreshDefaultCarriers() {
      for (const sel of el.querySelectorAll("select.o-carrier-select")) {
        const id = Number(sel.dataset.id);
        const own = S.edits.get(id);
        if (own && own.carrier_name) continue;
        const value = canonicalCarrier(S.defaultCarrier);
        mount(sel, carrierOptions(value));
        sel.value = value;
        carrierWrapClass(sel.closest(".o-carrier"), value);
      }
    }

    function renderTracking(row) {
      const id = row.receipt_id;
      if (!isEditable(row)) {
        const s = lastShipment(row);
        if (!s || !s.tracking_code) return h("span", { class: "muted" }, "–");
        return h(
          "span",
          { class: "o-track", title: s.tracking_code },
          icon("check", { size: 13, strokeWidth: 2.4 }),
          h("span", { class: "mono ellipsis" }, s.tracking_code),
        );
      }
      const input = h("input", {
        type: "text",
        class: "o-track-input mono",
        value: (S.edits.get(id) || {}).tracking_code || "",
        placeholder: t("tracking.placeholder"),
        autocomplete: "off",
        spellcheck: "false",
        maxlength: "64",
        disabled: !!S.job,
        "aria-label": t("tracking.label", { id }),
        dataset: { id: String(id) },
      });
      input.addEventListener("input", () => {
        S.edits.set(id, { ...(S.edits.get(id) || {}), tracking_code: input.value });
        clearResult(id);
        if (input.value.trim()) ensureSelected(input, id);
        syncSend();
      });
      input.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        const all = [...el.querySelectorAll("input.o-track-input:not(:disabled)")];
        const next = all[all.indexOf(input) + 1];
        if (next) next.focus();
        else input.blur();
      });
      return input;
    }

    function statusBadge(row) {
      const id = row.receipt_id;
      const r = S.results.get(id);
      if (inFlight(id)) {
        const sending = S.jobIds.find((x) => !S.results.has(x)) === id && S.job.status === "running";
        return sending
          ? h("span", { class: "badge tone-accent o-sending" }, spinner({ size: 11 }), h("span", null, t("status.sending")))
          : badge({ text: t("status.queued"), tone: "muted" });
      }
      if (r && r.status === "error") {
        return badge({ text: t("status.error"), tone: "danger", icon: "alert", title: rowErrorText(r) });
      }
      if (r && r.status === "skipped") {
        return badge({ text: t("status.skipped"), tone: "muted", title: rowErrorText(r) });
      }
      switch (row.status) {
        case "shipped":
          return badge({ text: t("status.shipped"), tone: "success", iconRight: "check" });
        case "delivered":
          return badge({ text: t("status.delivered"), tone: "neutral", iconRight: "check" });
        case "canceled":
          return badge({ text: t("status.canceled"), tone: "muted" });
        default:
          return badge({ text: t("status.unshipped"), tone: "warning", dot: true });
      }
    }

    function renderStatus(row) {
      return h("span", { class: "o-status", dataset: { sid: String(row.receipt_id) } }, statusBadge(row));
    }

    // Codes whose own words say more than Etsy's message; for the rest, Etsy's message.
    const WORDED = new Set(["tracking_restricted", "missing_scope", "not_waiting", "offline", "reconnect",
      "bad_keys", "rate_limited", "setup_needed", "cancelled"]);

    function rowErrorText(r) {
      const code = r.code || "internal";
      if ((WORDED.has(code) || !r.message) && t.has(`errors.${code}`)) {
        return t(`errors.${code}`, { message: r.message || "", status: r.http_status || "" });
      }
      if (code === "etsy_error" && r.http_status) {
        return `${t("errors.etsy_error", { status: r.http_status })} ${r.message || ""}`.trim();
      }
      return r.message || code;
    }

    function clearResult(id) {
      const r = S.results.get(id);
      if (!r || r.status === "ok") return;
      S.results.delete(id);
      const cell = el.querySelector(`[data-sid="${id}"]`);
      const row = S.known.get(id);
      if (cell && row) mount(cell, statusBadge(row));
      const tr = cell && cell.closest("tr");
      if (tr) tr.classList.remove("is-error");
    }

    /** Tick a row's checkbox the way a click would (no re-render: the input keeps focus). */
    function ensureSelected(node, id) {
      if (S.selected.has(id)) return;
      const tr = node.closest("tr");
      const cb = tr && tr.querySelector("td.tbl-check input.checkbox");
      if (cb && !cb.checked) {
        cb.checked = true;
        cb.dispatchEvent(new Event("change"));
      }
    }

    const emptyHost = h("div", { class: "o-empty" });
    const tbl = table({
      columns: [
        { key: "order", label: t("col.order"), width: 146, render: renderOrder },
        { key: "buyer", label: t("col.buyer"), width: 160, render: renderBuyer },
        { key: "product", label: t("col.product"), render: renderProduct },
        { key: "total", label: t("col.total"), width: 108, align: "right", render: renderTotal },
        { key: "carrier", label: t("col.carrier"), width: 131, render: renderCarrier, class: "o-td-carrier" },
        { key: "tracking", label: t("col.tracking"), width: 206, render: renderTracking, class: "o-td-tracking" },
        { key: "status", label: t("col.status"), width: 160, render: renderStatus },
      ],
      rows: [],
      rowKey: "receipt_id",
      selectable: true,
      selected: S.selected,
      skeletonRows: S.perPage,
      empty: emptyHost,
      class: "o-table",
      rowClass: (row) => {
        const r = S.results.get(row.receipt_id);
        return cx(r && r.status === "error" && "is-error", !isEditable(row) && "is-done");
      },
      onSelectionChange: (sel) => {
        S.selected = sel;
        syncSend();
      },
    });

    const rangeEl = h("span", { class: "o-range" });
    const pag = pagination({
      page: S.page,
      pages: 1,
      onChange: (p) => {
        S.page = p;
        ctx.setQuery({ page: p > 1 ? p : null });
        load();
      },
    });
    const foot = h("div", { class: "o-foot" }, rangeEl, h("div", { class: "spacer" }), pag);
    const tableHost = h("div", { class: "o-table-host" }, tbl.el);
    const tableCard = card({ pad: false, class: "o-card", body: [tableHost, foot] });
    const note = h("p", { class: "o-note" }, icon("info", { size: 14 }), h("span", null, t("note")));

    const fileInput = h("input", { type: "file", accept: ".csv,text/csv,text/plain", class: "sr-only", tabindex: "-1", "aria-hidden": "true" });
    fileInput.addEventListener("change", () => {
      const file = fileInput.files && fileInput.files[0];
      fileInput.value = "";
      if (file) importCsv(file);
    });

    el.append(bar, alerts, tableCard, note, fileInput);

    function setEmpty() {
      const searching = !!(S.data && S.data.q);
      const key = searching ? "search" : S.tab;
      mount(
        emptyHost,
        emptyState({
          icon: searching ? "search" : "truck",
          title: t(`empty.${key}`),
          message: searching ? t("empty.search_msg") : S.tab === "unshipped" ? t("empty.unshipped_msg") : null,
          compact: true,
        }),
      );
    }

    function renderRows() {
      setEmpty();
      tbl.update(S.rows, S.selected);
      const d = S.data;
      if (d && d.total > 0) {
        const from = (d.page - 1) * d.per_page + 1;
        const to = Math.min(d.total, from + S.rows.length - 1);
        const parts = rich(t("range", { from, to }), { total: h("b", { class: "num" }, String(d.total)) });
        if (d.truncated && d.q) parts.push(" · ", t("range.truncated", { n: d.scanned }));
        mount(rangeEl, parts);
      } else mount(rangeEl);
      foot.hidden = !d || d.total === 0;
      pag.hidden = !d || d.pages <= 1;
      if (d) pag.update(d.page, d.pages);
      tabsC.update(tabItems());
      syncSend();
    }

    function showError(err) {
      mount(
        tableHost,
        h(
          "div",
          { class: "o-error" },
          infoNote({
            tone: "danger",
            icon: "alert",
            title: t("load_error"),
            text: ctx.api.errorText(err, t),
            action: button({ label: t("common.retry"), icon: "refresh", size: "sm", onClick: () => load({ fresh: true }) }),
          }),
        ),
      );
      foot.hidden = true;
    }

    function renderBanner() {
      const done = S.lastDone;
      if (done && done.sent > 0) {
        doneEl.className = cx("o-done", done.sent < done.total && "is-partial");
        mount(doneEl, icon("check", { size: 15, strokeWidth: 2.4 }), h("span", null, t("banner.done", { sent: done.sent, total: done.total })));
        doneEl.hidden = false;
      } else doneEl.hidden = true;
      const restricted = (S.summary && S.summary.tracking_restricted) || (done && done.restricted);
      mount(
        alerts,
        restricted
          ? infoNote({
              tone: "warning",
              icon: "alert",
              text: [t("restricted.text"), " ", h("span", { class: "muted" }, t("restricted.csv"))],
              action: h(
                "a",
                { class: "btn btn-secondary btn-sm", href: SOLD_URL, target: "_blank", rel: "noopener noreferrer" },
                h("span", { class: "btn-label" }, t("restricted.link")),
                icon("external", { size: 14 }),
              ),
            })
          : null,
      );
    }

    // --------------------------------------------------------------- loading

    async function load({ fresh = false } = {}) {
      const seq = ++S.loadSeq;
      if (!tableHost.contains(tbl.el)) mount(tableHost, tbl.el);
      tbl.update(null);
      foot.hidden = true;
      try {
        const data = await ctx.api.get(
          "/api/orders",
          { tab: S.tab, q: S.q || undefined, page: S.page, per_page: S.perPage, fresh: fresh ? 1 : undefined },
          { signal: ctx.signal },
        );
        if (seq !== S.loadSeq) return;
        if (!data.rows.length && data.total > 0 && S.page > data.pages) {
          S.page = data.pages;
          ctx.setQuery({ page: S.page > 1 ? S.page : null });
          load();
          return;
        }
        S.data = data;
        S.rows = data.rows;
        if (data.tab === "unshipped" && !data.q && Array.isArray(data.ids)) S.waiting = data.ids;
        for (const row of data.rows) {
          const r = S.results.get(row.receipt_id);
          if (r && r.status === "ok") markShipped(row, r);
          S.known.set(row.receipt_id, row);
        }
        renderRows();
      } catch (err) {
        if (ctx.api.isAbort(err) || seq !== S.loadSeq) return;
        if (err.code === "setup_needed") {
          renderSetup();
          return;
        }
        showError(err);
      }
    }

    async function loadSummary(fresh = false) {
      try {
        S.summary = await ctx.api.get("/api/orders/summary", fresh ? { fresh: 1 } : null, { signal: ctx.signal });
        tabsC.update(tabItems());
        updateSubtitle();
        renderBanner();
      } catch (err) {
        // The list shows what went wrong; the counts only stay empty.
        if (!ctx.api.isAbort(err) && !(err instanceof ctx.api.ApiError)) console.warn("[orders] summary", err);
      }
    }

    function applyCarriers(c, changedCountry) {
      S.carriers = { ...c, carriers: c.carriers || [] };
      const last = canonicalCarrier(c.last_carrier || "");
      if (changedCountry || !S.defaultCarrier || !carrierKnown(S.defaultCarrier)) S.defaultCarrier = last;
      syncCountry();
      if (S.data) renderRows();
    }

    async function loadCarriers() {
      try {
        applyCarriers(await ctx.api.get("/api/orders/carriers", null, { signal: ctx.signal }), false);
      } catch (err) {
        // Without the list the carrier menu offers only "other"; the list shows the error.
        if (!ctx.api.isAbort(err) && !(err instanceof ctx.api.ApiError)) console.warn("[orders] carriers", err);
      }
    }

    async function changeCountry(code) {
      countryBtn.setLoading(true);
      try {
        applyCarriers(await ctx.api.post("/api/orders/country", { country: code }, { signal: ctx.signal }), true);
      } catch (err) {
        if (!ctx.api.isAbort(err)) ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t) });
      } finally {
        countryBtn.setLoading(false);
      }
    }

    // --------------------------------------------------------------- the ship job

    function markShipped(row, r) {
      row.status = "shipped";
      row.shipments = r.shipments && r.shipments.length ? r.shipments : [{ carrier_name: r.carrier_name, tracking_code: r.tracking_code }];
    }

    function applyResult(r) {
      if (!r || r.receipt_id === undefined) return;
      S.results.set(r.receipt_id, r);
      const row = S.known.get(r.receipt_id);
      if (row && r.status === "ok") markShipped(row, r);
    }

    function attachJob(job, ids) {
      S.job = job;
      if (ids) S.jobIds = ids;
      syncSend();
      if (S.data) renderRows();
    }

    async function finishJob(job) {
      if (S.finished.has(job.id)) return;
      S.finished.add(job.id);
      let full = job;
      if (!job.result || job.status !== "done") {
        try {
          full = await ctx.api.get(`/api/jobs/${job.id}`, null, { signal: ctx.signal });
        } catch {
          full = job;
        }
      }
      const state = full.state || {};
      for (const r of (full.result && full.result.rows) || state.rows || []) applyResult(r);
      S.job = null;
      S.jobIds = [];
      const res = full.result;
      if (full.status === "done" && res) {
        S.lastDone = res;
        doneToast(res);
      } else if (full.status === "cancelled") {
        S.lastDone = { sent: state.sent || 0, total: state.total || 0, restricted: false };
        ctx.toast({ tone: "info", title: t("done.cancelled", { sent: state.sent || 0 }) });
      } else if (full.status === "error") {
        ctx.toast({ tone: "danger", title: ctx.api.errorText(full.error || {}, t), timeout: 8000 });
      }
      renderRows();
      renderBanner();
      updateSubtitle();
      loadSummary(true);
    }

    function doneToast(res) {
      const skippedMsg = res.skipped && !res.stopped ? t("done.skipped", { n: res.skipped }) : "";
      if (res.restricted) {
        ctx.toast({ tone: "warning", title: t("done.restricted"), message: t("errors.tracking_restricted"), timeout: 9000 });
      } else if (res.sent > 0 && !res.failed) {
        ctx.toast({
          tone: "success",
          title: t("done.title", { n: res.sent }),
          message: [`${t("done.msg")} · ${relative(Date.now())}`, skippedMsg].filter(Boolean).join(" "),
          timeout: 8000,
        });
      } else if (res.sent > 0) {
        ctx.toast({ tone: "warning", title: t("done.partial", { sent: res.sent, failed: res.failed }), message: t("done.partial_msg"), timeout: 9000 });
      } else {
        const first = (res.rows || []).find((r) => r.status === "error");
        ctx.toast({ tone: "warning", title: t("done.nothing"), message: first ? rowErrorText(first) : skippedMsg, timeout: 9000 });
      }
    }

    ctx.events.on("job", (job) => {
      if (!job || job.kind !== "orders") return;
      if (S.job && job.id === S.job.id) {
        S.job = job;
        if (ACTIVE.has(job.status)) {
          syncSend();
          if (S.data) renderRows();
        } else finishJob(job);
      } else if (!S.job && ACTIVE.has(job.status) && !S.finished.has(job.id)) {
        attachJob(job, []); // started a moment ago (the answer is on its way) or in another tab
      }
    });

    ctx.events.on("job-event", (ev) => {
      if (!ev || ev.kind !== "orders" || ev.type !== "row" || !S.job || ev.job_id !== S.job.id) return;
      applyResult(ev.data);
      if (S.data) renderRows();
    });

    async function resumeJob() {
      try {
        const jobs = await ctx.api.get("/api/jobs", { kind: "orders" }, { signal: ctx.signal });
        const latest = Array.isArray(jobs) ? jobs[0] : null;
        if (!latest) return;
        const full = await ctx.api.get(`/api/jobs/${latest.id}`, null, { signal: ctx.signal });
        for (const r of (full.state && full.state.rows) || []) applyResult(r);
        if (ACTIVE.has(full.status)) {
          const queue = (full.state && full.state.queue) || [];
          for (const q of queue) {
            const id = Number(q.receipt_id);
            if (!S.results.has(id)) S.edits.set(id, { carrier_name: q.carrier_name, tracking_code: q.tracking_code });
          }
          attachJob(full, queue.map((q) => Number(q.receipt_id)));
        }
        else if (full.status === "done" && full.result) S.lastDone = full.result;
        renderBanner();
        updateSubtitle();
        if (S.data) renderRows();
      } catch (err) {
        if (!ctx.api.isAbort(err) && !(err instanceof ctx.api.ApiError)) console.warn("[orders] jobs", err);
      }
    }

    async function send() {
      if (S.job) return;
      const ids = readyIds();
      if (!ids.length) return;
      const list = ids.map((id) => ({
        receipt_id: id,
        carrier_name: canonicalCarrier(carrierFor(id)),
        tracking_code: trackingFor(id),
        note_to_buyer: (S.edits.get(id) || {}).note_to_buyer || undefined,
      }));
      const preview = h(
        "div",
        { class: "o-confirm-list" },
        list.slice(0, CONFIRM_PREVIEW).map((item) => {
          const row = S.known.get(item.receipt_id);
          return h(
            "div",
            { class: "o-confirm-row" },
            h(
              "div",
              { class: "o-cell" },
              h("span", { class: "o-no num" }, `#${item.receipt_id}`),
              h("span", { class: "o-sub ellipsis" }, row ? row.buyer : t("confirm.not_loaded")),
            ),
            carrierPill(item.carrier_name === OTHER ? t("carrier.other") : item.carrier_name),
            h("span", { class: "mono ellipsis o-confirm-track", title: item.tracking_code }, item.tracking_code),
          );
        }),
        list.length > CONFIRM_PREVIEW ? h("p", { class: "o-confirm-more" }, t("confirm.more", { n: list.length - CONFIRM_PREVIEW })) : null,
      );
      const restricted = S.summary && S.summary.tracking_restricted;
      const ok = await confirmSend(list.length, [
        h("p", { class: "modal-msg" }, t("confirm.msg", { n: list.length })),
        preview,
        restricted ? infoNote({ tone: "warning", icon: "alert", text: t("confirm.restricted") }) : null,
      ]);
      if (!ok || S.job) return;
      sendBtn.setLoading(true);
      // Old answers go now: the new job's first rows may arrive before the POST's answer.
      for (const id of ids) S.results.delete(id);
      try {
        const job = await ctx.api.post("/api/orders/ship", {
          country: (S.carriers && S.carriers.country) || undefined,
          rows: list,
        });
        if (!S.finished.has(job.id)) {
          S.lastDone = null;
          renderBanner();
          attachJob(job, ids);
        }
      } catch (err) {
        sendBtn.setLoading(false);
        if (err.code === "invalid" && err.params && Array.isArray(err.params.rows)) {
          for (const p of err.params.rows) {
            if (p.receipt_id) S.results.set(p.receipt_id, { receipt_id: p.receipt_id, status: "error", code: "invalid", message: p.message });
          }
          renderRows();
        }
        ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t), timeout: 8000 });
        syncSend();
      }
    }

    /** ctx.confirm, wide enough for the list of orders and tracking numbers. */
    function confirmSend(n, body) {
      return new Promise((resolve) => {
        let ok = false;
        ctx.modal({
          title: t("confirm.title"),
          body,
          width: 540,
          class: "o-confirm",
          actions: [
            { label: t("common.cancel"), variant: "secondary", onClick: ({ close }) => close() },
            {
              label: t("confirm.ok", { n }),
              variant: "primary",
              icon: "send",
              onClick: ({ close }) => {
                ok = true;
                close();
              },
            },
          ],
          onClose: () => resolve(ok),
        });
      });
    }

    async function stopJob() {
      if (!S.job) return;
      try {
        await ctx.api.post(`/api/jobs/${S.job.id}/cancel`, {});
      } catch (err) {
        ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t) });
      }
    }

    // --------------------------------------------------------------- CSV

    function openCsvMenu() {
      menu(
        csvBtn,
        [
          { label: t("csv.export"), icon: "download", onClick: () => exportCsv() },
          { label: t("csv.import"), icon: "upload", onClick: () => fileInput.click(), disabled: !!S.job },
          { divider: true },
          { label: t("menu.refresh"), icon: "refresh", onClick: () => refreshAll() },
          { label: t("menu.etsy"), icon: "external", onClick: () => window.open(SOLD_URL, "_blank", "noopener") },
        ],
        { placement: "bottom-end", width: 270 },
      );
    }

    function refreshAll() {
      load({ fresh: true });
      loadSummary(true);
    }

    async function exportCsv() {
      const edits = {};
      for (const [id, e] of S.edits) {
        if (!e || !(e.tracking_code || "").trim()) continue;
        edits[id] = {
          tracking_code: e.tracking_code.trim(),
          carrier_name: canonicalCarrier(e.carrier_name || S.defaultCarrier),
          note_to_buyer: e.note_to_buyer || "",
        };
      }
      try {
        const text = await ctx.api.post("/api/orders/export.csv", { tab: S.tab, edits }, { signal: ctx.signal });
        const blob = new Blob(["﻿", typeof text === "string" ? text : ""], { type: "text/csv;charset=utf-8" });
        const href = URL.createObjectURL(blob);
        const a = h("a", { href, download: `${t("csv.filename")}-${S.tab}-${today()}.csv`, hidden: true });
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(href), 2000);
        ctx.toast({ tone: "success", title: t("csv.exported") });
      } catch (err) {
        if (!ctx.api.isAbort(err)) ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t) });
      }
    }

    async function importCsv(file) {
      let res;
      try {
        res = await ctx.api.upload("/api/orders/import-tracking", file, { method: "POST", signal: ctx.signal });
      } catch (err) {
        if (!ctx.api.isAbort(err)) ctx.toast({ tone: "danger", title: ctx.api.errorText(err, t), timeout: 8000 });
        return;
      }
      if (!S.waiting) {
        // Not on the waiting list yet: read which orders wait, so no shipped one gets a number.
        try {
          const waiting = await ctx.api.get("/api/orders", { tab: "unshipped", per_page: S.perPage }, { signal: ctx.signal });
          if (Array.isArray(waiting.ids)) S.waiting = waiting.ids;
        } catch (err) {
          if (ctx.api.isAbort(err)) return;
        }
      }
      let unknown = 0;
      let notWaiting = 0;
      const filled = [];
      for (const r of res.rows || []) {
        const carrier = r.carrier_name ? canonicalCarrier(r.carrier_name) : "";
        const row = S.known.get(r.receipt_id);
        // Only orders that still wait for shipment (when the waiting list is known).
        if ((S.waiting && !S.waiting.includes(r.receipt_id)) || (row && !isEditable(row))) {
          notWaiting += 1;
          continue;
        }
        if (carrier && !carrierKnown(carrier)) unknown += 1;
        S.edits.set(r.receipt_id, { tracking_code: r.tracking_code, carrier_name: carrier, note_to_buyer: r.note_to_buyer || "" });
        S.results.delete(r.receipt_id);
        S.selected.add(r.receipt_id);
        filled.push(r.receipt_id);
      }
      S.selected = new Set(S.selected);
      const errors = res.errors || [];
      const extra = [
        errors.length ? t("csv.import_errors", { n: errors.length, line: errors[0].line }) : "",
        unknown ? t("csv.import_unknown_carrier", { n: unknown }) : "",
        notWaiting ? t("csv.import_not_waiting", { n: notWaiting }) : "",
      ].filter(Boolean);
      if (!filled.length) {
        ctx.toast({ tone: "warning", title: t("csv.import_empty"), message: extra.join(" ") || null, timeout: 8000 });
        return;
      }
      ctx.toast({
        tone: extra.length ? "warning" : "success",
        title: t("csv.imported", { n: filled.length }),
        message: [t("csv.imported_msg"), ...extra].join(" "),
        timeout: 8000,
      });
      // Show the waiting list at the page of the first number filled in.
      const at = S.waiting ? S.waiting.indexOf(filled[0]) : -1;
      const page = at >= 0 ? Math.floor(at / S.perPage) + 1 : 1;
      if (S.tab !== "unshipped" || S.q || page !== S.page) {
        S.tab = "unshipped";
        S.q = "";
        S.page = page;
        search.value = "";
        ctx.setQuery({ tab: null, q: null, page: page > 1 ? page : null });
        tabsC.update(tabItems(), "unshipped");
        syncCountry();
        load();
      } else renderRows();
    }

    // --------------------------------------------------------------- start

    const onResize = () => {
      const n = pageSize();
      if (n === S.perPage) return;
      S.perPage = n;
      S.page = 1;
      load();
    };
    let resizeTimer = null;
    const resized = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(onResize, 250);
    };
    window.addEventListener("resize", resized);

    await Promise.all([load(), loadSummary(), loadCarriers(), resumeJob()]);

    return () => {
      window.removeEventListener("resize", resized);
      clearTimeout(resizeTimer);
    };
  },
};
