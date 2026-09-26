"""The desktop window, and the single entry point the downloadable app is built from.

One executable does both jobs. Double-clicked, with no arguments, it opens the
window. Given arguments — `stallkit.exe pinterest post` from Task Scheduler, say —
it is the command line tool, so the download never needs a Python install for either.
"""

from __future__ import annotations

import io
import sys
from datetime import date


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    # macOS adds a process serial number when an app is opened from Finder.
    args = [a for a in args if not a.startswith("-psn_")]
    if args == ["--self-test"]:
        if (sys.stdout is None or sys.stderr is None) and not _attach_parent_console():
            _log_to_file()
        sys.exit(self_test())
    if args:
        sys.exit(_run_as_cli(args))
    from .app import launch

    launch()


def _run_as_cli(args: list[str]) -> int:
    """Behave exactly like the `stallkit` command.

    A windowed executable starts with no console: stdout is None and every print
    would vanish. Borrow the console of whatever started it, if anything did;
    otherwise (Task Scheduler) append to a dated log file so a scheduled run still
    leaves a record.
    """
    if sys.stdout is None or sys.stderr is None:
        if not _attach_parent_console():
            _log_to_file()

    from ..cli import main as cli_main

    sys.argv = ["stallkit", *args]
    try:
        cli_main()
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return 0


def _attach_parent_console() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            return False
        out = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
        sys.stdout = sys.stderr = out
        sys.stdin = open("CONIN$", encoding="utf-8", errors="replace")  # noqa: SIM115
        return True
    except OSError:
        return False


def _log_to_file() -> None:
    from ..config import home_dir

    folder = home_dir() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / f"stallkit-{date.today().isoformat()}.log", "a", encoding="utf-8")  # noqa: SIM115
    sys.stdout = sys.stderr = handle
    # Nobody is there to answer a prompt; end-of-file makes it cancel instead of hang.
    sys.stdin = io.StringIO("")


def self_test() -> int:
    """Prove a built executable is complete: every module imports and the window
    can be built. Run by the release workflow on the packaged app, where a missing
    data file shows up — not in a source checkout, where it cannot."""
    import tkinter as tk

    from .. import __version__, cli  # noqa: F401 — importing is the test
    from . import app, i18n, icon

    icon.render(64)
    for language, _name in i18n.LANGUAGES:
        missing = set(i18n.STRINGS["en"]) ^ set(i18n.STRINGS[language])
        if missing:
            print(f"self-test: {language} strings differ: {sorted(missing)}")
            return 1
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        # Only a genuinely headless Linux box may skip the window. A missing
        # init.tcl is exactly the packaging fault this test exists to catch.
        if "display" in str(exc).lower():
            print(f"self-test: no display ({exc}); window not built")
            print(f"stallkit {__version__} self-test passed (without window)")
            return 0
        print(f"self-test: Tk failed to start: {exc}")
        return 1
    try:
        root.withdraw()
        window = app.App(root, language="en")
        window.build()
        root.update_idletasks()
        window.shutdown()
    finally:
        root.destroy()
    print(f"stallkit {__version__} self-test passed")
    return 0
