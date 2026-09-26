// Placeholder page: replaced by the page owner (see SPEC section 9).
import { emptyState } from "../ui.js";

export default {
  async mount(el, ctx) {
    ctx.setHeader({ title: ctx.t("title"), subtitle: ctx.t("subtitle") });
    el.append(
      emptyState({ icon: "clock", title: ctx.t("placeholder.title"), message: ctx.t("placeholder.message") }),
    );
    return () => {};
  },
};
