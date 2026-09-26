"""The desktop window: every stallkit command behind a button, for people who do not
use a terminal.

Each action builds the same argument list you would type and hands it to
`runner.run_cli`. The window adds what a terminal cannot: forms instead of flags,
file pickers instead of paths, a confirmation dialog before anything reaches the live
shop, and a log that stays on screen.
"""

from __future__ import annotations

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

ETSY_APPS_URL = "https://www.etsy.com/developers/your-apps"
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
        self.prefs = settings.load_prefs()
        self.lang = language or self.prefs.get("language") or i18n.detect_language()
        self.events: queue.Queue = queue.Queue()
        self.out = runner.LogStream(self.events, "out")
        self.err = runner.LogStream(self.events, "err")
        self.stdin = runner.PromptStream(self._ask_from_worker, self.out.question)
        self.worker = runner.Worker(self.events)
        self.busy = 0
        self.buttons: list[ttk.Button] = []
        self.shop_label_value = ""
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

    def t(self, key: str, **kwargs: object) -> str:
        return i18n.text(self.lang, key, **kwargs)

    def _init_vars(self) -> None:
        from ..drop import workspace as workspace_mod

        p = self.prefs
        text = {
            "keystring": settings.current("ETSY_KEYSTRING"),
            "secret": settings.current("ETSY_SHARED_SECRET"),
            "redirect": settings.current("ETSY_REDIRECT_URI", settings.ETSY_REDIRECT_DEFAULT),
            "workspace": p.get("workspace") or str(workspace_mod.default_root()),
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
            "anonymise": bool(p.get("anonymise", False)),
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
        tab = self.prefs.get("tab", 0)
        self.notebook.select(tab if isinstance(tab, int) and 0 <= tab < len(self.notebook.tabs()) else 0)
        self.notebook.bind("<<NotebookTabChanged>>", self._remember_tab)
        self._render_status()
        self._set_busy_widgets()

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
        self.status_label = ttk.Label(bar, text="", style="Status.TLabel")
        self.status_label.pack(side="right", padx=(0, 16))

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

    def _tab_setup(self, notebook: ttk.Notebook) -> None:
        tab = self._tab(notebook, self.t("tab_setup"))

        app = self._section(tab, self.t("setup_app_title"), self.t("setup_app_hint"))
        callback = ttk.Frame(app)
        callback.grid(row=self._row(app), column=0, columnspan=4, sticky="ew")
        ttk.Label(callback, text=self.t("callback_label")).pack(side="left", padx=(0, 10))
        shown = ttk.Entry(callback, width=40)
        shown.insert(0, settings.ETSY_REDIRECT_DEFAULT)
        shown.configure(state="readonly")
        shown.pack(side="left")
        ttk.Button(callback, text=self.t("copy"), command=lambda: self._copy(settings.ETSY_REDIRECT_DEFAULT)).pack(
            side="left", padx=(8, 0)
        )
        self._buttons(app, (self.t("open_etsy_apps"), lambda: webbrowser.open(ETSY_APPS_URL)), track=False)

        keys = self._section(tab, self.t("setup_keys_title"), self.t("setup_keys_hint"))
        self._field(keys, "Keystring", "keystring")
        self._field(keys, "Shared secret", "secret", secret="show_secret")
        self._field(keys, self.t("callback_field"), "redirect")
        self._buttons(keys, (self.t("save_verify"), self.save_etsy_keys), primary=0)
        self._note(keys, self.t("saved_to", path=settings.env_path()))

        shop = self._section(tab, self.t("setup_connect_title"), self.t("setup_connect_hint"))
        self._buttons(
            shop,
            (self.t("connect_shop"), self.connect_shop),
            (self.t("run_checks"), lambda: self.run(["doctor"])),
            (self.t("shop_info"), lambda: self.run(["shop", "info"])),
            (self.t("shop_profiles"), lambda: self.run(["shop", "profiles"])),
            (self.t("disconnect"), self.disconnect_shop),
            primary=0,
        )

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
            (self.t("connect_pinterest"), lambda: self.run(["pinterest", "login"])),
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
        self._log(f"\n▶ stallkit {' '.join(self._quote(a) for a in args)}\n", "cmd")
        anonymise = bool(self.vars["anonymise"].get())

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
                   then: Callable[[object], None] | None = None) -> None:
        """Run a function in the background, with the same busy state as a command."""
        self._log(f"\n▶ {label}\n", "cmd")

        def done(result: object, error: BaseException | None) -> None:
            self._finish()
            if error is not None:
                self._log(f"✗ {error}\n", "err")
            elif then:
                then(result)

        self._start(label)
        self.worker.submit(job, done)

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
        """Ask Etsy which shop the stored token belongs to, without logging it."""
        def job() -> tuple[str, str]:
            from .. import auth
            from ..client import EtsyClient
            from ..config import Config
            from ..errors import StallKitError

            try:
                config = Config.load()
            except StallKitError:
                return ("keys", "")
            token = auth.load_token()
            if token is None:
                return ("disconnected", "")
            try:
                with EtsyClient(config, token=token) as client:
                    shop = client.shop()
            except StallKitError:
                return ("error", "")
            return ("connected", str(shop.get("shop_name") or ""))

        def done(result: object, error: BaseException | None) -> None:
            state, name = result if isinstance(result, tuple) else ("error", "")
            self.shop_label_value = f"{state}|{name}"
            self._render_status()

        self.worker.submit(job, done)

    def _render_status(self) -> None:
        if not hasattr(self, "status_label"):
            return
        state, _, name = self.shop_label_value.partition("|")
        if not state:
            text, colour = self.t("status_checking"), "#6b6b6b"
        elif state == "connected":
            shown = "‹your shop›" if self.vars["anonymise"].get() else name
            text, colour = self.t("status_connected", shop=shown), "#1b7a3a"
        elif state == "keys":
            text, colour = self.t("status_keys"), "#9a5b00"
        elif state == "disconnected":
            text, colour = self.t("status_disconnected"), "#9a5b00"
        else:
            text, colour = self.t("status_error"), "#b3261e"
        self.status_label.configure(text=text, foreground=colour)

    def _anonymise_changed(self) -> None:
        self.prefs["anonymise"] = bool(self.vars["anonymise"].get())
        settings.save_prefs(self.prefs)
        self._render_status()

    # ------------------------------------------------------------------ actions: setup

    def save_etsy_keys(self) -> None:
        from ..config import split_credential

        keystring, secret = split_credential(self.vars["keystring"].get(), self.vars["secret"].get())
        redirect = self.vars["redirect"].get().strip() or settings.ETSY_REDIRECT_DEFAULT
        if not keystring or not secret:
            messagebox.showwarning("stallkit", self.t("need_both_keys"), parent=self.root)
            return
        try:
            from .. import auth

            auth.validate_redirect_uri(redirect)
        except Exception as exc:  # noqa: BLE001 — shown to the person, not raised
            messagebox.showwarning("stallkit", str(exc), parent=self.root)
            return
        self.vars["keystring"].set(keystring)
        self.vars["secret"].set(secret)
        self.vars["redirect"].set(redirect)
        path = settings.save(
            {"ETSY_KEYSTRING": keystring, "ETSY_SHARED_SECRET": secret, "ETSY_REDIRECT_URI": redirect}
        )
        self._log(self.t("keys_saved", path=path, key=keystring[:6], n=len(secret)) + "\n", "ok")

        def verify() -> bool:
            from ..client import EtsyClient
            from ..config import Config

            with EtsyClient(Config.load(), require_auth=False) as client:
                client.ping()
            return True

        self.run_python(self.t("verifying"), verify, then=lambda _r: self._log(self.t("keys_ok") + "\n", "ok"))

    def connect_shop(self) -> None:
        self._log(self.t("login_browser") + "\n", "dim")
        self.run(["auth", "login"])

    def disconnect_shop(self) -> None:
        if messagebox.askyesno("stallkit", self.t("confirm_disconnect"), parent=self.root):
            self.run(["auth", "logout"])

    # ------------------------------------------------------------------ actions: drop

    def _ws_args(self) -> list[str]:
        path = self.vars["workspace"].get().strip()
        self.prefs["workspace"] = path
        settings.save_prefs(self.prefs)
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
        settings.save_prefs(self.prefs)
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
            settings.save_prefs(self.prefs)

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
            settings.save_prefs(self.prefs)
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
                    settings.save_prefs(self.prefs)

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
        settings.save_prefs(self.prefs)
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
        settings.save_prefs(self.prefs)
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
        settings.save_prefs(self.prefs)
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
            self.prefs["tab"] = self.notebook.index(self.notebook.select())
        except tk.TclError:
            return
        settings.save_prefs(self.prefs)

    def _switch_language(self, name: str) -> None:
        code = {label: code for code, label in i18n.LANGUAGES}.get(name, self.lang)
        if code == self.lang:
            return
        self.lang = code
        self.prefs["language"] = code
        settings.save_prefs(self.prefs)
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


def launch() -> None:
    """Start the window. Output that would have gone to a terminal goes to the log."""
    if sys.platform == "win32":
        # Crisp text on scaled displays; without this Windows bitmap-stretches the window.
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    app = App(root)
    # Everything printed from here on — by a command, or by a library it calls —
    # belongs in the log. A windowed .exe has no stdout at all, and a console one
    # would show output nobody is looking at.
    sys.stdout, sys.stderr, sys.stdin = app.out, app.err, app.stdin
    root.mainloop()
