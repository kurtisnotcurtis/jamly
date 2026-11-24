"""Shared paths, pipeline settings, and local browser pairing."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from filelock import FileLock
from platformdirs import user_data_path

from . import __version__
from .models import (
    AUDIO_FORMATS, DEFAULT_AUDIO_FORMAT, DEFAULT_MODEL, DEFAULT_STEM, MODELS, STEM_MODES,
    validate_audio_format, validate_model, validate_stem,
)

API_VERSION = 3
MIN_TOKEN_LENGTH = 32
_CHROME_EXT_ID_RE = re.compile(r"^[a-p]{32}$")
_MOZ_EXT_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def state_directory(value: str | None = None) -> Path:
    return Path(
        value or os.environ.get("JAMLY_STATE_DIR")
        or os.environ.get("JAMLY_SCOPE") or user_data_path("jamly", appauthor=False)
    ).expanduser().absolute()


def default_output_directory() -> str:
    return str(Path(os.environ.get("JAMLY_OUTPUT_DIR", str(Path.home() / "Music" / "jamly")))
               .expanduser().absolute())


def runtime_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    queue = args.queue or os.environ.get("JAMLY_QUEUE")
    explicit_state = (args.state_dir or os.environ.get("JAMLY_STATE_DIR")
                      or os.environ.get("JAMLY_SCOPE"))
    state = state_directory(explicit_state)
    return state, Path(queue).expanduser().absolute() if queue else state / "jamly_queue.json"


def nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return number


def add_pipeline_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-V", "--version", action="version", version="%(prog)s " + __version__)
    parser.add_argument("--state-dir", "--scope", dest="state_dir", default=None,
                        help="Jamly state directory (default: per-user application data)")
    parser.add_argument("--queue", default=None, help="Override the queue file")
    parser.add_argument("--ffmpeg-dir", default=None, help="Directory containing ffmpeg and ffprobe")
    parser.add_argument("-o", "--output-dir", default=default_output_directory())
    parser.add_argument("--audio-format", choices=AUDIO_FORMATS, default=DEFAULT_AUDIO_FORMAT)
    parser.add_argument("--model", choices=tuple(MODELS), default=DEFAULT_MODEL,
                        help="htdemucs: Balanced (default); htdemucs_ft: Higher quality (~4x slower); "
                             "htdemucs_6s: 6-stem HTDemucs")
    parser.add_argument("--stem", choices=tuple(STEM_MODES), default=DEFAULT_STEM,
                        help="Target stem pair (drums, vocals, bass, guitar, piano, other) "
                             "or all individual stems (default: drums)")
    parser.add_argument("-t", "--threads", type=nonnegative_int, default=0,
                        help="Parallel Demucs jobs; higher values use more memory (default: 0)")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    parser.add_argument("--replace-existing", action="store_true",
                        help="Replace a completed track/format directory")


def pipeline_options(args: argparse.Namespace) -> dict:
    ffmpeg_dir = getattr(args, "ffmpeg_dir", None)
    return {
        "output_dir": str(Path(args.output_dir).expanduser().absolute()),
        "audio_format": args.audio_format,
        "model": getattr(args, "model", DEFAULT_MODEL),
        "stem": getattr(args, "stem", DEFAULT_STEM),
        "threads": args.threads,
        "device": getattr(args, "device", "auto"),
        "replace_existing": args.replace_existing,
        "ffmpeg_dir": str(Path(ffmpeg_dir).expanduser().absolute()) if ffmpeg_dir else None,
    }


def job_arguments(args: argparse.Namespace, options: dict) -> argparse.Namespace:
    result = argparse.Namespace(**vars(args))
    # Jobs saved before model/stem selection was introduced always used Balanced drums.
    result.model = options.get("model", DEFAULT_MODEL)
    result.stem = options.get("stem", DEFAULT_STEM)
    for key in ("output_dir", "audio_format", "threads", "device", "replace_existing", "ffmpeg_dir"):
        if key in options:
            setattr(result, key, options[key])
    return result


def token_path(state_dir: Path) -> Path:
    return state_dir / "server-token"


def _normalized_queue_id(queue: Path) -> str:
    return os.path.normcase(str(queue.resolve()))


def instance_path(state: Path, queue: Path) -> Path:
    identity = hashlib.sha256(_normalized_queue_id(queue).encode()).hexdigest()[:16]
    return state / ("server-" + identity + ".json")


def pairing_proof(token: str, nonce: str, url: str, queue: Path) -> str:
    context = json.dumps(["jamly-backend-v1", nonce, url, _normalized_queue_id(queue)],
                         separators=(",", ":")).encode()
    return hmac.new(token.encode(), context, hashlib.sha256).hexdigest()


def client_pairing_proof(token: str, nonce: str, port: int) -> str:
    context = json.dumps(["jamly-client-v1", nonce, int(port)],
                         separators=(",", ":")).encode()
    return hmac.new(token.encode(), context, hashlib.sha256).hexdigest()


def validate_token(token: str) -> str:
    if (not isinstance(token, str) or len(token) < MIN_TOKEN_LENGTH
            or not token.isascii() or any(c.isspace() for c in token)):
        raise ValueError(
            "the server token must be at least %d non-whitespace ASCII characters" % MIN_TOKEN_LENGTH
        )
    return token


def read_token_file(path: str | Path) -> str:
    token_file = Path(path).expanduser()
    token = token_file.read_text(encoding="utf-8").strip()
    return validate_token(token)


def validate_allowed_origin(origin: str) -> str:
    if not isinstance(origin, str) or not origin.strip() or "*" in origin:
        raise ValueError("Use exact origin values, not '*'")
    value = origin.strip()
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https", "chrome-extension", "moz-extension")
            or not parsed.netloc or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Invalid allowed origin: %s" % value)
    if value.endswith("/"):
        value = value.rstrip("/")
    return value


def validate_extension_origin(origin: str) -> str:
    value = validate_allowed_origin(origin)
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "chrome-extension" and _CHROME_EXT_ID_RE.fullmatch(host) and parsed.port is None:
        return "chrome-extension://" + host
    if parsed.scheme == "moz-extension" and _MOZ_EXT_ID_RE.fullmatch(host) and parsed.port is None:
        return "moz-extension://" + host
    raise ValueError("Enter a valid chrome-extension:// or moz-extension:// origin")


def token_command(state_dir: Path) -> str:
    command = ["jamly-service", "token", "--state-dir", str(state_dir)]
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def pairing_token(state_dir: Path, explicit: str | None = None, *,
                  token_file: str | Path | None = None) -> str:
    if explicit is not None and explicit != "":
        return validate_token(explicit)
    if token_file:
        return read_token_file(token_file)
    env_token = os.environ.get("JAMLY_SERVER_TOKEN")
    if env_token:
        return validate_token(env_token)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = token_path(state_dir)
    with FileLock(str(path) + ".lock", timeout=5):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            token = path.read_text(encoding="utf-8").strip()
        else:
            token = secrets.token_urlsafe(32)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(token + "\n")
    try:
        return validate_token(token)
    except ValueError as exc:
        raise ValueError("invalid pairing token file %s: %s" % (path, exc)) from exc


def validate_preferences(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - {"model", "audio_format", "output_dir", "stem"}:
        raise ValueError("Settings accept only model, stem, audio_format and output_dir")
    result = dict(data)
    if "model" in data:
        validate_model(data["model"])
    if "stem" in data:
        validate_stem(data["stem"], data["model"] if "model" in data else None)
    if "audio_format" in data:
        validate_audio_format(data["audio_format"])
    if "output_dir" in data:
        value = data["output_dir"]
        if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Enter an absolute output directory")
        try:
            path = Path(value).expanduser()
        except RuntimeError as exc:
            raise ValueError("Cannot expand the output directory") from exc
        if not path.is_absolute():
            raise ValueError("Enter an absolute output directory")
        if path.exists() and not path.is_dir():
            raise ValueError("The output location is a file, not a directory")
        result["output_dir"] = str(path)
    return result


def write_private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + "-", delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _config_file_args(parser: argparse.ArgumentParser, path: str | None) -> list[str]:
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = {action.dest for action in parser._actions} - {"help", "config", "version"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("unsupported server configuration fields")
    # Feed values back through argparse so types and choices are validated too.
    config_args: list[str] = []
    for action in parser._actions:
        if action.dest not in data:
            continue
        value = data[action.dest]
        option = next((name for name in action.option_strings if name.startswith("--")), None)
        if not option:
            continue
        if isinstance(action, argparse._StoreTrueAction):
            if not isinstance(value, bool):
                raise ValueError("%s must be a boolean" % action.dest)
            if value:
                config_args.append(option)
        elif isinstance(action, argparse._AppendAction):
            if not isinstance(value, list):
                raise ValueError("%s must be a list" % action.dest)
            for entry in value:
                config_args.extend((option, str(entry)))
        elif value is not None:
            config_args.extend((option, str(value)))
    return config_args


def _preference_args(parsed: argparse.Namespace) -> list[str]:
    state, _queue = runtime_paths(parsed)
    path = state / "preferences.json"
    if not path.exists():
        return []
    try:
        data = validate_preferences(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise ValueError("Invalid companion settings in %s: %s" % (path, exc)) from exc
    return [part for key, value in data.items() for part in ("--" + key.replace("_", "-"), value)]


def parse_configured_args(parser: argparse.ArgumentParser, argv=None, *,
                          preferences: bool = False) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    preliminary, _ = parser.parse_known_args(argv)
    config_args = _config_file_args(parser, getattr(preliminary, "config", None))
    parsed = parser.parse_args(config_args + argv)
    if preferences:
        saved_args = _preference_args(parsed)
        if saved_args:
            parsed = parser.parse_args(config_args + saved_args + argv)
    return parsed
