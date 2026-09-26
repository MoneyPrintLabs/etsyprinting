"""The desktop window: its command runner, its settings, its two languages, and the
window itself where a display exists.

The runner tests need no display and run everywhere. The window tests build a real
Tk window, so they skip on a headless Linux runner and run on Windows and macOS —
the two platforms the downloadable app is built for.
"""

from __future__ import annotations

import os
import queue
import string
import sys
import threading
import time
from pathlib import Path

import pytest

from stallkit import __version__, cli
from stallkit.config import write_env_file
from stallkit.desktop import i18n, runner, settings

try:
    from stallkit.desktop import app as app_mod
except ImportError:  # a Python built without Tk; the runner tests still apply
    app_mod = None

needs_tk = pytest.mark.skipif(app_mod is None, reason="this Python has no Tk")

EXAMPLE_CSV = Path(__file__).resolve().parent.parent / "examples" / "listings.csv"


def drain(sink: queue.Queue) -> str:
    parts = []
    while True:
        try:
            event = sink.get_nowait()
        except queue.Empty:
            return "".join(parts)
        if event[0] == "log":
            parts.append(event[2])


@pytest.fixture
def streams():
    sink: queue.Queue = queue.Queue()
    return sink, runner.LogStream(sink, "out"), runner.LogStream(sink, "err")


@pytest.fixture
def env_guard(monkeypatch):
    """settings.save() writes os.environ directly; make sure the test undoes it."""
    for key in ("ETSY_KEYSTRING", "ETSY_SHARED_SECRET", "ETSY_REDIRECT_URI", "ETSY_SHOP_ID",
                "PINTEREST_APP_ID", "PINTEREST_APP_SECRET", "PINTEREST_REDIRECT_URI",
                "PINTEREST_SANDBOX"):
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)


# --- LogStream / PromptStream ---------------------------------------------------


def test_log_stream_refuses_bytes_so_click_treats_it_as_text(streams):
    sink, out, _err = streams
    with pytest.raises(TypeError):
        out.write(b"")
    assert out.write("hello\n") == 6
    assert out.encoding == "utf-8" and not out.isatty()
    assert drain(sink) == "hello\n"


def test_log_stream_remembers_the_question_a_prompt_is_waiting_under(streams):
    _sink, out, _err = streams
    out.write("About to create 2 drafts.\n")
    out.write("Proceed? [y/N]:")
    out.write(" ")
    assert out.question() == "Proceed? [y/N]:"
    out.write("\n")
    assert out.question() == "Proceed? [y/N]:"


def test_prompt_stream_never_blocks_the_ui_thread():
    asked = []
    stream = runner.PromptStream(lambda q: asked.append(q) or "yes", lambda: "question")
    assert threading.current_thread() is threading.main_thread()
    assert stream.readline() == ""
    assert asked == []


def test_prompt_stream_answers_from_another_thread():
    stream = runner.PromptStream(lambda q: f"answer to {q}", lambda: "question")
    result = []
    thread = threading.Thread(target=lambda: result.append(stream.readline()))
    thread.start()
    thread.join(5)
    assert result == ["answer to question\n"]


# --- run_cli ---------------------------------------------------------------------


def test_run_cli_prints_the_version(streams):
    sink, out, err = streams
    assert runner.run_cli(["--version"], out=out, err=err) == 0
    assert f"stallkit {__version__}" in drain(sink)


def test_run_cli_turns_a_library_error_into_a_message_not_a_traceback(streams):
    sink, out, err = streams
    assert runner.run_cli(["shop", "info"], out=out, err=err) == 1
    text = drain(sink)
    assert "Error: ETSY_KEYSTRING is not set" in text
    assert "Traceback" not in text


def test_run_cli_reports_a_usage_error(streams):
    sink, out, err = streams
    assert runner.run_cli(["no-such-command"], out=out, err=err) == 2
    assert "No such command" in drain(sink)


def test_run_cli_writes_no_colour_codes_and_restores_the_cli(streams):
    sink, out, err = streams
    before = (cli.console, cli.err_console, cli.ANONYMISE)
    assert runner.run_cli(["listings", "push", str(EXAMPLE_CSV), "--dry-run"], out=out, err=err,
                          anonymise=True) == 0
    text = drain(sink)
    assert "Dry run:" in text
    assert "\x1b[" not in text
    assert (cli.console, cli.err_console, cli.ANONYMISE) == before


def test_a_confirmation_prompt_is_answered_by_the_window(streams, monkeypatch):
    """`listings push` asks "Proceed?" — in the window that question becomes a dialog."""
    sink, out, err = streams
    asked: list[str] = []

    def answer(question: str) -> str:
        asked.append(question)
        return "n"

    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stdin", runner.PromptStream(answer, out.question))
    codes = []
    thread = threading.Thread(
        target=lambda: codes.append(runner.run_cli(["listings", "push", str(EXAMPLE_CSV)], out=out, err=err))
    )
    thread.start()
    thread.join(30)
    assert codes == [1]
    assert asked and "Proceed?" in asked[0]
    assert "Cancelled." in drain(sink)


# --- settings ---------------------------------------------------------------------


def test_save_merges_into_the_env_file_and_the_running_process(env_guard):
    write_env_file(settings.env_path(), {"ETSY_SHOP_ID": "5", "ETSY_KEYSTRING": "old"})
    path = settings.save({"ETSY_KEYSTRING": " abc ", "ETSY_SHARED_SECRET": "sec"})

    content = path.read_text(encoding="utf-8")
    assert "ETSY_SHOP_ID=5" in content  # a key the window has no field for survives
    assert "ETSY_KEYSTRING=abc" in content and "=old" not in content
    assert os.environ["ETSY_KEYSTRING"] == "abc"
    assert settings.current("ETSY_SHARED_SECRET") == "sec"

    settings.save({"ETSY_SHARED_SECRET": ""})
    assert "ETSY_SHARED_SECRET" not in settings.env_path().read_text(encoding="utf-8")
    assert "ETSY_SHARED_SECRET" not in os.environ


def test_saved_keys_are_what_the_cli_reads(env_guard):
    from stallkit.config import Config

    settings.save({"ETSY_KEYSTRING": "key123", "ETSY_SHARED_SECRET": "sec456",
                   "ETSY_REDIRECT_URI": settings.ETSY_REDIRECT_DEFAULT})
    for key in ("ETSY_KEYSTRING", "ETSY_SHARED_SECRET", "ETSY_REDIRECT_URI"):
        os.environ.pop(key)  # a fresh process: only the file is left
    config = Config.load()
    assert (config.keystring, config.shared_secret) == ("key123", "sec456")
    assert config.redirect_uri == settings.ETSY_REDIRECT_DEFAULT


def test_prefs_round_trip_and_survive_a_corrupt_file():
    settings.save_prefs({"language": "tr", "workspace": "C:/x"})
    assert settings.load_prefs() == {"language": "tr", "workspace": "C:/x"}
    settings.prefs_path().write_text("{not json", encoding="utf-8")
    assert settings.load_prefs() == {}


# --- languages --------------------------------------------------------------------


def _fields(value: str) -> set[str]:
    return {name for _text, name, _spec, _conv in string.Formatter().parse(value) if name}


def test_every_language_has_every_string_with_the_same_placeholders():
    english = i18n.STRINGS["en"]
    for code, _name in i18n.LANGUAGES:
        table = i18n.STRINGS[code]
        assert set(table) == set(english), code
        for key, value in table.items():
            assert value.strip(), (code, key)
            assert _fields(value) == _fields(english[key]), (code, key)


def test_text_falls_back_to_english_then_to_the_key():
    assert i18n.text("xx", "help") == "Help"
    assert i18n.text("tr", "done_fail", code=2).startswith("✗")
    assert i18n.text("tr", "no-such-key") == "no-such-key"


def test_language_detection_reads_the_locale(monkeypatch):
    monkeypatch.setattr(i18n.sys, "platform", "linux")
    monkeypatch.setenv("LC_ALL", "tr_TR.UTF-8")
    assert i18n.detect_language() == "tr"
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    assert i18n.detect_language() == "en"


@needs_tk
def test_listing_numbers_can_be_typed_any_way():
    assert app_mod._ids(" 1, 2;3\n4  5 ") == ["1", "2", "3", "4", "5"]
    assert app_mod._ids("") == []


# --- the window -------------------------------------------------------------------


def _tk_root(tk):
    """A Tk root, or a skip on a machine with no display.

    pytest's default fd-level capture intermittently breaks Tcl's startup on
    Windows: a file in Tk's own library reads as missing. It reproduces with a bare
    `tk.Tk()` under `--capture=fd` and never under `--capture=sys` or `-s`, so it is
    the harness, not the window — and a second attempt succeeds.
    """
    last = None
    for _attempt in range(5):
        try:
            return tk.Tk()
        except tk.TclError as exc:
            if "display" in str(exc).lower():
                pytest.skip(f"no display: {exc}")
            last = exc
    raise last


@pytest.fixture
def window(monkeypatch, tmp_path):
    if app_mod is None:
        pytest.skip("this Python has no Tk")
    import tkinter as tk

    from stallkit.drop import workspace as workspace_mod

    # Never read or write the real Desktop.
    monkeypatch.setattr(workspace_mod, "default_root", lambda: tmp_path / "Etsy Studio")
    root = _tk_root(tk)
    root.withdraw()
    window = app_mod.App(root, language="en")
    yield window
    window.shutdown()
    del window
    root.destroy()


def pump(window, *, until, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        window.root.update()
        if until():
            return
        time.sleep(0.02)
    # Say where it stuck: the log so far, and what every thread is doing.
    import traceback

    stacks = "\n".join(
        f"--- {thread.name}\n" + "".join(traceback.format_stack(sys._current_frames()[thread.ident]))
        for thread in threading.enumerate()
        if thread.ident in sys._current_frames()
    )
    raise AssertionError(
        f"timed out waiting for the window (busy={window.busy}, "
        f"queued={window.events.qsize()})\nlog:\n{log_text(window)}\n{stacks}"
    )


def log_text(window) -> str:
    return window.log_text.get("1.0", "end")


def test_the_window_builds_every_tab_and_keeps_typed_values_across_languages(window):
    assert len(window.notebook.tabs()) == 6
    window.vars["keystring"].set("typed-before-switch")
    window._switch_language("Türkçe")
    assert window.lang == "tr"
    assert window.notebook.tab(0, "text") == i18n.text("tr", "tab_setup")
    assert window.vars["keystring"].get() == "typed-before-switch"
    assert settings.load_prefs()["language"] == "tr"


def test_a_button_runs_the_command_and_its_output_reaches_the_log(window):
    window.run(["--version"])
    assert window.busy == 1
    pump(window, until=lambda: window.busy == 0)
    text = log_text(window)
    assert "▶ stallkit --version" in text
    assert f"stallkit {__version__}" in text
    assert i18n.text("en", "done_ok") in text


def test_creating_the_folder_uses_the_folder_in_the_form(window, tmp_path):
    target = tmp_path / "My Studio"
    window.vars["workspace"].set(str(target))
    window.run(["drop", "init", *window._ws_args()])
    pump(window, until=lambda: window.busy == 0)
    assert (target / "2-PRODUCTS").is_dir()
    assert settings.load_prefs()["workspace"] == str(target)


def test_a_command_that_asks_gets_a_dialog(window, monkeypatch):
    questions = []

    def askyesno(_title, message, **_kwargs):
        questions.append(message)
        return False

    monkeypatch.setattr(app_mod.messagebox, "askyesno", askyesno)
    monkeypatch.setattr(sys, "stdout", window.out)
    monkeypatch.setattr(sys, "stdin", window.stdin)
    window.run(["listings", "push", str(EXAMPLE_CSV)])
    pump(window, until=lambda: window.busy == 0)
    assert questions == ["Proceed?"]
    assert "Cancelled." in log_text(window)


def test_saving_keys_refuses_half_a_credential(window, monkeypatch, env_guard):
    warnings = []
    monkeypatch.setattr(app_mod.messagebox, "showwarning", lambda *a, **k: warnings.append(a[1]))
    window.vars["keystring"].set("only-the-keystring")
    window.vars["secret"].set("")
    window.save_etsy_keys()
    assert warnings == [i18n.text("en", "need_both_keys")]
    assert not settings.env_path().exists()


def test_the_template_listing_is_read_back_from_the_folder(window, tmp_path):
    studio = tmp_path / "Etsy Studio"
    studio.mkdir()
    (studio / "product.json").write_text('{"source_listing_id": 1234567890}', encoding="utf-8")
    window.vars["workspace"].set(str(studio))
    window._render_template_state()
    assert window.vars["template_listing"].get() == "1234567890"
    assert "1234567890" in window.template_state.cget("text")


def test_every_button_builds_the_command_it_says(window, monkeypatch, tmp_path):
    """What each button runs, spelled out: a wrong flag here would reach a live shop."""
    ran: list[list[str]] = []
    monkeypatch.setattr(window, "run", lambda args, then=None: ran.append(list(args)))
    monkeypatch.setattr(app_mod.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(app_mod.filedialog, "asksaveasfilename", lambda **k: str(tmp_path / k["initialfile"]))
    source = tmp_path / "in.csv"
    source.write_text("listing_id\n", encoding="utf-8")
    ws = str(tmp_path / "ws")
    for key, value in {
        "push_csv": str(source), "ship_csv": str(source), "workspace": ws,
        "template_listing": "111", "inventory_from": "222", "country": "tr",
        "keyword": "wall mural", "suggest_listing": "333", "suggest_keyword": "mural",
        "pin_listings": "1, 2", "pin_board": "My Board", "pin_images": "1-3",
    }.items():
        window.vars[key].set(value)
    window.vars["use_inventory"].set(True)

    window.capture_template()
    window.upload_drafts()
    window.export_listings()
    window.listing_template()
    window.push_listings(dry_run=True)
    window.push_listings(dry_run=False)
    window.export_orders()
    window.ship_orders(dry_run=True)
    window.ship_orders(dry_run=False)
    window.list_carriers()
    window.audit_to_csv()
    window.research_keyword()
    window.suggest()
    window.queue_pins(dry_run=True)
    window.queue_pins(dry_run=False)
    window.connect_shop()
    window.disconnect_shop()
    window.disconnect_pinterest()

    out = str(tmp_path)
    assert ran == [
        ["drop", "template", "--from-listing", "111", "--path", ws],
        ["drop", "auto", "--path", ws],
        ["listings", "pull", "--state", "active", "-o", f"{out}{os.sep}listings-active.csv"],
        ["listings", "template", "-o", f"{out}{os.sep}listings.csv"],
        ["listings", "push", str(source), "--inventory-from", "222", "--dry-run"],
        ["listings", "push", str(source), "--inventory-from", "222", "--yes",
         "--out", str(tmp_path / "in-results.csv")],
        ["orders", "pull", "--since", "30d", "-o", f"{out}{os.sep}orders.csv", "--unshipped"],
        ["orders", "ship", str(source), "--country", "TR", "--dry-run"],
        ["orders", "ship", str(source), "--country", "TR", "--yes"],
        ["orders", "carriers", "--country", "TR"],
        ["seo", "audit", "-o", f"{out}{os.sep}seo-audit.csv"],
        ["seo", "keywords", "wall mural"],
        ["seo", "suggest", "333", "--keyword", "mural"],
        ["pinterest", "queue", "1", "2", "--board", "My Board", "--per-day", "2",
         "--images", "1-3", "--ai-modified", "--dry-run"],
        ["pinterest", "queue", "1", "2", "--board", "My Board", "--per-day", "2",
         "--images", "1-3", "--ai-modified"],
        ["auth", "login"],
        ["auth", "logout"],
        ["pinterest", "logout"],
    ]


def test_saying_no_sends_nothing_to_the_shop(window, monkeypatch, tmp_path):
    ran: list[list[str]] = []
    monkeypatch.setattr(window, "run", lambda args, then=None: ran.append(list(args)))
    monkeypatch.setattr(app_mod.messagebox, "askyesno", lambda *a, **k: False)
    source = tmp_path / "in.csv"
    source.write_text("listing_id\n", encoding="utf-8")
    window.vars["push_csv"].set(str(source))
    window.vars["ship_csv"].set(str(source))

    window.upload_drafts()
    window.push_listings(dry_run=False)
    window.ship_orders(dry_run=False)
    window.disconnect_shop()
    window.disconnect_pinterest()
    assert ran == []


def test_pinterest_keys_are_saved_beside_the_etsy_keys(window, env_guard):
    window.vars["pin_app_id"].set("app-1")
    window.vars["pin_secret"].set("secret-2")
    window.vars["pin_sandbox"].set(True)
    window.save_pinterest_keys()
    content = settings.env_path().read_text(encoding="utf-8")
    assert "PINTEREST_APP_ID=app-1" in content
    assert "PINTEREST_APP_SECRET=secret-2" in content
    assert "PINTEREST_SANDBOX=1" in content
    assert f"PINTEREST_REDIRECT_URI={settings.PINTEREST_REDIRECT_DEFAULT}" in content
