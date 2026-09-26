"""The desktop window: every stallkit command behind a button, for people who do not
use a terminal.

Each action builds the same argument list you would type and hands it to
`runner.run_cli`. The window adds what a terminal cannot: forms instead of flags,
file pickers instead of paths, a confirmation dialog before anything reaches the live
shop, and a log that stays on screen.
"""

from __future__ import annotations

import gc
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

from .. import __version__
from . import i18n, runner, settings

# Etsy's own form for a seller's app for their own shop (the "Seller App" tier,
# July 2026): two fields, usually approved in minutes. The dashboard is where the
# keys are shown and the callback address is added once the app is approved.
ETSY_SELLER_APP_URL = "https://www.etsy.com/developers/register-seller-app"
ETSY_DASHBOARD_URL = "https://www.etsy.com/developers/"
# Required by Etsy's API Terms, prominently, in every application that uses the API.
TRADEMARK_NOTICE = (
    "The term 'Etsy' is a trademark of Etsy, Inc. "
    "This Application uses Etsy's API, but is not endorsed or certified by Etsy."
)
PINTEREST_APPS_URL = "https://developers.pinterest.com/apps/"
HELP_URL = "https://github.com/MoneyPrintLabs/etsyprinting#readme"

LISTING_STATES = ("active", "draft", "inactive", "expired", "sold_out")

# First character of a CLI output line -> log colour. The CLI marks every status
# line this way already, so the window colours it without parsing any wording.
_LINE_TAGS = (("✓", "ok"), ("✗", "err"), ("Error:", "err"), ("!", "warn"), ("→", "warn"))


def _open_path(path: Path) -> None:
    """Show a folder or file in the system's own file manager."""
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 — a local path the person chose
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _ids(text: str) -> list[str]:
    """Listing numbers typed with spaces, commas or new lines between them."""
    return [part for part in re.split(r"[\s,;]+", text.strip()) if part]


class App:
    """The main window. `root` is created by the caller so tests can own it."""

    def __init__(self, root: tk.Tk, *, language: str | None = None) -> None:
        self.root = root
        self.app_prefs = settings.load_app_prefs()
        self._open_saved_shop()
        self.prefs = settings.load_shop_prefs()
        self.lang = language or self.app_prefs.get("language") or i18n.detect_language()
        self.events: queue.Queue = queue.Queue()
        self.out = runner.LogStream(self.events, "out")
        self.err = runner.LogStream(self.events, "err")
        self.stdin = runner.PromptStream(self._ask_from_worker, self.out.question)
        self.worker = runner.Worker(self.events)
        self.busy = 0
        self.buttons: list[ttk.Button] = []
        # (state, shop name) of the open shop; state is "" until the first check.
        self.shop_state: tuple[str, str] = ("", "")
        # Bumped on every shop switch, so a status check that started for the
        # previous shop cannot paint its answer onto the new one.
        self._shop_generation = 0
        self._first_status = True
        self._cancel: threading.Event | None = None
        self.closing = False

        self.vars: dict[str, tk.Variable] = {}
        self._init_vars()

        root.title(f"stallkit {__version__}")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._scale = self._ui_scale()
        self._place_window()
        self._set_icon()
        self._style()

        self.container: ttk.Frame | None = None
        self.log_text: tk.Text | None = None
        self.log_buffer: list[tuple[str, str]] = []
        self.build()
        self._drain_after = self.root.after(40, self._drain)
        self.refresh_status()

    # ------------------------------------------------------------------ setup

    def t(self, key: str, /, **kwargs: object) -> str:
        return i18n.text(self.lang, key, **kwargs)

    def _open_saved_shop(self) -> None:
        """Reopen the shop that was open last time, if it still exists."""
        from .. import shops

        wanted = str(self.app_prefs.get("shop") or "")
        known = {shop.id for shop in shops.all_shops()}
        settings.use_shop(wanted if wanted in known else "")

    def _default_workspace(self) -> str:
        """The same per-shop folder the command line uses: see `default_root`."""
        from ..drop import workspace as workspace_mod

        return str(workspace_mod.default_root())

    def _init_vars(self) -> None:
        p = self.prefs
        text = {
            "keystring": settings.current("ETSY_KEYSTRING"),
            "secret": settings.current("ETSY_SHARED_SECRET"),
            "redirect": settings.current("ETSY_REDIRECT_URI", settings.ETSY_REDIRECT_DEFAULT),
            "workspace": p.get("workspace") or self._default_workspace(),
            "template_listing": p.get("template_listing", ""),
            "pull_state": "active",
            "push_csv": p.get("push_csv", ""),
            "inventory_from": p.get("inventory_from", ""),
            "since": "30d",
            "ship_csv": p.get("ship_csv", ""),
            "country": p.get("country", ""),
            "keyword": "",
            "suggest_listing": "",
            "suggest_keyword": "",
            "pin_app_id": settings.current("PINTEREST_APP_ID"),
            "pin_secret": settings.current("PINTEREST_APP_SECRET"),
            "pin_redirect": settings.current("PINTEREST_REDIRECT_URI", settings.PINTEREST_REDIRECT_DEFAULT),
            "pin_listings": "",
            "pin_board": p.get("pin_board", ""),
            "pin_images": "",
            "pin_per_day": "2",
        }
        flags = {
            "show_secret": False,
            "use_inventory": bool(p.get("inventory_from")),
            "unshipped": True,
            "pin_sandbox": settings.current("PINTEREST_SANDBOX").lower() in {"1", "true", "yes"},
            "pin_ai": True,
            "anonymise": bool(self.app_prefs.get("anonymise", False)),
            "show_pin_secret": False,
        }
        for key, value in text.items():
            self.vars[key] = tk.StringVar(master=self.root, value=value)
        for key, value in flags.items():
            self.vars[key] = tk.BooleanVar(master=self.root, value=value)

    def _ui_scale(self) -> float:
        """Tk sizes fonts by DPI but window geometry in raw pixels; match the two."""
        try:
            return max(1.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        except (tk.TclError, ValueError):
            return 1.0

    def _place_window(self) -> None:
        """Centre the window at a comfortable size that still fits the screen.

        A laptop at 125% scaling has barely 800 usable points of height, and a
        window taller than that hides its log behind the taskbar.
        """
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width = min(int(1080 * self._scale), int(screen_w * 0.92))
        height = min(int(780 * self._scale), int(screen_h * 0.85))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - int(20 * self._scale))
        self._window_height = height
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(int(860 * self._scale), width), min(int(560 * self._scale), height))

    def _set_icon(self) -> None:
        try:
            from PIL import ImageTk

            from .icon import render

            self._icon = ImageTk.PhotoImage(render(64), master=self.root)
            self.root.iconphoto(True, self._icon)
        except Exception:  # noqa: BLE001 — an icon is never worth failing to start
            pass

    def _style(self) -> None:
        from tkinter import font as tkfont

        style = ttk.Style(self.root)
        for theme in ("vista", "aqua", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        # Font(name=..., exists=True) rather than nametofont(root=...), which is 3.10+.
        base = tkfont.Font(root=self.root, name="TkDefaultFont", exists=True).actual()
        family, size = base["family"], abs(int(base["size"])) or 10
        self.bold = (family, size, "bold")
        style.configure("Title.TLabel", font=(family, size + 6, "bold"))
        style.configure("Hint.TLabel", foreground="#555555")
        style.configure("Status.TLabel", font=self.bold)
        style.configure("Primary.TButton", font=self.bold)

    # ------------------------------------------------------------------ layout

    def build(self) -> None:
        """(Re)build every widget in the current language. Field values live in
        `self.vars`, so switching language keeps what was typed."""
        self.buttons = []
        if self.container is not None:
            self.container.destroy()
        self.container = ttk.Frame(self.root, padding=(12, 10, 12, 10))
        self.container.pack(fill="both", expand=True)

        self._header(self.container)
        ttk.Label(self.container, text=TRADEMARK_NOTICE, style="Hint.TLabel").pack(side="bottom", anchor="w", pady=(6, 0))
        panes = ttk.PanedWindow(self.container, orient="vertical")
        panes.pack(fill="both", expand=True, pady=(8, 0))

        self.notebook = ttk.Notebook(panes)
        for builder in (
            self._tab_setup,
            self._tab_drop,
            self._tab_listings,
            self._tab_orders,
            self._tab_seo,
            self._tab_pinterest,
        ):
            builder(self.notebook)
        panes.add(self.notebook, weight=4)
        panes.add(self._log_panel(panes), weight=1)
        tab = self.app_prefs.get("tab", 0)
        self.notebook.select(tab if isinstance(tab, int) and 0 <= tab < len(self.notebook.tabs()) else 0)
        self.notebook.bind("<<NotebookTabChanged>>", self._remember_tab)
        self._install_edit_menu()
        self._render_status()
        self._set_busy_widgets()

    def _install_edit_menu(self) -> None:
        """Right-click Cut/Copy/Paste on every field, and Ctrl+A to select all.

        Tk entries have neither by default, and "right-click, Paste" is how most
        people put a key copied from a web page into a form.
        """
        if getattr(self, "_edit_menu_installed", False):
            return
        self._edit_menu_installed = True

        def popup(event: tk.Event) -> None:
            widget = event.widget
            menu = tk.Menu(self.root, tearoff=0)
            for label, action in (
                (self.t("cut"), "<<Cut>>"),
                (self.t("copy"), "<<Copy>>"),
                (self.t("paste"), "<<Paste>>"),
            ):
                menu.add_command(label=label, command=lambda a=action: widget.event_generate(a))
            menu.add_separator()
            menu.add_command(label=self.t("select_all"), command=lambda: select_all(widget))
            try:
                widget.focus_set()
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        def select_all(widget: tk.Widget) -> str:
            try:
                widget.select_range(0, "end")  # type: ignore[attr-defined]
                widget.icursor("end")  # type: ignore[attr-defined]
            except (AttributeError, tk.TclError):
                widget.tag_add("sel", "1.0", "end")  # type: ignore[attr-defined]
            return "break"

        buttons = ("<Button-2>", "<Control-Button-1>") if sys.platform == "darwin" else ("<Button-3>",)
        for cls in ("TEntry", "TCombobox", "Text"):
            for sequence in buttons:
                self.root.bind_class(cls, sequence, popup, add="+")
            self.root.bind_class(cls, "<Control-a>", lambda e: select_all(e.widget), add="+")

    def _header(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.pack(fill="x")
        ttk.Label(bar, text="stallkit", style="Title.TLabel").pack(side="left")
        ttk.Label(bar, text=self.t("tagline"), style="Hint.TLabel").pack(side="left", padx=(10, 0), pady=(4, 0))

        languages = ttk.Combobox(bar, values=[name for _code, name in i18n.LANGUAGES], state="readonly", width=10)
        languages.set(dict(i18n.LANGUAGES)[self.lang])
        languages.bind("<<ComboboxSelected>>", lambda _e: self._switch_language(languages.get()))
        languages.pack(side="right")
        ttk.Button(bar, text=self.t("help"), command=lambda: webbrowser.open(HELP_URL)).pack(side="right", padx=(0, 8))

        self.shop_picker = ttk.Combobox(bar, state="readonly", width=24)
        self.shop_picker.bind("<<ComboboxSelected>>", self._shop_picked)
        self.shop_picker.pack(side="right", padx=(0, 16))
        ttk.Label(bar, text=self.t("shop")).pack(side="right", padx=(0, 6))
        self._fill_shop_picker()

        self.status_label = ttk.Label(bar, text="", style="Status.TLabel")
        self.status_label.pack(side="right", padx=(0, 16))

    # ------------------------------------------------------------------ shops

    def _shop_label(self, shop, index: int) -> str:
        if "anonymise" in self.vars and self.vars["anonymise"].get():
            return self.t("shop_n", n=index + 1)
        return shop.name or self.t("shop_n", n=index + 1)

    def _fill_shop_picker(self) -> None:
        from .. import shops

        every = shops.all_shops()
        self._shop_ids = [shop.id for shop in every]
        self.shop_picker.configure(values=[self._shop_label(s, i) for i, s in enumerate(every)] + [self.t("add_shop")])
        current = shops.current().id
        self.shop_picker.current(self._shop_ids.index(current) if current in self._shop_ids else 0)

    def _shop_picked(self, _event: tk.Event) -> None:
        from .. import shops

        index = self.shop_picker.current()
        if self.busy:
            messagebox.showinfo("stallkit", self.t("wait_for_task"), parent=self.root)
            self._fill_shop_picker()
            return
        if index >= len(self._shop_ids):
            self.add_shop()
        elif self._shop_ids[index] != shops.current().id:
            wanted = self._shop_ids[index]
            self._when_idle(lambda: self.switch_shop(wanted))

    def _when_idle(self, action: Callable[[], None]) -> None:
        """Run `action` now if the worker is idle, else as soon as it is.

        Anything that changes which shop is open must not overlap a background
        job: a status check mid-refresh would save one shop's token into the
        other shop's folder.
        """
        if self.worker.idle():
            action()
            return
        # Busy until it has run: a button clicked in between would capture this
        # shop's form values and then run against the next shop's keys.
        self._start(self.t("please_wait"))

        def then() -> None:
            try:
                action()
            finally:
                self._finish()

        self.worker.run_exclusive(then)

    def switch_shop(self, shop_id: str) -> None:
        """Open another shop: its keys, its token, its folders. Call it through
        `_when_idle` unless the worker is known to be idle."""
        settings.use_shop(shop_id)
        self.app_prefs["shop"] = shop_id
        settings.save_app_prefs(self.app_prefs)
        self.prefs = settings.load_shop_prefs()
        self._shop_generation += 1
        self.shop_state = ("", "")
        self._init_vars()
        self.build()
        self.refresh_status()

    def add_shop(self) -> None:
        def now() -> None:
            from .. import shops

            shop = shops.add()
            self.switch_shop(shop.id)
            self.notebook.select(0)
            self._log(self.t("shop_added") + "\n", "ok")

        self._when_idle(now)

    def remove_shop(self) -> None:
        from .. import shops

        shop = shops.current()
        if not shop.id:
            return
        label = shop.name or shop.id
        if not messagebox.askyesno("stallkit", self.t("confirm_remove_shop", shop=label),
                                   parent=self.root, icon="warning"):
            return

        def now() -> None:
            self.switch_shop("")
            shops.remove(shop.id)
            self._fill_shop_picker()
            self._log(self.t("shop_removed", shop=label) + "\n", "ok")

        self._when_idle(now)

    def _tab(self, notebook: ttk.Notebook, title: str) -> ttk.Frame:
        """A scrollable tab: forms grow, windows do not."""
        outer = ttk.Frame(notebook)
        notebook.add(outer, text=title)
        # The requested height decides how the window first splits between the
        # form and the log; Tk's default canvas height would give the log most of it.
        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0, height=int(self._window_height * 0.6))
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, padding=(14, 12, 14, 12))
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        def wheel(event: tk.Event) -> None:
            if inner.winfo_height() > canvas.winfo_height():
                canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        inner.columnconfigure(0, weight=1)
        return inner

    def _section(self, parent: ttk.Frame, title: str, hint: str = "") -> ttk.Frame:
        frame = ttk.LabelFrame(parent, text=title, padding=(12, 8, 12, 10))
        frame.grid(sticky="ew", pady=(0, 10))
        frame.columnconfigure(1, weight=1)
        if hint:
            label = ttk.Label(frame, text=hint, style="Hint.TLabel", justify="left")
            label.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
            frame.bind("<Configure>", lambda e, lb=label: lb.configure(wraplength=max(200, e.width - 30)))
        return frame

    def _row(self, frame: ttk.Frame) -> int:
        return frame.grid_size()[1]

    def _field(self, frame: ttk.Frame, label: str, var: str, *, secret: str | None = None,
               browse: Callable[[], None] | None = None, width: int = 48) -> ttk.Entry:
        row = self._row(frame)
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
        entry = ttk.Entry(frame, textvariable=self.vars[var], width=width)
        # Paths and keys get the full width; a country code or a number does not.
        entry.grid(row=row, column=1, sticky="ew" if width >= 40 else "w", pady=3)
        if secret:
            def toggle(entry: ttk.Entry = entry, flag: str = secret) -> None:
                entry.configure(show="" if self.vars[flag].get() else "•")

            toggle()
            ttk.Checkbutton(frame, text=self.t("show"), variable=self.vars[secret], command=toggle).grid(
                row=row, column=2, sticky="w", padx=(8, 0)
            )
        if browse:
            ttk.Button(frame, text=self.t("browse"), command=browse).grid(row=row, column=2, sticky="w", padx=(8, 0))
        return entry

    def _buttons(self, frame: ttk.Frame, *specs: tuple[str, Callable[[], None]], primary: int = -1,
                 track: bool = True) -> ttk.Frame:
        row = ttk.Frame(frame)
        row.grid(row=self._row(frame), column=0, columnspan=4, sticky="w", pady=(8, 0))
        for index, (label, command) in enumerate(specs):
            button = ttk.Button(row, text=label, command=command,
                                style="Primary.TButton" if index == primary else "TButton")
            button.pack(side="left", padx=(0, 8))
            if track:
                self.buttons.append(button)
        return row

    def _note(self, frame: ttk.Frame, text: str) -> None:
        label = ttk.Label(frame, text=text, style="Hint.TLabel", justify="left")
        label.grid(row=self._row(frame), column=0, columnspan=4, sticky="w", pady=(8, 0))
        frame.bind("<Configure>", lambda e, lb=label: lb.configure(wraplength=max(200, e.width - 30)), add="+")

    # ------------------------------------------------------------------ tabs

    def _copy_row(self, frame: ttk.Frame, label: str, value: str = "", *, var: str = "",
                  width: int = 44) -> None:
        """A read-only value with a Copy button: what to paste into Etsy's form.

        With `var`, it mirrors that field, so what is shown and copied is always
        what the app will send — a callback changed in step 2 changes here too.
        """
        row = ttk.Frame(frame)
        row.grid(row=self._row(frame), column=0, columnspan=4, sticky="w", pady=2)
        ttk.Label(row, text=label, width=22).pack(side="left")
        if var:
            shown = ttk.Entry(row, width=width, textvariable=self.vars[var], state="readonly")
        else:
            shown = ttk.Entry(row, width=width)
            shown.insert(0, value)
            shown.configure(state="readonly")
        shown.pack(side="left")
        current = (lambda: self.vars[var].get()) if var else (lambda: value)
        ttk.Button(row, text=self.t("copy"), command=lambda: self._copy(current())).pack(side="left", padx=(8, 0))

    def _result_label(self, frame: ttk.Frame) -> ttk.Label:
        label = ttk.Label(frame, text="", style="Status.TLabel", justify="left")
        label.grid(row=self._row(frame), column=0, columnspan=4, sticky="w", pady=(8, 0))
        frame.bind("<Configure>", lambda e, lb=label: lb.configure(wraplength=max(200, e.width - 30)), add="+")
        return label

    def _tab_setup(self, notebook: ttk.Notebook) -> None:
        from .. import shops

        tab = self._tab(notebook, self.t("tab_setup"))
        self.step_frames: dict[int, tuple[ttk.LabelFrame, str]] = {}

        # Step 1: the one-off Etsy app. Everything to type into Etsy's form is here
        # with a Copy button, because a mistyped callback is the classic failure.
        title = self.t("setup_app_title")
        app = self._section(tab, title, self.t("setup_app_hint"))
        self.step_frames[1] = (app, title)
        steps = ttk.Label(app, text=self.t("setup_app_steps"), justify="left")
        steps.grid(row=self._row(app), column=0, columnspan=4, sticky="w", pady=(0, 8))
        app.bind("<Configure>", lambda e: steps.configure(wraplength=max(200, e.width - 30)), add="+")
        self._copy_row(app, self.t("callback_label"), var="redirect")
        self._copy_row(app, self.t("app_description_label"), self.t("app_description_value"), width=60)
        self._buttons(
            app,
            (self.t("open_seller_app"), lambda: webbrowser.open(ETSY_SELLER_APP_URL)),
            (self.t("open_dashboard"), lambda: webbrowser.open(ETSY_DASHBOARD_URL)),
            primary=0,
            track=False,
        )
        self._note(app, self.t("setup_app_wait"))

        # Step 2: paste the two keys; saving checks them with Etsy straight away.
        title = self.t("setup_keys_title")
        keys = self._section(tab, title, self.t("setup_keys_hint"))
        self.step_frames[2] = (keys, title)
        self._field(keys, "Keystring", "keystring")
        self._field(keys, "Shared secret", "secret", secret="show_secret")
        self._field(keys, self.t("callback_field"), "redirect")
        self._buttons(keys, (self.t("save_verify"), self.save_etsy_keys), primary=0)
        self.keys_result = self._result_label(keys)
        self._note(keys, self.t("saved_to", path=settings.env_path()))

        # Step 3: one click, Etsy's own consent page, done.
        title = self.t("setup_connect_title")
        connect = self._section(tab, title, self.t("setup_connect_hint"))
        self.step_frames[3] = (connect, title)
        self._buttons(connect, (self.t("connect_shop"), self.connect_shop), primary=0)
        self.connect_result = self._result_label(connect)
        self.next_row = self._buttons(connect, (self.t("go_to_upload"), lambda: self.notebook.select(1)),
                                      track=False)

        tools = self._section(tab, self.t("setup_tools_title"))
        specs = [
            (self.t("run_checks"), lambda: self.run(["doctor", *self._ws_args()])),
            (self.t("shop_info"), lambda: self.run(["shop", "info"])),
            (self.t("shop_profiles"), lambda: self.run(["shop", "profiles"])),
            (self.t("disconnect"), self.disconnect_shop),
        ]
        if shops.current().id:
            specs.append((self.t("remove_shop"), self.remove_shop))
        self._buttons(tools, *specs)
        self._note(tools, self.t("setup_tools_hint"))

    def _tab_drop(self, notebook: ttk.Notebook) -> None:
        tab = self._tab(notebook, self.t("tab_drop"))

        folder = self._section(tab, self.t("drop_folder_title"), self.t("drop_folder_hint"))
        self._field(folder, self.t("folder"), "workspace", browse=self._browse_workspace)
        self._buttons(
            folder,
            (self.t("create_folder"), lambda: self.run(["drop", "init", *self._ws_args()], then=self._render_template_state)),
            (self.t("open_folder"), self._open_workspace),
        )

        template = self._section(tab, self.t("drop_template_title"), self.t("drop_template_hint"))
        self._field(template, self.t("listing_number"), "template_listing", width=20)
        self._buttons(template, (self.t("copy_settings"), self.capture_template))
        self.template_state = ttk.Label(template, text="", style="Hint.TLabel")
        self.template_state.grid(row=self._row(template), column=0, columnspan=4, sticky="w", pady=(8, 0))
        self._render_template_state()

        upload = self._section(tab, self.t("drop_upload_title"), self.t("drop_upload_hint"))
        self._buttons(
            upload,
            (self.t("check_only"), lambda: self.run(["drop", "auto", "--dry-run", *self._ws_args()])),
            (self.t("upload_drafts"), self.upload_drafts),
            primary=1,
        )

        advanced = self._section(tab, self.t("drop_mockup_title"), self.t("drop_mockup_hint"))
        self._buttons(
            advanced,
            (self.t("prepare_mockups"), lambda: self.run(["drop", "run", *self._ws_args()])),
            (self.t("preview_print_area"), lambda: self.run(["drop", "calibrate", "--preview", *self._ws_args()])),
        )

    def _tab_listings(self, notebook: ttk.Notebook) -> None:
        tab = self._tab(notebook, self.t("tab_listings"))

        export = self._section(tab, self.t("export_title"), self.t("export_hint"))
        row = self._row(export)
        ttk.Label(export, text=self.t("which_listings")).grid(row=row, column=0, sticky="w", padx=(0, 10))
        ttk.Combobox(export, textvariable=self.vars["pull_state"], values=LISTING_STATES, state="readonly", width=14).grid(
            row=row, column=1, sticky="w"
        )
        self._buttons(export, (self.t("save_as_csv"), self.export_listings))

        push = self._section(tab, self.t("push_title"), self.t("push_hint"))
        self._field(push, self.t("csv_file"), "push_csv", browse=lambda: self._browse_csv("push_csv"))
        inventory = ttk.Frame(push)
        inventory.grid(row=self._row(push), column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Checkbutton(inventory, text=self.t("copy_variations_from"), variable=self.vars["use_inventory"]).pack(side="left")
        ttk.Entry(inventory, textvariable=self.vars["inventory_from"], width=16).pack(side="left", padx=(8, 0))
        self._buttons(
            push,
            (self.t("check_only"), lambda: self.push_listings(dry_run=True)),
            (self.t("send_to_etsy"), lambda: self.push_listings(dry_run=False)),
            (self.t("blank_template"), self.listing_template),
            primary=1,
        )

    def _tab_orders(self, notebook: ttk.Notebook) -> None:
        tab = self._tab(notebook, self.t("tab_orders"))

        pull = self._section(tab, self.t("orders_pull_title"), self.t("orders_pull_hint"))
        self._field(pull, self.t("since"), "since", width=14)
        ttk.Checkbutton(pull, text=self.t("unshipped_only"), variable=self.vars["unshipped"]).grid(
            row=self._row(pull), column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        self._buttons(pull, (self.t("save_as_csv"), self.export_orders))

        ship = self._section(tab, self.t("ship_title"), self.t("ship_hint"))
        self._field(ship, self.t("csv_file"), "ship_csv", browse=lambda: self._browse_csv("ship_csv"))
        self._field(ship, self.t("country_code"), "country", width=8)
        self._buttons(
            ship,
            (self.t("check_only"), lambda: self.ship_orders(dry_run=True)),
            (self.t("send_tracking"), lambda: self.ship_orders(dry_run=False)),
            (self.t("list_carriers"), self.list_carriers),
            primary=1,
        )

    def _tab_seo(self, notebook: ttk.Notebook) -> None:
        tab = self._tab(notebook, self.t("tab_seo"))

        audit = self._section(tab, self.t("audit_title"), self.t("audit_hint"))
        self._buttons(
            audit,
            (self.t("score_listings"), lambda: self.run(["seo", "audit"])),
            (self.t("save_report"), self.audit_to_csv),
            primary=0,
        )

        keywords = self._section(tab, self.t("keywords_title"), self.t("keywords_hint"))
        self._field(keywords, self.t("keyword"), "keyword", width=36)
        self._buttons(keywords, (self.t("research"), self.research_keyword))

        suggest = self._section(tab, self.t("suggest_title"), self.t("suggest_hint"))
        self._field(suggest, self.t("listing_number"), "suggest_listing", width=20)
        self._field(suggest, self.t("keyword_optional"), "suggest_keyword", width=36)
        self._buttons(suggest, (self.t("get_suggestions"), self.suggest))

    def _tab_pinterest(self, notebook: ttk.Notebook) -> None:
        tab = self._tab(notebook, self.t("tab_pinterest"))

        app = self._section(tab, self.t("pin_app_title"), self.t("pin_app_hint"))
        self._field(app, "App ID", "pin_app_id")
        self._field(app, "App secret", "pin_secret", secret="show_pin_secret")
        self._field(app, self.t("callback_field"), "pin_redirect")
        ttk.Checkbutton(app, text=self.t("pin_sandbox"), variable=self.vars["pin_sandbox"]).grid(
            row=self._row(app), column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        self._buttons(
            app,
            (self.t("save"), self.save_pinterest_keys),
            (self.t("open_pinterest_apps"), lambda: webbrowser.open(PINTEREST_APPS_URL)),
            track=False,
        )

        account = self._section(tab, self.t("pin_account_title"))
        self._buttons(
            account,
            (self.t("connect_pinterest"), self.connect_pinterest),
            (self.t("status"), lambda: self.run(["pinterest", "status"])),
            (self.t("my_boards"), lambda: self.run(["pinterest", "boards"])),
            (self.t("disconnect"), self.disconnect_pinterest),
            primary=0,
        )

        pins = self._section(tab, self.t("pin_queue_title"), self.t("pin_queue_hint"))
        self._field(pins, self.t("listing_numbers"), "pin_listings")
        self._field(pins, self.t("board"), "pin_board", width=30)
        self._field(pins, self.t("images"), "pin_images", width=12)
        self._field(pins, self.t("per_day"), "pin_per_day", width=6)
        ttk.Checkbutton(pins, text=self.t("pin_ai"), variable=self.vars["pin_ai"]).grid(
            row=self._row(pins), column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        self._buttons(
            pins,
            (self.t("preview"), lambda: self.queue_pins(dry_run=True)),
            (self.t("add_to_queue"), lambda: self.queue_pins(dry_run=False)),
            primary=1,
        )

        post = self._section(tab, self.t("pin_post_title"), self.t("pin_post_hint"))
        self._buttons(
            post,
            (self.t("post_due"), lambda: self.run(["pinterest", "post"])),
            (self.t("show_queue"), lambda: self.run(["pinterest", "list"])),
        )

    def _log_panel(self, parent: ttk.PanedWindow) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=(0, 8, 0, 0))
        bar = ttk.Frame(frame)
        bar.pack(fill="x")
        ttk.Label(bar, text=self.t("log"), font=self.bold).pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=140)
        self.progress.pack(side="left", padx=(12, 0))
        self.running_label = ttk.Label(bar, text="", style="Hint.TLabel")
        self.running_label.pack(side="left", padx=(8, 0))
        self.cancel_button = ttk.Button(bar, text=self.t("cancel"), command=self.cancel_running)
        ttk.Button(bar, text=self.t("clear"), command=self.clear_log).pack(side="right")
        ttk.Button(bar, text=self.t("copy_log"), command=self.copy_log).pack(side="right", padx=(0, 8))
        ttk.Checkbutton(bar, text=self.t("anonymise"), variable=self.vars["anonymise"],
                        command=self._anonymise_changed).pack(side="right", padx=(0, 12))

        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True, pady=(6, 0))
        mono = ("Consolas", 10) if sys.platform == "win32" else ("Menlo", 11) if sys.platform == "darwin" else ("DejaVu Sans Mono", 10)
        text = tk.Text(body, wrap="word", height=8, font=mono, relief="flat", borderwidth=1,
                       background="#fbfbfb", foreground="#1f1f1f", padx=8, pady=6)
        scroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.tag_configure("ok", foreground="#1b7a3a")
        text.tag_configure("err", foreground="#b3261e")
        text.tag_configure("warn", foreground="#9a5b00")
        text.tag_configure("cmd", foreground="#0b57d0", font=(mono[0], mono[1], "bold"))
        text.tag_configure("dim", foreground="#6b6b6b")
        self.log_text = text
        # Replay what was logged before a language switch rebuilt the widget.
        for chunk, tag in self.log_buffer:
            text.insert("end", chunk, tag)
        text.see("end")
        text.configure(state="disabled")
        if not self.log_buffer:
            self._log(self.t("welcome") + "\n", "dim")
        return frame

    # ------------------------------------------------------------------ running

    def run(self, args: list[str], *, then: Callable[[int], None] | None = None) -> None:
        """Run `stallkit <args>` in the background, streaming its output to the log."""
        anonymise = bool(self.vars["anonymise"].get())
        shown = " ".join(self._quote(a) for a in args)
        name = self.shop_state[1]
        if anonymise and name:
            shown = shown.replace(name, "‹your shop›")
        self._log(f"\n▶ stallkit {shown}\n", "cmd")

        def job() -> int:
            return runner.run_cli(args, out=self.out, err=self.err, anonymise=anonymise)

        def done(code: object, error: BaseException | None) -> None:
            self._finish()
            if error is not None:
                self._log(f"✗ {error}\n", "err")
                code = 1
            if code == 0:
                self._log(self.t("done_ok") + "\n", "ok")
            else:
                self._log(self.t("done_fail", code=code) + "\n", "err")
            if then:
                then(int(code) if isinstance(code, int) else 1)

        self._start(" ".join(args[:2]))
        self.worker.submit(job, done)

    def run_python(self, label: str, job: Callable[[], object],
                   then: Callable[[object], None] | None = None,
                   cancel: threading.Event | None = None) -> None:
        """Run a function in the background, with the same busy state as a command.

        With `cancel`, a Cancel button appears while it runs and sets that event;
        the job is expected to watch it.
        """
        self._log(f"\n▶ {label}\n", "cmd")

        def done(result: object, error: BaseException | None) -> None:
            self._cancel = None
            self._finish()
            if error is not None:
                self._log(f"✗ {error}\n", "err")
            elif then:
                then(result)

        self._cancel = cancel
        self._start(label)
        self.worker.submit(job, done)

    def cancel_running(self) -> None:
        if self._cancel is not None and not self._cancel.is_set():
            self._cancel.set()
            self._log(self.t("cancelling") + "\n", "warn")

    def _start(self, label: str) -> None:
        self.busy += 1
        self.running_label.configure(text=self.t("running", what=label))
        self._set_busy_widgets()

    def _finish(self) -> None:
        self.busy = max(0, self.busy - 1)
        self._set_busy_widgets()
        if not self.busy:
            self.refresh_status()

    def _set_busy_widgets(self) -> None:
        state = "disabled" if self.busy else "normal"
        for button in self.buttons:
            try:
                button.configure(state=state)
            except tk.TclError:
                pass
        try:
            self.shop_picker.configure(state="disabled" if self.busy else "readonly")
        except (AttributeError, tk.TclError):
            pass
        # Idle, an indeterminate bar still shows a parked block that reads as
        # "something is half done", so it is only on screen while work is.
        if self.busy:
            if not self.progress.winfo_ismapped():
                self.progress.pack(side="left", padx=(12, 0), before=self.running_label)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self.running_label.configure(text="")
        if self.busy and self._cancel is not None:
            if not self.cancel_button.winfo_ismapped():
                self.cancel_button.pack(side="left", padx=(12, 0), after=self.running_label)
        else:
            self.cancel_button.pack_forget()

    def _drain(self) -> None:
        """Move worker output and results onto the Tk thread, a batch at a time.

        Each event is handled on its own and the next drain is always scheduled:
        one callback that raises must not stop every later result from arriving,
        or the window would sit on "Working…" with its buttons disabled for good.
        """
        try:
            for _ in range(500):
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(event)
                except Exception:  # noqa: BLE001 — shown in the log, never swallowed
                    self._log(traceback.format_exc(), "err")
        finally:
            if not self.closing:
                self._drain_after = self.root.after(40, self._drain)

    def _handle(self, event: tuple) -> None:
        kind = event[0]
        if kind == "log":
            _, stream, text = event
            self._log_output(text, stream)
        elif kind == "done":
            _, callback, result, error = event
            callback(result, error)
        elif kind == "ask":
            _, question, slot = event
            try:
                slot["answer"] = self._ask(question)
            finally:
                # The worker is blocked until this is set; never leave it waiting.
                slot["event"].set()
        elif kind == "exclusive":
            _, action, finished = event
            try:
                action()
            finally:
                finished.set()

    def _ask_from_worker(self, question: str) -> str | None:
        """Called on the worker thread by PromptStream; blocks for the answer."""
        slot: dict = {"event": threading.Event(), "answer": None}
        self.events.put(("ask", question, slot))
        while not slot["event"].wait(0.2):
            if self.closing:
                return None
        return slot["answer"]

    def _ask(self, question: str) -> str | None:
        """A command is waiting for input: ask in a dialog rather than hang."""
        clean = question.strip() or self.t("input_needed")
        if re.search(r"\[(y/N|Y/n|y/n)\]", clean):
            prompt = re.sub(r"\s*\[(y/N|Y/n|y/n)\]:?\s*$", "", clean)
            return "y" if messagebox.askyesno("stallkit", prompt, parent=self.root) else "n"
        return simpledialog.askstring("stallkit", clean, parent=self.root)

    # ------------------------------------------------------------------ log

    def _log_output(self, text: str, stream: str) -> None:
        for line in text.splitlines(keepends=True):
            stripped = line.lstrip()
            tag = "err" if stream == "err" and stripped else ""
            for prefix, name in _LINE_TAGS:
                if stripped.startswith(prefix):
                    tag = name
                    break
            self._log(line, tag)

    def _log(self, text: str, tag: str = "") -> None:
        self.log_buffer.append((text, tag))
        if len(self.log_buffer) > 5000:
            del self.log_buffer[:1000]
        if self.log_text is None:
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text, tag)
        if int(self.log_text.index("end-1c").split(".")[0]) > 6000:
            self.log_text.delete("1.0", "1000.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_buffer.clear()
        if self.log_text is not None:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")

    def copy_log(self) -> None:
        if self.log_text is not None:
            self._copy(self.log_text.get("1.0", "end-1c"))

    def _copy(self, value: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self._log(self.t("copied") + "\n", "dim")

    # ------------------------------------------------------------------ status

    def refresh_status(self) -> None:
        """Work out, quietly, how far the open shop is through setup.

        States: "keys" (none saved), "bad_keys" (Etsy refused them), "disconnected"
        (keys accepted, shop not connected), "connected", "reconnect" (the sign-in
        expired or was revoked), "offline" (Etsy unreachable) and "error".
        """
        generation = self._shop_generation

        def job() -> tuple[str, str, str]:
            from .. import auth, shops
            from ..client import EtsyClient
            from ..config import Config
            from ..errors import AuthError, AuthUnreachable, EtsyApiError, StallKitError

            try:
                config = Config.load()
            except StallKitError:
                return ("keys", "", "")
            token = auth.load_token()
            try:
                with EtsyClient(config, token=token, require_auth=False) as client:
                    if token is None:
                        client.ping()
                        return ("disconnected", "", "")
                    shop = client.shop()
            except EtsyApiError as exc:
                if exc.status == 0:
                    return ("offline", "", str(exc))
                if exc.status == 403 and "api key" in str(exc).lower():
                    return ("bad_keys", "", exc.message)
                if exc.status == 401:
                    return ("reconnect", "", exc.message)
                return ("error", "", str(exc))
            except AuthUnreachable as exc:
                return ("offline", "", str(exc))
            except AuthError as exc:
                return ("reconnect", "", str(exc))
            except StallKitError as exc:
                return ("error", "", str(exc))
            name = str(shop.get("shop_name") or "")
            shops.remember(name, shop.get("shop_id"))
            return ("connected", name, "")

        def done(result: object, error: BaseException | None) -> None:
            if generation != self._shop_generation or self.closing:
                return  # an answer about the shop that was open before a switch
            if not isinstance(result, tuple):
                result = ("offline" if error is not None else "error", "", str(error or ""))
            self.shop_state = (result[0], result[1])
            self._status_detail = result[2]
            if result[0] == "connected":
                self._shop_connected(result[1])
            first, self._first_status = self._first_status, False
            if first and result[0] in ("keys", "bad_keys", "disconnected", "reconnect"):
                self.notebook.select(0)  # setup is not finished: start where it stops
            self._render_status()

        self.worker.submit(job, done)

    def _shop_connected(self, name: str) -> None:
        """Etsy told us the shop's name: label the picker with it."""
        self._fill_shop_picker()

    def _render_status(self) -> None:
        if not hasattr(self, "status_label"):
            return
        state, name = self.shop_state
        shown = "‹your shop›" if self.vars["anonymise"].get() else name
        texts = {
            "": (self.t("status_checking"), "#6b6b6b"),
            "connected": (self.t("status_connected", shop=shown), "#1b7a3a"),
            "keys": (self.t("status_keys"), "#9a5b00"),
            "bad_keys": (self.t("status_bad_keys"), "#b3261e"),
            "disconnected": (self.t("status_disconnected"), "#9a5b00"),
            "reconnect": (self.t("status_reconnect"), "#b3261e"),
            "offline": (self.t("status_offline"), "#b3261e"),
        }
        text, colour = texts.get(state, (self.t("status_error"), "#b3261e"))
        self.status_label.configure(text=text, foreground=colour)
        self._render_steps()

    def _render_steps(self) -> None:
        """Tick the setup steps that are done and point at the one that is next."""
        if not hasattr(self, "step_frames"):
            return
        state, name = self.shop_state
        keys_ok = state in ("disconnected", "connected", "reconnect")
        marks = {
            1: "✓" if keys_ok else ("→" if state in ("keys", "") else ""),
            2: "✓" if keys_ok else ("✗" if state == "bad_keys" else ("→" if state == "keys" else "")),
            3: "✓" if state == "connected" else ("→" if state in ("disconnected", "reconnect") else ""),
        }
        for number, (frame, title) in self.step_frames.items():
            mark = marks[number]
            try:
                frame.configure(text=f"{mark}  {title}" if mark else title)
            except tk.TclError:
                return
        detail = getattr(self, "_status_detail", "")
        if state == "bad_keys":
            self.keys_result.configure(text=self.t("keys_rejected", detail=detail), foreground="#b3261e")
        elif keys_ok:
            self.keys_result.configure(text=self.t("keys_accepted"), foreground="#1b7a3a")
        else:
            self.keys_result.configure(text="")
        shown = "‹your shop›" if self.vars["anonymise"].get() else name
        if state == "connected":
            self.connect_result.configure(text=self.t("connected_as", shop=shown), foreground="#1b7a3a")
            self.next_row.grid()
        else:
            message = {
                "reconnect": self.t("reconnect_needed"),
                "offline": self.t("offline_detail"),
            }.get(state, "")
            self.connect_result.configure(text=message, foreground="#b3261e")
            self.next_row.grid_remove()

    def _anonymise_changed(self) -> None:
        self.app_prefs["anonymise"] = bool(self.vars["anonymise"].get())
        settings.save_app_prefs(self.app_prefs)
        self._fill_shop_picker()
        self._render_status()

    # ------------------------------------------------------------------ actions: setup

    def save_etsy_keys(self) -> None:
        from .. import auth
        from ..config import split_credential

        keystring, secret = split_credential(self.vars["keystring"].get(), self.vars["secret"].get())
        redirect = self.vars["redirect"].get().strip() or settings.ETSY_REDIRECT_DEFAULT
        if not keystring or not secret:
            messagebox.showwarning("stallkit", self.t("need_both_keys"), parent=self.root)
            return
        try:
            auth.validate_redirect_uri(redirect)
        except Exception as exc:  # noqa: BLE001 — shown to the person, not raised
            messagebox.showwarning("stallkit", str(exc), parent=self.root)
            return
        self.vars["keystring"].set(keystring)
        self.vars["secret"].set(secret)
        self.vars["redirect"].set(redirect)

        def now() -> None:
            # Not while a status check runs: its token refresh could land after the
            # old sign-in is cleared below, and bring it back.
            previous = settings.current("ETSY_KEYSTRING")
            path = settings.save(
                {"ETSY_KEYSTRING": keystring, "ETSY_SHARED_SECRET": secret, "ETSY_REDIRECT_URI": redirect}
            )
            self._log(self.t("keys_saved", path=path, key=keystring[:6], n=len(secret)) + "\n", "ok")
            # A token belongs to the app that issued it. New keys make the old sign-in
            # useless, and keeping it would only turn every command into a 401.
            if previous and previous != keystring and auth.clear_token():
                self._log(self.t("token_cleared") + "\n", "warn")

            def verify() -> bool:
                from ..client import EtsyClient
                from ..config import Config

                with EtsyClient(Config.load(), require_auth=False) as client:
                    client.ping()
                return True

            self.run_python(self.t("verifying"), verify,
                            then=lambda _r: self._log(self.t("keys_ok") + "\n", "ok"))

        self._when_idle(now)

    def connect_shop(self) -> None:
        """Etsy's consent page in the browser; the answer comes back to this computer.

        Called directly rather than through `auth login` so that it can be cancelled:
        someone who closes the browser tab should not wait five minutes for a timeout.
        """
        cancel = threading.Event()

        def job() -> tuple[str, tuple[str, ...]]:
            from .. import auth, shops
            from ..client import EtsyClient
            from ..config import Config

            config = Config.load()
            auth.validate_redirect_uri(config.redirect_uri)
            token = auth.login(config, cancel=cancel)
            with EtsyClient(config, token=token) as client:
                shop = client.shop()
            name = str(shop.get("shop_name") or "")
            shops.remember(name, shop.get("shop_id"))
            return name, token.missing_scopes(config.scopes)

        def then(result: object) -> None:
            name, missing = result  # type: ignore[misc]
            shown = "‹your shop›" if self.vars["anonymise"].get() else name
            self._log(self.t("connected_as", shop=shown) + "\n", "ok")
            if missing:
                self._log(self.t("scopes_missing", scopes=" ".join(missing)) + "\n", "warn")

        self._log(self.t("login_browser") + "\n", "dim")
        self.run_python(self.t("connecting"), job, then=then, cancel=cancel)

    def disconnect_shop(self) -> None:
        if messagebox.askyesno("stallkit", self.t("confirm_disconnect"), parent=self.root):
            self.run(["auth", "logout"])

    # ------------------------------------------------------------------ actions: drop

    def _ws_args(self) -> list[str]:
        path = self.vars["workspace"].get().strip()
        self.prefs["workspace"] = path
        settings.save_shop_prefs(self.prefs)
        return ["--path", path] if path else []

    def _browse_workspace(self) -> None:
        chosen = filedialog.askdirectory(parent=self.root, initialdir=self._existing_dir(self.vars["workspace"].get()))
        if chosen:
            self.vars["workspace"].set(str(Path(chosen)))
            self._ws_args()
            self._render_template_state()

    def _open_workspace(self) -> None:
        path = Path(self.vars["workspace"].get().strip())
        if path.is_dir():
            _open_path(path)
        else:
            messagebox.showinfo("stallkit", self.t("folder_missing"), parent=self.root)

    def _render_template_state(self, _code: int = 0) -> None:
        import json

        from ..drop import workspace as workspace_mod

        path = Path(self.vars["workspace"].get().strip() or ".") / workspace_mod.TEMPLATE_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            source = data.get("source_listing_id") or "?"
            text = self.t("template_ready", listing=source)
            if str(source).isdigit() and not self.vars["template_listing"].get().strip():
                self.vars["template_listing"].set(str(source))
        except (OSError, ValueError, AttributeError):
            text = self.t("template_missing")
        if hasattr(self, "template_state"):
            try:
                self.template_state.configure(text=text)
            except tk.TclError:
                pass

    def capture_template(self) -> None:
        listing = self.vars["template_listing"].get().strip()
        if not listing.isdigit():
            messagebox.showwarning("stallkit", self.t("need_listing_number"), parent=self.root)
            return
        self.prefs["template_listing"] = listing
        settings.save_shop_prefs(self.prefs)
        self.run(["drop", "template", "--from-listing", listing, *self._ws_args()], then=self._render_template_state)

    def upload_drafts(self) -> None:
        if messagebox.askyesno("stallkit", self.t("confirm_upload"), parent=self.root):
            self.run(["drop", "auto", *self._ws_args()])

    # ------------------------------------------------------------------ actions: listings

    def _browse_csv(self, var: str) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.root,
            initialdir=self._existing_dir(self.vars[var].get()),
            filetypes=[("CSV", "*.csv"), (self.t("all_files"), "*.*")],
        )
        if chosen:
            self.vars[var].set(str(Path(chosen)))
            self.prefs[var] = str(Path(chosen))
            settings.save_shop_prefs(self.prefs)

    def _save_csv(self, suggested: str) -> str | None:
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            initialdir=self.prefs.get("last_save_dir") or str(Path.home()),
            initialfile=suggested,
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if chosen:
            self.prefs["last_save_dir"] = str(Path(chosen).parent)
            settings.save_shop_prefs(self.prefs)
        return chosen or None

    def export_listings(self) -> None:
        state = self.vars["pull_state"].get() or "active"
        out = self._save_csv(f"listings-{state}.csv")
        if out:
            self.run(["listings", "pull", "--state", state, "-o", out])

    def listing_template(self) -> None:
        out = self._save_csv("listings.csv")
        if out:
            def then(code: int) -> None:
                if code == 0:
                    self.vars["push_csv"].set(out)
                    self.prefs["push_csv"] = out
                    settings.save_shop_prefs(self.prefs)

            self.run(["listings", "template", "-o", out], then=then)

    def push_listings(self, *, dry_run: bool) -> None:
        src = self.vars["push_csv"].get().strip()
        if not src or not Path(src).is_file():
            messagebox.showwarning("stallkit", self.t("pick_csv"), parent=self.root)
            return
        args = ["listings", "push", src]
        if self.vars["use_inventory"].get():
            listing = self.vars["inventory_from"].get().strip()
            if not listing.isdigit():
                messagebox.showwarning("stallkit", self.t("need_listing_number"), parent=self.root)
                return
            args += ["--inventory-from", listing]
            self.prefs["inventory_from"] = listing
        else:
            self.prefs["inventory_from"] = ""
        settings.save_shop_prefs(self.prefs)
        if dry_run:
            self.run(args + ["--dry-run"])
            return
        if not messagebox.askyesno("stallkit", self.t("confirm_push"), parent=self.root):
            return
        results = str(Path(src).with_name(Path(src).stem + "-results.csv"))
        self.run(args + ["--yes", "--out", results])

    # ------------------------------------------------------------------ actions: orders

    def export_orders(self) -> None:
        out = self._save_csv("orders.csv")
        if not out:
            return
        args = ["orders", "pull", "--since", self.vars["since"].get().strip() or "30d", "-o", out]
        if self.vars["unshipped"].get():
            args.append("--unshipped")
        self.run(args)

    def _country(self) -> list[str]:
        country = self.vars["country"].get().strip().upper()
        self.prefs["country"] = country
        settings.save_shop_prefs(self.prefs)
        return ["--country", country] if country else []

    def ship_orders(self, *, dry_run: bool) -> None:
        src = self.vars["ship_csv"].get().strip()
        if not src or not Path(src).is_file():
            messagebox.showwarning("stallkit", self.t("pick_csv"), parent=self.root)
            return
        args = ["orders", "ship", src, *self._country()]
        if dry_run:
            self.run(args + ["--dry-run"])
        elif messagebox.askyesno("stallkit", self.t("confirm_ship"), parent=self.root, icon="warning"):
            self.run(args + ["--yes"])

    def list_carriers(self) -> None:
        country = self._country()
        if not country:
            messagebox.showwarning("stallkit", self.t("need_country"), parent=self.root)
            return
        self.run(["orders", "carriers", *country])

    # ------------------------------------------------------------------ actions: seo

    def audit_to_csv(self) -> None:
        out = self._save_csv("seo-audit.csv")
        if out:
            self.run(["seo", "audit", "-o", out])

    def research_keyword(self) -> None:
        keyword = self.vars["keyword"].get().strip()
        if not keyword:
            messagebox.showwarning("stallkit", self.t("need_keyword"), parent=self.root)
            return
        self.run(["seo", "keywords", keyword])

    def suggest(self) -> None:
        listing = self.vars["suggest_listing"].get().strip()
        if not listing.isdigit():
            messagebox.showwarning("stallkit", self.t("need_listing_number"), parent=self.root)
            return
        keyword = self.vars["suggest_keyword"].get().strip()
        self.run(["seo", "suggest", listing, *(["--keyword", keyword] if keyword else [])])

    # ------------------------------------------------------------------ actions: pinterest

    def save_pinterest_keys(self) -> None:
        path = settings.save(
            {
                "PINTEREST_APP_ID": self.vars["pin_app_id"].get(),
                "PINTEREST_APP_SECRET": self.vars["pin_secret"].get(),
                "PINTEREST_REDIRECT_URI": self.vars["pin_redirect"].get() or settings.PINTEREST_REDIRECT_DEFAULT,
                "PINTEREST_SANDBOX": "1" if self.vars["pin_sandbox"].get() else "",
            }
        )
        self._log(self.t("pin_saved", path=path) + "\n", "ok")

    def connect_pinterest(self) -> None:
        """Like connect_shop: called directly so that it can be cancelled."""
        cancel = threading.Event()

        def job() -> str:
            from .. import pinterest

            token = pinterest.login(pinterest.PinterestConfig.load(), cancel=cancel)
            return token.scope or ""

        self._log(self.t("login_browser") + "\n", "dim")
        self.run_python(self.t("connecting_pinterest"), job,
                        then=lambda _r: self._log(self.t("pinterest_connected") + "\n", "ok"),
                        cancel=cancel)

    def disconnect_pinterest(self) -> None:
        if messagebox.askyesno("stallkit", self.t("confirm_disconnect_pin"), parent=self.root):
            self.run(["pinterest", "logout"])

    def queue_pins(self, *, dry_run: bool) -> None:
        listings = _ids(self.vars["pin_listings"].get())
        board = self.vars["pin_board"].get().strip()
        if not listings or not all(x.isdigit() for x in listings):
            messagebox.showwarning("stallkit", self.t("need_listing_numbers"), parent=self.root)
            return
        if not board:
            messagebox.showwarning("stallkit", self.t("need_board"), parent=self.root)
            return
        self.prefs["pin_board"] = board
        settings.save_shop_prefs(self.prefs)
        args = ["pinterest", "queue", *listings, "--board", board,
                "--per-day", self.vars["pin_per_day"].get().strip() or "2"]
        images = self.vars["pin_images"].get().strip()
        if images:
            args += ["--images", images]
        if self.vars["pin_ai"].get():
            args.append("--ai-modified")
        if dry_run:
            args.append("--dry-run")
        self.run(args)

    # ------------------------------------------------------------------ misc

    @staticmethod
    def _quote(arg: str) -> str:
        return f'"{arg}"' if (" " in arg or not arg) else arg

    @staticmethod
    def _existing_dir(value: str) -> str:
        path = Path(value.strip()) if value.strip() else Path.home()
        for candidate in (path, path.parent):
            if candidate.is_dir():
                return str(candidate)
        return str(Path.home())

    def _remember_tab(self, _event: tk.Event) -> None:
        try:
            self.app_prefs["tab"] = self.notebook.index(self.notebook.select())
        except tk.TclError:
            return
        settings.save_app_prefs(self.app_prefs)

    def _switch_language(self, name: str) -> None:
        code = {label: code for code, label in i18n.LANGUAGES}.get(name, self.lang)
        if code == self.lang:
            return
        self.lang = code
        self.app_prefs["language"] = code
        settings.save_app_prefs(self.app_prefs)
        self.build()

    def close(self) -> None:
        if self.busy and not messagebox.askyesno("stallkit", self.t("confirm_close"), parent=self.root, icon="warning"):
            return
        self.shutdown()
        self.root.destroy()

    def shutdown(self) -> None:
        """Stop the worker and discard what it handed back, here on the Tk thread.

        Undelivered results hold closures over this window; left in the queue they
        would be freed later by whichever thread runs the garbage collector, and Tk
        objects freed off the thread that made them corrupt the interpreter.
        """
        self.closing = True
        try:
            self.root.after_cancel(self._drain_after)
        except tk.TclError:
            pass
        self.worker.stop(wait=2.0)
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break
        # The same goes for the window's own Tk objects. The icon in particular:
        # a PhotoImage freed on the worker thread calls into Tk from there and
        # waits for a main loop that has already ended — a silent hang.
        self._icon = None
        self.vars.clear()
        gc.collect()


def launch() -> None:
    """Start the window. Output that would have gone to a terminal goes to the log."""
    if sys.platform == "win32":
        # Crisp text on scaled displays; without this Windows bitmap-stretches the window.
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    # Keys come from each shop's own home, never from a .env that happens to sit in
    # the folder the app was started from — with several shops it would win for all.
    os.environ["STALLKIT_IGNORE_CWD_ENV"] = "1"
    root = tk.Tk()
    app = App(root)
    # Everything printed from here on — by a command, or by a library it calls —
    # belongs in the log. A windowed .exe has no stdout at all, and a console one
    # would show output nobody is looking at.
    sys.stdout, sys.stderr, sys.stdin = app.out, app.err, app.stdin
    root.mainloop()
