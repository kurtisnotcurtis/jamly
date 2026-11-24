"""Unified single-command CLI for Jamly."""
from __future__ import annotations

import importlib
import sys

from . import __version__

COMMANDS: dict[str, tuple[str, str]] = {
    "companion": ("jamly.companion", "Open or reconnect to the local web companion (default)"),
    "split": ("jamly.run", "Split media URLs or local audio files in the terminal"),
    "server": ("jamly.server", "Run the local loopback backend in the foreground"),
    "doctor": ("jamly.bootstrap", "Check Python, yt-dlp, Deno, ffmpeg, and Demucs readiness"),
    "service": ("jamly.service", "Manage the background login service, token, or browser assets"),
}


def format_help() -> str:
    lines = [
        "usage: jamly [command] [options]",
        "",
        "Split media URLs or local audio files into isolated stems locally with Demucs.",
        "Running `jamly` with no command opens the local web companion.",
        "",
        "commands:",
    ]
    for name, (_module, summary) in COMMANDS.items():
        lines.append("  %-12s %s" % (name, summary))
    lines.extend((
        "",
        "options:",
        "  -h, --help     Show this help message and exit",
        "  -V, --version  Show program version and exit",
        "",
        "Run `jamly <command> --help` for command-specific options.",
    ))
    return "\n".join(lines)


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return importlib.import_module(COMMANDS["companion"][0]).main([])
    command = arguments[0]
    if command in ("-h", "--help"):
        print(format_help())
        return 0
    if command in ("-V", "--version"):
        print("jamly " + __version__)
        return 0
    target = COMMANDS.get(command)
    if target is None:
        if command in ("-i", "--input"):
            print(
                "jamly: use `jamly split` to split tracks from the terminal (run `jamly --help` for commands)",
                file=sys.stderr,
            )
        else:
            print(
                "jamly: unknown command %r (choose from %s; run `jamly --help`)"
                % (command, ", ".join(COMMANDS)),
                file=sys.stderr,
            )
        return 2
    return importlib.import_module(target[0]).main(arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
