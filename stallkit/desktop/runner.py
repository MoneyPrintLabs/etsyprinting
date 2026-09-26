"""Run stallkit commands inside the desktop window.

The window is a front end to the commands the terminal runs, not a second
implementation of them. Every button builds an argument list and `run_cli` executes
it in-process, with output going to the log panel instead of a terminal. What a
command prints, validates and refuses is therefore identical in both places, and the
CLI's test suite covers the window too.

Two things a terminal gives a command for free have to be supplied here:

* **somewhere to print** — `LogStream` hands each write to a queue the Tk thread
  drains, because Tk widgets may only be touched from the thread that made them;
* **somewhere to answer a question** — `PromptStream` is a stdin whose `readline`
  asks the window and blocks until the person answers. `input()`, `typer.confirm()`
  and the paste-the-redirect login flow all read through it unchanged.
"""

from __future__ import annotations

import contextlib
import io
import queue
import threading
import traceback
from typing import Callable, TextIO

# Rich wraps tables and panels to this many columns. The log panel is wide enough
# for it at the default window size, and a fixed figure keeps output identical
# whatever the window happens to measure.
LOG_WIDTH = 100


class LogStream(io.TextIOBase):
    """A write-only text stream that forwards every write to a queue.

    It also remembers the unfinished last line, because that is where `input()` and
    `typer.confirm()` leave their question before reading the answer.
    """

    def __init__(self, sink: queue.Queue, kind: str = "out") -> None:
        super().__init__()
        self._sink = sink
        self._kind = kind
        self._lock = threading.Lock()
        self._line = ""
        self._last_full = ""

    # Rich and the vendored click inspect these to decide how to write.
    @property
    def encoding(self) -> str:  # type: ignore[override]
        return "utf-8"

    @property
    def errors(self) -> str:  # type: ignore[override]
        return "replace"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:  # type: ignore[override]
        # Refusing bytes is load-bearing: click probes a stream with write(b"") and,
        # if that succeeds, treats it as binary and wraps it in a second encoder.
        if not isinstance(text, str):
            raise TypeError(f"write() argument must be str, not {type(text).__name__}")
        if not text:
            return 0
        with self._lock:
            pending = self._line + text
            if "\n" in pending:
                done, _, self._line = pending.rpartition("\n")
                last = done.rsplit("\n", 1)[-1]
                if last.strip():
                    self._last_full = last
            else:
                self._line = pending
        self._sink.put(("log", self._kind, text))
        return len(text)

    def flush(self) -> None:
        pass

    def question(self) -> str:
        """The text a prompt is waiting under: the open line, else the last full one."""
        with self._lock:
            return (self._line.strip() or self._last_full.strip())


class PromptStream(io.TextIOBase):
    """A stdin that asks the window for each line it is asked to read."""

    def __init__(self, ask: Callable[[str], str | None], question: Callable[[], str]) -> None:
        super().__init__()
        self._ask = ask
        self._question = question

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return "utf-8"

    def readable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def readline(self, size: int = -1) -> str:  # type: ignore[override]
        # Asking blocks until the Tk thread answers, so asking FROM the Tk thread
        # would wait for itself forever. Nothing should, but end-of-file is the
        # safe reply if it ever does: prompts treat it as "cancel".
        if threading.current_thread() is threading.main_thread():
            return ""
        answer = self._ask(self._question())
        return "" if answer is None else answer.replace("\n", " ") + "\n"

    def read(self, size: int = -1) -> str:  # type: ignore[override]
        return self.readline()


def run_cli(args: list[str], *, out: TextIO, err: TextIO, anonymise: bool = False) -> int:
    """Run one stallkit command in-process and return its exit code.

    Mirrors `cli.main`: library errors become a one-line message plus hint rather
    than a traceback. The CLI's consoles are swapped for plain ones on `out`/`err`
    for the duration, so nothing depends on how the process was started — a
    windowed .exe has no console at all, and a console's colour codes would land in
    the log as literal escape sequences.

    sys.stdout and sys.stderr are redirected as well, because not everything goes
    through those consoles: Typer prints usage errors on its own console, and the
    login flows print the authorisation URL with plain print().
    """
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        return _run_cli(args, out=out, err=err, anonymise=anonymise)


def _run_cli(args: list[str], *, out: TextIO, err: TextIO, anonymise: bool) -> int:
    from rich.console import Console
    from rich.markup import escape

    from .. import cli
    from ..errors import StallKitError

    def plain(stream: TextIO, *, stderr: bool) -> Console:
        return Console(
            file=stream,
            stderr=stderr,
            width=LOG_WIDTH,
            color_system=None,
            force_terminal=False,
            legacy_windows=False,
            highlight=False,
            emoji=False,
        )

    saved = (cli.console, cli.err_console, cli.ANONYMISE)
    cli.console = plain(out, stderr=False)
    cli.err_console = plain(err, stderr=True)
    cli.ANONYMISE = anonymise
    try:
        cli.app(args=list(args), prog_name="stallkit", standalone_mode=True)
        return 0
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        err.write(f"{exc.code}\n")
        return 1
    except StallKitError as exc:
        cli.err_console.print(f"Error: {escape(str(exc))}")
        hint = getattr(exc, "hint", None)
        if callable(hint) and hint():
            cli.err_console.print(f"→ {escape(hint())}")
        return 1
    except KeyboardInterrupt:
        err.write("Cancelled.\n")
        return 130
    except Exception:  # noqa: BLE001 — the window must survive any command failing
        err.write(traceback.format_exc())
        return 1
    finally:
        cli.console, cli.err_console, cli.ANONYMISE = saved


class Worker:
    """One background thread that runs jobs one at a time, in order.

    Serial on purpose. Commands share a token file that a refresh rewrites and an
    upload lock that a second `drop auto` would trip over, so two jobs overlapping
    would only ever produce a confusing failure.
    """

    def __init__(self, events: queue.Queue) -> None:
        self._events = events
        self._jobs: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._outstanding = 0  # submitted and not yet finished, running or queued
        self._thread = threading.Thread(target=self._loop, name="stallkit-worker", daemon=True)
        self._thread.start()

    def submit(self, job: Callable[[], object], on_done: Callable[[object, BaseException | None], None]) -> None:
        """Run `job` in the background; `on_done(result, error)` runs on the Tk thread."""
        with self._lock:
            self._outstanding += 1
        self._jobs.put(("job", job, on_done))

    def idle(self) -> bool:
        """True when nothing is running or waiting. Only the Tk thread submits, so
        from the Tk thread the answer cannot go stale before it acts on it."""
        with self._lock:
            return self._outstanding == 0

    def run_exclusive(self, action: Callable[[], None]) -> None:
        """Run `action` on the Tk thread at a point where no job is running.

        The worker reaches this item after everything queued before it, hands the
        action to the Tk thread, and waits until it has finished before starting
        anything queued after it. Switching shops changes process-wide environment
        variables; doing it while a job runs could let that job write one shop's
        token into another shop's folder.
        """
        with self._lock:
            self._outstanding += 1
        self._jobs.put(("exclusive", action, None))

    def stop(self, wait: float = 0.0) -> bool:
        """Finish the current job, drop the rest, and end the thread.

        Returns False if a job was still running after `wait` seconds.
        """
        self._stop.set()
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            self._outstanding = 0
        self._jobs.put(None)
        if wait:
            self._thread.join(wait)
        return not self._thread.is_alive()

    def _loop(self) -> None:
        while True:
            item = self._jobs.get()
            if item is None:
                return
            kind, job, on_done = item
            if kind == "exclusive":
                finished = threading.Event()
                self._events.put(("exclusive", job, finished))
                # Wait for the Tk thread, but not forever: once the window is
                # closing, nobody will ever set it.
                while not finished.wait(0.2):
                    if self._stop.is_set():
                        return
                self._finish_one()
                item = job = finished = None
                continue
            try:
                result, error = job(), None
            except BaseException as exc:  # noqa: BLE001 — reported to the window, not lost
                result, error = None, exc
            self._finish_one()
            self._events.put(("done", on_done, result, error))
            # The job and its callback close over the window. Holding them until the
            # next job arrives would make this thread the one that frees the window,
            # and Tk objects must only be freed by the thread that created them.
            item = job = on_done = result = error = None

    def _finish_one(self) -> None:
        with self._lock:
            self._outstanding = max(0, self._outstanding - 1)
