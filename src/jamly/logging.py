"""Terse, friendly, swappable run-time logging for jamly.

Every user-facing line goes through :class:`Logger` so the wording/colour/layout lives in one
place and can be swapped or captured by tests (``logger.stream = io.StringIO()``). The default is
quiet-friendly: compact one-liners with a leading symbol, optional ANSI colour only on an interactive
terminal.

Pure standard library; no third-party imports so it runs anywhere Python 3 does.
"""
from __future__ import annotations

import os
import sys
from typing import Optional


SYMBOL = {"start": "\u25b6", "ok": "\u2713", "fail": "\u2717",
          "warn": "\u26a0", "info": "\u2022", "done": "\u2014"}

_ANSI = {
    "bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "cyan": "36", "grey": "90", "reset": "0",
}


def _auto_color(stream=None) -> bool:
    """True when colour should be emitted (interactive terminal or explicitly forced)."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("JAMLY_COLOR") == "1":
        return True
    try:
        s = stream or sys.stdout
    except Exception:
        return False
    return bool(getattr(s, "isatty", lambda: False)())


class Logger:
    """Emits a compact, friendly run log.

    Parameters: ``quiet`` suppresses all output (useful for machine pipes); ``color`` auto-detects an
    interactive terminal unless explicitly set; ``stream`` routes output to a file-like object instead
    of stderr -- handy for tests and redirecting. Every public method is pure string formatting plus one
    write, so they are trivially unit-testable by capturing the stream.
    """

    def __init__(self, quiet: bool = False, color: Optional[bool] = None) -> None:
        self.quiet = quiet
        self.color_enabled = bool(color if color is not None else _auto_color())
        self.stream: Optional[object] = None

    def _write(self, text: str) -> None:
        out = self.stream or sys.stderr
        try:
            out.write(text + "\n")
            out.flush()
        except (ValueError, OSError):
            pass

    def _emit(self, text: str) -> None:
        if self.quiet:
            return
        self._write(text)

    def _c(self, text: str, color_name: Optional[str]) -> str:
        if not color_name or not self.color_enabled:
            return text
        return "\x1b[%sm%s\x1b[0m" % (_ANSI[color_name], text)

    def step(self, label: str) -> None:
        """Announce the start of a pipeline stage (►)."""
        self._emit("%s %s" % (SYMBOL["start"], self._c(label, "cyan")))

    def ok(self, msg: str = "") -> None:
        self._emit("%s %s" % (SYMBOL["ok"], self._c(msg or "", "green")))

    def fail(self, msg: str) -> None:
        self._emit("%s %s" % (SYMBOL["fail"], self._c(msg, "red")))

    def warn(self, msg: str) -> None:
        self._emit("%s %s" % (SYMBOL["warn"], self._c(msg, "yellow")))

    def link(self, path: str, rel_path: Optional[str] = None) -> None:
        """Print an output file path as a clickable Windows link; plain elsewhere."""
        target = rel_path or path
        if os.path.isabs(target):
            display = os.path.relpath(target, os.getcwd()) or target
        else:
            display = target
        if sys.platform.startswith("win") and not self.quiet:
            open_hint = "click here to open" + ("  [%s]" % display) if display != target else ""
            self._emit("%s\n   %s%s" % (display, self._c(target, "cyan"), open_hint))
        else:
            self._emit(display)
