// Şablon İlan (/kurulum/sablon): the listing every new draft copies its business
// settings from. Left: the shop's active listings (cached by the server, filtered and
// paged here). Right: the seven fields that would be copied, with their names resolved.
// Saving writes product.json in the products folder; nothing on Etsy changes.

import { badge, button, card, cx, h, infoNote, mount, searchInput, thumb } from "../ui.js";
import { icon } from "../icons.js";
import { lower, money, monthName, number, relative } from "../format.js";

// Until "N ilan daha göster" is pressed the list shows only the rows that fit whole in its
// card (the video's five, then empty room, then the link): at most FIRST_PAGE, at least
// MIN_FIT. Each press adds MORE_PAGE and the list scrolls.
const FIRST_PAGE = 20;
const MIN_FIT = 3;
const MORE_PAGE = 20;
const FIELDS = ["price", "shipping", "category", "who_made", "when_made", "processing", "returns"];
const FIELD_ICON = {
  price: "coins",
  shipping: "truck",
  category: "grid",
  who_made: "user",
  when_made: "loader",
  processing: "clock",
  returns: "refresh",
};

function fieldIcon(key) {
  return icon(FIELD_ICON[key], { size: 15 });
}

/** Etsy's category names as i18n keys: "Home & Living" -> home_living, "T-shirts" -> t_shirts. */
function catSlug(name) {
  return String(name || "")
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}
const STATE_TONE = { active: "success", draft: "muted", inactive: "muted", sold_out: "warning", expired: "warning" };
// Etsy's listing types, and what the drafts made from such a template are.
const TYPE_ICON = { physical: "box", download: "download", both: "package" };
// A pasted Etsy listing link, or a bare listing number (the old window's "listing number" field).
const LISTING_URL = /etsy\.com\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?listing\/(\d{5,13})/i;
const LISTING_NUMBER = /^#?(\d{5,13})$/;

export default {
  async mount(el, ctx) {
    const t = ctx.t;
    const st = {
      items: null, // null while loading
      count: 0,
      truncated: false,
      currency: null,
      listError: null,
      setupStep: null, // "keys" | "connect" when the shop is not set up
      query: "",
      expanded: false, // "daha göster" pressed: show `limit` rows and scroll
      limit: FIRST_PAGE,
      fit: null, // how many whole rows fit in the list before that (measured)
      sales: null, // {months: ["YYYY-MM"]} when the rows carry units sold
      current: undefined, // undefined while loading, null when there is none
      currentProblem: null,
      selectedId: null,
      pinnedId: null, // the saved template's listing when the list was (re)loaded
      pinnedRow: null, // its row, for a template that is not an active listing (a draft)
      previews: new Map(), // listing id -> summary (the saved template is kept here too)
      extras: new Map(), // listings reached by number that are not in the active list
      previewError: null,
      previewLoading: false,
      saving: false,
    };
    let previewSeq = 0;

    // ------------------------------------------------------------------ layout

    const refreshBtn = button({
      label: t("refresh"),
      icon: "refresh",
      variant: "secondary",
      autoLoading: true,
      onClick: () => loadListings(true),
    });
    ctx.setHeader({ actions: [refreshBtn] });

    const countSlot = h("div", { class: "tpl-count" });
    const search = searchInput({
      placeholder: t("search"),
      shortcut: "/",
      ariaLabel: t("search_aria"),
      onInput: (v) => {
        st.query = v.trim();
        st.expanded = false;
        renderList();
      },
      onEnter: () => {
        const id = manualId(st.query);
        if (id) select(id);
      },
    });
    const listEl = h("div", { class: "tpl-list", role: "radiogroup", "aria-label": t("list.title") });
    const moreEl = h("div", { class: "tpl-more" });
    const leftCard = card({
      title: t("list.title"),
      subtitle: t("list.subtitle"),
      actions: countSlot,
      class: "tpl-card tpl-listings",
      body: [search, listEl, moreEl],
    });

    const sourceEl = h("span", { class: "tpl-source" });
    const badgesEl = h("div", { class: "tpl-badges" });
    const fieldsEl = h("div", { class: "tpl-fields-list" });
    const notesEl = h("div", { class: "tpl-notes" });
    const hintEl = h("div", { class: "tpl-hint" });
    // Always "Şablon olarak kaydet", as in the video; the last save is in its tooltip.
    const saveBtn = button({ label: t("save"), icon: "file", variant: "primary", size: "lg", onClick: save });
    // Rows revealed one by one after a pick (the video): the id they were last played for.
    let revealedId = null;
    const rightCard = card({
      title: t("fields.title"),
      subtitle: sourceEl,
      actions: badgesEl,
      class: "tpl-card tpl-fields",
      body: [
        fieldsEl,
        h(
          "div",
          { class: "tpl-bottom" },
          notesEl,
          infoNote({ icon: "info", tone: "info", text: [h("b", null, t("note.bold")), " ", t("note.text")] }),
          h("div", { class: "tpl-actions" }, hintEl, saveBtn),
        ),
      ],
    });
    const grid = h("div", { class: "tpl-grid" }, leftCard, rightCard);
    el.append(grid);

    // The cards fill the visible height under the top bar (and under the app's warning
    // banner when one shows), so the list scrolls inside its card like in the video.
    const content = el.closest(".content");
    function fit() {
      if (!content || !grid.isConnected) return;
      const cs = getComputedStyle(content);
      const padTop = parseFloat(cs.paddingTop) || 0;
      const padBottom = parseFloat(cs.paddingBottom) || 0;
      const offset = grid.getBoundingClientRect().top - content.getBoundingClientRect().top + content.scrollTop - padTop;
      const avail = content.clientHeight - padTop - padBottom - offset;
      grid.style.setProperty("--tpl-fill", `${Math.max(0, Math.floor(avail))}px`);
    }
    const resizer = typeof ResizeObserver === "function" ? new ResizeObserver(() => fit()) : null;
    if (resizer && content) {
      resizer.observe(content);
      const banner = content.querySelector(".banner-slot");
      if (banner) resizer.observe(banner);
    }
    fit();
    // The list's own height decides how many whole rows it shows (it follows the window
    // and the fields card); the rows are only re-rendered when that number changes.
    const listSizer = typeof ResizeObserver === "function" ? new ResizeObserver(() => refit()) : null;
    if (listSizer) listSizer.observe(listEl);

    // ------------------------------------------------------------------ helpers

    function findItem(id) {
      return (st.items || []).find((it) => it.listing_id === id) || null;
    }

    function manualId(q) {
      const m = LISTING_URL.exec(q) || LISTING_NUMBER.exec((q || "").trim());
      return m ? Number(m[1]) : null;
    }

    function isCurrent(id) {
      return !!st.current && st.current.listing_id === id;
    }

    function shopCurrency() {
      const s = ctx.status();
      return st.currency || (s && s.shop && s.shop.currency) || "USD";
    }

    function tOr(key, fallback, params) {
      return t.has(key) ? t(key, params) : fallback;
    }

    function country(iso) {
      if (!iso) return "";
      const key = `country.${String(iso).toUpperCase()}`;
      if (t.has(key)) return t(key);
      try {
        return new Intl.DisplayNames([ctx.lang === "en" ? "en-US" : "tr-TR"], { type: "region" }).of(String(iso).toUpperCase()) || iso;
      } catch {
        return iso;
      }
    }

    function stateEl(state) {
      const s = state || "active";
      return h(
        "span",
        { class: cx("tpl-state", `tone-${STATE_TONE[s] || "muted"}`) },
        h("span", { class: "dot", "aria-hidden": "true" }),
        tOr(`state.${s}`, s),
      );
    }

    function listingType(value) {
      return value === "download" || value === "both" ? value : "physical";
    }

    /**
     * "Fiziksel" / "Dijital" / "Fiziksel + dijital" at the head of the source line: the
     * drafts keep the template's type. (The card's title row has no room left for it.)
     */
    function typeTag(value) {
      const kind = listingType(value);
      return h(
        "span",
        { class: cx("tpl-type", `is-${kind}`), title: t(`type.${kind}_hint`) },
        icon(TYPE_ICON[kind], { size: 12 }),
        h("span", null, t(`type.${kind}`)),
      );
    }

    function dotSep() {
      return h("span", { class: "tpl-sep", "aria-hidden": "true" }, "·");
    }

    function errorNote(err, retry) {
      const code = err && err.code;
      const toConnect = code === "reconnect" || code === "bad_keys" || code === "setup_needed";
      const action = toConnect
        ? button({ label: t("setup.action"), size: "sm", iconRight: "arrow-right", onClick: () => ctx.navigate("/kurulum/magaza") })
        : button({ label: t("common.retry"), size: "sm", icon: "refresh", autoLoading: true, onClick: retry });
      return infoNote({ icon: "alert", tone: "danger", text: ctx.api.errorText(err, t), action });
    }

    // ------------------------------------------------------------------ left: listings

    // Rows the list can show: the saved template first (as it was when the list loaded, so
    // a row does not jump away right after saving it), then listings reached by number,
    // then the shop's active listings in Etsy's order.
    function allRows() {
      const rows = [];
      const seen = new Set();
      const add = (row) => {
        if (!row || seen.has(row.listing_id)) return;
        seen.add(row.listing_id);
        rows.push(row);
      };
      if (st.pinnedId !== null) add(findItem(st.pinnedId) || st.extras.get(st.pinnedId) || st.pinnedRow);
      for (const row of st.extras.values()) add(row);
      for (const row of st.items || []) add(row);
      return rows;
    }

    function rowFromSummary(s) {
      const price = (s.fields || []).find((f) => f.key === "price");
      return {
        listing_id: s.listing_id,
        title: s.title || t("listing_number", { id: s.listing_id }),
        price: price && price.value ? price.value.amount : null,
        currency: s.currency,
        thumb_url: s.thumb_url,
        state: s.state,
        num_favorers: null,
        sold: null,
        product_type: null,
        product_type_key: null,
        listing_type: s.listing_type,
      };
    }

    /** "Tişört" for Etsy's "T-shirts" when the server knows the product; else Etsy's name. */
    function productType(row) {
      const key = row.product_type_key ? `product.${row.product_type_key}` : null;
      return key && t.has(key) ? t(key) : row.product_type;
    }

    function matches(row, q) {
      if (!q) return true;
      if (manualId(q) === row.listing_id) return true;
      if (String(row.listing_id).startsWith(q.replace(/^#/, ""))) return true;
      return lower(row.title).includes(lower(q));
    }

    function listRow(row) {
      const id = row.listing_id;
      const selected = id === st.selectedId;
      const input = h("input", {
        type: "radio",
        name: "tpl-listing",
        class: "tpl-radio-input",
        value: String(id),
        checked: selected,
        onChange: () => select(id),
      });
      const sub = [];
      const kind = listingType(row.listing_type);
      if (kind !== "physical") sub.push(h("span", { class: "tpl-row-type" }, icon(TYPE_ICON[kind], { size: 12 }), t(`type.${kind}_short`)));
      const product = productType(row);
      if (product) sub.push(h("span", null, product));
      // Units sold (Kâr-Zarar's months on disk) as in the video; the favourites without them.
      if (row.sold !== null && row.sold !== undefined) {
        sub.push(h("span", { class: "tpl-sold", title: salesHint() }, t("sales", { n: row.sold, count: number(row.sold) })));
      } else if (row.num_favorers !== null && row.num_favorers !== undefined) {
        sub.push(h("span", null, t("favorites", { n: row.num_favorers, count: number(row.num_favorers) })));
      }
      if (row.state) sub.push(stateEl(row.state));
      const parts = [];
      sub.forEach((node, i) => {
        if (i) parts.push(dotSep());
        parts.push(node);
      });
      // The saved template has no mark of its own (the video has none): it is pinned first
      // and picked at load; its tooltip and the screen reader say which one it is.
      const current = isCurrent(id);
      return h(
        "label",
        { class: cx("tpl-row", selected && "is-selected"), dataset: { id: String(id) }, title: current ? t("current") : undefined },
        input,
        h("span", { class: "tpl-radio", "aria-hidden": "true" }),
        thumb({ src: row.thumb_url, size: 64, radius: 9, icon: "image" }),
        h(
          "span",
          { class: "tpl-row-main" },
          h(
            "span",
            { class: "tpl-row-head" },
            current ? h("span", { class: "sr-only" }, `${t("current")}: `) : null,
            h("span", { class: "tpl-row-title ellipsis", title: current ? `${t("current")}: ${row.title}` : row.title }, row.title),
          ),
          h("span", { class: "tpl-row-sub" }, parts),
        ),
        h("span", { class: "tpl-row-price mono" }, row.price === null || row.price === undefined ? "–" : money(row.price, row.currency || shopCurrency())),
      );
    }

    function skeletonRows(n) {
      const out = [];
      for (let i = 0; i < n; i += 1) {
        out.push(
          h(
            "div",
            { class: "tpl-row is-skeleton", "aria-hidden": "true" },
            h("span", { class: "tpl-radio" }),
            h("span", { class: "skeleton tpl-skel-thumb" }),
            h(
              "span",
              { class: "tpl-row-main" },
              h("span", { class: "skeleton", style: { height: 13, width: `${70 - i * 6}%` } }),
              h("span", { class: "skeleton", style: { height: 10, width: "34%" } }),
            ),
            h("span", { class: "skeleton", style: { height: 14, width: 52 } }),
          ),
        );
      }
      return out;
    }

    /** "Nis 2026 – Eyl 2026": the months the units sold were counted over. */
    function salesHint() {
      const months = (st.sales && st.sales.months) || [];
      if (!months.length) return "";
      const label = (ym) => {
        const [y, m] = String(ym).split("-").map(Number);
        return y && m ? `${monthName(m - 1, true)} ${y}` : String(ym);
      };
      const first = label(months[0]);
      const last = label(months[months.length - 1]);
      return t("sales_hint", { n: months.length, range: first === last ? first : `${first} – ${last}` });
    }

    /** Rows shown before "daha göster" is pressed: what fits whole, else FIRST_PAGE. */
    function rowLimit() {
      return st.expanded ? st.limit : st.fit || FIRST_PAGE;
    }

    /**
     * How many whole rows fit in the list's box: the rows as rendered, in order, and more
     * of the last one's height when every rendered row fits. null before it is laid out.
     */
    function measureFit() {
      if (!listEl.isConnected || !listEl.clientHeight) return null;
      const rowsEls = [...listEl.children].filter((node) => node.matches(".tpl-row[data-id]"));
      if (!rowsEls.length) return null;
      const cs = getComputedStyle(listEl);
      const gap = parseFloat(cs.rowGap) || 0;
      let avail = listEl.clientHeight - (parseFloat(cs.paddingTop) || 0) - (parseFloat(cs.paddingBottom) || 0);
      // Anything above the rows (the "use listing #id" button) takes its room first.
      for (const node of listEl.children) {
        if (node === rowsEls[0]) break;
        avail -= node.getBoundingClientRect().height + gap;
      }
      let used = 0;
      let n = 0;
      let last = 0;
      for (const rowEl of rowsEls) {
        const height = rowEl.getBoundingClientRect().height;
        const next = used + (n ? gap : 0) + height;
        if (next > avail + 0.5) return Math.max(MIN_FIT, n);
        used = next;
        n += 1;
        last = height;
      }
      return Math.max(MIN_FIT, n + Math.max(0, Math.floor((avail - used) / (last + gap))));
    }

    /** The list's height changed: re-render only when a different number of rows fits. */
    function refit() {
      if (st.expanded || !st.items || st.setupStep) return;
      const fitted = measureFit();
      if (fitted !== null && fitted !== st.fit) {
        st.fit = fitted;
        renderList();
      }
    }

    function renderCount() {
      mount(
        countSlot,
        st.items ? badge({ icon: "list", text: t("count", { n: st.count, count: number(st.count) }), tone: "neutral" }) : null,
      );
    }

    function renderList(again = false) {
      renderCount();
      search.hidden = !!st.setupStep;
      refreshBtn.setDisabled(!!st.setupStep);
      if (st.setupStep) {
        mount(
          listEl,
          h(
            "div",
            { class: "tpl-setup" },
            h("span", { class: "empty-icon" }, icon("link", { size: 22 })),
            h("p", { class: "empty-title" }, t("setup.title")),
            h("p", { class: "empty-msg" }, t(`setup.${st.setupStep === "connect" ? "connect" : "keys"}`)),
            button({ label: t("setup.action"), variant: "primary", iconRight: "arrow-right", onClick: () => ctx.navigate("/kurulum/magaza") }),
          ),
        );
        mount(moreEl);
        return;
      }
      if (st.listError && !st.items) {
        mount(listEl, errorNote(st.listError, () => loadListings(false)));
        mount(moreEl);
        return;
      }
      if (!st.items) {
        mount(listEl, skeletonRows(5));
        mount(moreEl);
        return;
      }

      const q = st.query;
      const rows = allRows().filter((row) => matches(row, q));
      const shown = rows.slice(0, rowLimit());
      const nodes = [];
      const wanted = manualId(q);
      if (wanted && !rows.some((row) => row.listing_id === wanted)) {
        nodes.push(
          h(
            "button",
            { type: "button", class: "tpl-byid", onClick: () => select(wanted) },
            h("span", { class: "tpl-byid-icon" }, icon("search", { size: 15 })),
            h("span", { class: "tpl-byid-text" }, h("b", null, t("by_id.title", { id: wanted })), h("span", null, t("by_id.sub"))),
            icon("arrow-right", { size: 15 }),
          ),
        );
      }
      const rowNodes = shown.map(listRow);
      nodes.push(...rowNodes);
      if (!rows.length && !nodes.length) {
        nodes.push(
          h(
            "div",
            { class: "tpl-empty" },
            h("p", { class: "empty-title" }, q ? t("empty.search_title") : t("empty.title")),
            h("p", { class: "empty-msg" }, q ? t("empty.search_msg") : t("empty.msg")),
          ),
        );
      }
      mount(listEl, nodes);
      let count = shown.length;
      if (!st.expanded) {
        // Whole rows only: measured on the rows just rendered, then shown once more when
        // the number differs. Rows of different heights could disagree between the two
        // passes: then whatever does not fit whole is dropped instead of rendered again.
        const fitted = measureFit();
        if (fitted !== null && fitted !== st.fit) {
          st.fit = fitted;
          if (!again && Math.min(rows.length, fitted) !== count) {
            renderList(true);
            return;
          }
        }
        if (fitted !== null && fitted < count) {
          for (const node of rowNodes.slice(fitted)) node.remove();
          count = fitted;
        }
      }

      const left = rows.length - count;
      const hidden = !q && st.truncated ? Math.max(0, st.count - (st.items || []).length) : 0;
      mount(
        moreEl,
        left > 0
          ? h(
              "button",
              {
                type: "button",
                class: "tpl-more-btn",
                onClick: () => {
                  st.limit = count + MORE_PAGE;
                  st.expanded = true;
                  renderList();
                },
              },
              t("more", { n: left + hidden, count: number(left + hidden) }),
              icon("arrow-down", { size: 14 }),
            )
          : hidden > 0
            ? h("p", { class: "tpl-more-note" }, t("truncated", { n: (st.items || []).length }))
            : null,
      );
    }

    // Selection changes only toggle classes, so keyboard focus stays on the radio.
    function renderSelection() {
      for (const rowEl of listEl.querySelectorAll(".tpl-row[data-id]")) {
        const on = Number(rowEl.dataset.id) === st.selectedId;
        rowEl.classList.toggle("is-selected", on);
        const input = rowEl.querySelector("input");
        if (input && input.checked !== on) input.checked = on;
      }
    }

    // ------------------------------------------------------------------ right: fields

    function describe(f) {
      const v = f.value || {};
      switch (f.key) {
        case "price":
          return { text: money(v.amount, v.currency || shopCurrency()) };
        case "shipping":
          if (v.digital) return { text: t("shipping.digital") };
          if (v.title) return { text: v.origin_country ? `${v.title} · ${country(v.origin_country)}` : v.title };
          return { text: t("shipping.id", { id: v.id }) };
        case "category":
          if (v.path && v.path.length) return { text: categoryText(v.path), title: v.path.join(" › ") };
          return { text: t("category.id", { id: v.id }) };
        case "who_made":
          return { code: v.code, text: tOr(`who.${v.code}`, v.code) };
        case "when_made":
          return { code: v.code, text: whenText(v.code) };
        case "processing":
          return { text: processingText(v) };
        case "returns":
          return { text: returnsText(v) };
        default:
          return { text: "" };
      }
    }

    /**
     * Etsy's root and leaf ("Giyim › Tişörtler", the video's), in the UI's language by the
     * exact Etsy name. When either has no string, both stay in Etsy's English rather than
     * a Turkish-English mix. The full path is the tooltip.
     */
    function categoryText(path) {
      const root = String(path[0]);
      const leaf = String(path[path.length - 1]);
      const rootKey = `category_root.${catSlug(root)}`;
      if (path.length === 1 || root === leaf) return t.has(rootKey) ? t(rootKey) : root;
      const leafKey = `category_leaf.${catSlug(leaf)}`;
      if (t.has(rootKey) && t.has(leafKey)) return `${t(rootKey)} › ${t(leafKey)}`;
      return `${root} › ${leaf}`;
    }

    function whenText(code) {
      if (!code) return "";
      if (t.has(`when.${code}`)) return t(`when.${code}`);
      let m = /^(\d{4})_(\d{4})$/.exec(code);
      if (m) return `${m[1]}–${m[2]}`;
      m = /^before_(\d{4})$/.exec(code);
      if (m) return t("when.before", { year: m[1] });
      m = /^(\d{4})s$/.exec(code);
      if (m) return t("when.decade", { decade: m[1] });
      return code;
    }

    function processingText(v) {
      if (v.digital && v.readiness_state_id === null && v.min === null && v.max === null) return t("processing.digital");
      if (v.min !== null || v.max !== null) {
        const lo = v.min ?? v.max;
        const hi = v.max ?? v.min;
        return lo === hi ? t("processing.days", { n: lo }) : t("processing.range", { min: lo, max: hi });
      }
      if (v.label) return v.label;
      return t("processing.id", { id: v.readiness_state_id });
    }

    function returnsText(v) {
      if (v.accepts_returns === null || v.accepts_returns === undefined) return t("returns.id", { id: v.id });
      if (v.accepts_returns) return v.deadline ? t("returns.days", { n: v.deadline }) : t("returns.yes");
      return v.accepts_exchanges ? t("returns.exchanges") : t("returns.none");
    }

    function fieldRow(key, f, mode, reveal = false) {
      // mode: "value" | "loading" | "blank"; reveal: the values fade in over grey bars
      const label = h("span", { class: "tpl-field-label" }, t(`field.${key}`));
      let value = null;
      let mark = null;
      let why = null;
      if (mode === "loading") {
        value = h("span", { class: "skeleton", style: { height: 13, width: 96 } });
        mark = h("span", { class: "tpl-check is-idle", "aria-hidden": "true" });
      } else if (mode === "blank") {
        // Nothing picked yet: a grey bar where the value will come (the video's first state).
        value = h("span", { class: "tpl-bar", "aria-hidden": "true" });
        mark = h("span", { class: "tpl-check is-idle", "aria-hidden": "true" });
      } else if (f.ok) {
        const d = describe(f);
        value = [
          // The video keeps each row's grey bar until its value has faded in over it.
          reveal ? h("span", { class: "tpl-bar tpl-bar-out", "aria-hidden": "true" }) : null,
          d.code ? h("code", { class: "tpl-code" }, d.code) : null,
          h("span", { class: cx("tpl-value ellipsis", key === "price" && "mono"), title: d.title || d.text }, d.text),
        ];
        mark = h("span", { class: "tpl-check", title: t("copied"), role: "img", "aria-label": t("copied") }, icon("check", { size: 12, strokeWidth: 2.6 }));
      } else {
        why = h("span", { class: cx("tpl-field-why", !f.required && "is-optional") }, t(`why.${key}`));
        mark = badge({ text: t("missing"), tone: "warning", size: "sm", icon: "alert" });
      }
      const missing = mode === "value" && !f.ok;
      return h(
        "div",
        {
          class: cx("tpl-field", missing && (f.required ? "is-missing" : "is-optional"), mode !== "value" && "is-idle"),
        },
        h("span", { class: "tpl-field-icon" }, fieldIcon(key)),
        h("span", { class: "tpl-field-text" }, label, why),
        h("span", { class: "tpl-field-value" }, value),
        mark,
      );
    }

    function renderRight() {
      const id = st.selectedId;
      const p = id !== null ? st.previews.get(id) : null;
      const current = p && isCurrent(p.listing_id);
      const known = p || (id !== null ? findItem(id) || st.extras.get(id) : null);

      // The type goes first only when it is not the usual physical one (digital, both).
      const shownType = p ? p.listing_type : known && known.listing_type;
      if (id === null) {
        mount(sourceEl, h("span", { class: "tpl-source-none" }, t("source_none")));
      } else {
        mount(
          sourceEl,
          shownType && listingType(shownType) !== "physical" ? typeTag(shownType) : null,
          h("span", { class: "tpl-source-label" }, t("source")),
          " ",
          known && known.title
            ? h("span", { class: "tpl-source-title", title: known.title }, known.title)
            : h("span", { class: "tpl-source-none" }, t("listing_number", { id })),
        );
      }

      mount(
        badgesEl,
        !p
          ? badge({ text: t("fields.count", { n: FIELDS.length }), tone: "neutral" })
          : p.ok_count === p.total
            ? badge({ icon: "check", text: t("fields.count", { n: p.total }), tone: "success" })
            : badge({ icon: "alert", text: t("fields.partial", { ok: p.ok_count, total: p.total }), tone: "warning" }),
      );

      if (p) {
        // Values fade in row by row the first time a listing's fields are shown after a pick;
        // not again for the same listing, and not for the saved template shown at load.
        const reveal = p.listing_id !== revealedId;
        revealedId = p.listing_id;
        mount(
          fieldsEl,
          p.fields.map((f, i) => {
            const row = fieldRow(f.key, f, "value", reveal);
            row.style.setProperty("--i", String(i));
            return row;
          }),
        );
        fieldsEl.classList.toggle("is-reveal", reveal);
      } else if (st.previewError && id !== null) {
        fieldsEl.classList.remove("is-reveal");
        const err = st.previewError;
        mount(
          fieldsEl,
          err.code === "not_found"
            ? infoNote({ icon: "alert", tone: "warning", text: t("preview_not_found", { id }) })
            : errorNote(err, () => loadPreview(id)),
        );
      } else {
        fieldsEl.classList.remove("is-reveal");
        const mode = st.previewLoading || st.current === undefined ? "loading" : "blank";
        mount(fieldsEl, FIELDS.map((key) => fieldRow(key, null, mode)));
      }

      const notes = [];
      if (st.currentProblem === "malformed" && !p) {
        notes.push(infoNote({ icon: "alert", tone: "warning", text: t("malformed") }));
      }
      mount(notesEl, notes);

      // Left of the save button: the video's footnote, then what the page must still say.
      const hints = [h("span", { class: "tpl-hint-line tpl-footnote" }, t("footnote"))];
      if (p && p.fields.some((f) => !f.ok && f.required)) {
        hints.push(h("span", { class: "tpl-hint-line is-warning" }, icon("alert", { size: 13 }), h("span", null, t("missing_hint"))));
      }
      if (p && p.has_variations) {
        hints.push(h("span", { class: "tpl-hint-line is-strong" }, icon("layers", { size: 13 }), t("variations")));
      }
      const kind = p ? listingType(p.listing_type) : "physical";
      if (kind !== "physical") {
        hints.push(h("span", { class: "tpl-hint-line is-strong" }, icon("download", { size: 13 }), t(`type.${kind}_note`)));
      }
      mount(hintEl, hints);
      saveBtn.title = current && st.current.saved_at ? t("saved_ago", { when: relative(st.current.saved_at) }) : "";
      saveBtn.setDisabled(!p || !!st.setupStep);
      saveBtn.setLoading(st.saving);
    }

    // ------------------------------------------------------------------ data

    async function loadCurrent() {
      try {
        const r = await ctx.api.get("/api/template", null, { signal: ctx.signal });
        st.current = r.template || null;
        st.currentProblem = r.problem || null;
        if (st.current) {
          st.previews.set(st.current.listing_id, st.current);
          if (st.selectedId === null) {
            st.selectedId = st.current.listing_id;
            revealedId = st.current.listing_id; // shown at load: no row-by-row reveal
          }
          pinCurrent();
        }
      } catch (err) {
        if (ctx.api.isAbort(err)) return;
        st.current = null;
        ctx.toast({ tone: "danger", title: t("current_failed"), message: ctx.api.errorText(err, t) });
      }
      renderList();
      renderRight();
    }

    // What the last status check says is missing (null: go and ask the server).
    function setupStepFromStatus() {
      const s = ctx.status();
      if (!s) return null;
      if (s.state === "keys") return "keys";
      if (s.state === "disconnected") return "connect";
      return null;
    }

    async function loadListings(refresh) {
      const hadItems = !!st.items;
      st.listError = null;
      const known = refresh ? null : setupStepFromStatus();
      if (known) {
        st.setupStep = known;
        st.items = null;
        renderList();
        renderRight();
        return;
      }
      if (!hadItems) renderList();
      try {
        const r = await ctx.api.get("/api/template/listings", refresh ? { refresh: 1 } : null, { signal: ctx.signal });
        st.setupStep = null;
        st.items = r.items || [];
        st.count = r.count || st.items.length;
        st.truncated = !!r.truncated;
        st.currency = r.currency || null;
        st.sales = r.sales || null;
        if (refresh) {
          // Everything but the saved template may have changed on Etsy.
          for (const key of [...st.previews.keys()]) if (!isCurrent(key)) st.previews.delete(key);
          if (st.selectedId !== null && !st.previews.has(st.selectedId)) loadPreview(st.selectedId);
          pinCurrent();
        }
      } catch (err) {
        if (ctx.api.isAbort(err)) return;
        if (err.code === "setup_needed") {
          st.setupStep = (err.params && err.params.step) || "keys";
          st.items = null;
        } else if (hadItems) {
          ctx.toast({ tone: "danger", title: t("refresh_failed"), message: ctx.api.errorText(err, t) });
        } else {
          st.listError = err;
        }
      }
      renderList();
      renderRight();
    }

    async function loadPreview(id) {
      st.previewError = null;
      if (st.previews.has(id)) {
        st.previewLoading = false;
        renderRight();
        return;
      }
      const seq = ++previewSeq;
      st.previewLoading = true;
      renderRight();
      try {
        const p = await ctx.api.get(`/api/template/preview/${encodeURIComponent(id)}`, null, { signal: ctx.signal });
        if (seq !== previewSeq) return;
        st.previews.set(id, p);
        if (!findItem(id) && !isCurrent(id)) {
          st.extras.set(id, rowFromSummary(p));
          renderList();
        }
      } catch (err) {
        if (ctx.api.isAbort(err) || seq !== previewSeq) return;
        st.previewError = err;
      }
      st.previewLoading = false;
      renderRight();
    }

    function pinCurrent() {
      st.pinnedId = st.current ? st.current.listing_id : null;
      st.pinnedRow = st.current ? rowFromSummary(st.current) : null;
    }

    function select(id) {
      if (st.selectedId === id && (st.previews.has(id) || st.previewLoading)) return;
      st.selectedId = id;
      renderSelection();
      loadPreview(id);
    }

    async function save() {
      const id = st.selectedId;
      const p = id !== null ? st.previews.get(id) : null;
      if (!p || st.saving) return;
      if (st.current && st.current.listing_id !== id) {
        const ok = await ctx.confirm({
          title: t("replace.title"),
          message: t("replace.message", { old: st.current.title, next: p.title }),
          confirmLabel: t("replace.confirm"),
          icon: "file",
        });
        if (!ok || !ctx.isActive()) return;
      }
      st.saving = true;
      renderRight();
      try {
        const r = await ctx.api.post("/api/template", { listing_id: id }, { signal: ctx.signal });
        st.current = r.template;
        st.currentProblem = null;
        st.previews.set(id, r.template);
        ctx.toast({
          tone: "success",
          title: t("saved.title"),
          message: listingType(r.template && r.template.listing_type) === "download" ? t("saved.message_digital") : t("saved.message"),
          action: button({ label: t("saved.next"), size: "sm", iconRight: "arrow-right", onClick: () => ctx.navigate("/tasarim-yukle") }),
        });
      } catch (err) {
        if (ctx.api.isAbort(err)) return;
        ctx.toast({ tone: "danger", title: t("save_failed"), message: ctx.api.errorText(err, t) });
      } finally {
        st.saving = false;
      }
      if (!ctx.isActive()) return;
      renderList();
      renderRight();
    }

    // A shop connected in another tab (or the status check finishing) fills the list.
    ctx.onStatus((s, prev) => {
      const was = prev && prev.state;
      if (!s || s.state === was) return;
      if (s.state === "connected" && (st.setupStep || st.listError)) {
        loadListings(false);
      } else if (st.setupStep) {
        const step = setupStepFromStatus();
        if (step && step !== st.setupStep) {
          st.setupStep = step;
          renderList();
        }
      }
    });

    renderList();
    renderRight();
    await Promise.all([loadCurrent(), loadListings(false)]);
    if (st.selectedId !== null && !st.previews.has(st.selectedId) && !st.previewLoading) loadPreview(st.selectedId);
    return () => {
      if (resizer) resizer.disconnect();
      if (listSizer) listSizer.disconnect();
    };
  },
};
