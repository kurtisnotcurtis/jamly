"""Build redistributable ffmpeg/ffprobe from checksum-pinned source, on the target OS."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCES_FILE = Path(__file__).with_name("native-sources.json")
SOURCES = json.loads(SOURCES_FILE.read_text())


def sndfile_library(prefix: Path) -> Path:
    if os.name == "nt":
        return prefix / "bin" / "libsndfile-1.dll"
    return prefix / "lib" / ("libsndfile.1.dylib" if sys.platform == "darwin" else "libsndfile.so.1")


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_transient_network_error(err: Exception) -> bool:
    """True for transient network errors worth retrying a source download.

    urllib wraps OSError (including socket timeouts and mirror connection
    refusals) in URLError, so the wrapped reason must be inspected rather than
    the outer exception type.
    """
    if isinstance(err, HTTPError):
        return err.code in (408, 429, 500, 502, 503, 504)
    reason = err.reason if isinstance(err, URLError) else err
    return isinstance(
        reason,
        (
            TimeoutError,
            ConnectionError,
        ),
    )


def download_source(name: str, cache: Path) -> Path:
    info = SOURCES[name]
    archive = cache / Path(urlsplit(info["url"]).path).name
    cache.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        if checksum(archive) != info["sha256"]:
            raise RuntimeError("Cached source checksum mismatch: " + str(archive))
        return archive
    temporary = archive.with_suffix(archive.suffix + ".partial")
    try:
        for attempt in range(3):
            candidate_url = info["url"]
            if attempt > 0 and "//downloads.sourceforge.net/project/" in candidate_url:
                candidate_url = (
                    candidate_url.replace(
                        "//downloads.sourceforge.net/project/",
                        "//master.dl.sourceforge.net/project/",
                    )
                    + "?viasf=1"
                )
            try:
                with urlopen(candidate_url, timeout=120) as response:
                    if urlsplit(response.url).scheme != "https":
                        raise RuntimeError("Refusing an insecure source download")
                    with temporary.open("wb") as file:
                        shutil.copyfileobj(response, file)
                break
            except (URLError, TimeoutError, ConnectionError) as err:
                # Retry on transient network errors: timeouts, connection refusals/resets, and 5xx mirror errors.
                if not (attempt + 1 < 3 and _is_transient_network_error(err)):
                    raise
                time.sleep(2 ** attempt)
        if checksum(temporary) != info["sha256"]:
            raise RuntimeError("Source checksum mismatch: " + info["url"])
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    return archive


def default_jobs() -> int:
    return max(1, os.cpu_count() or 4)


# Jamly only decodes audio (plus embedded cover art), resamples, measures peaks, and encodes
# MP3/PCM. Restricting FFmpeg to those components cuts native build time substantially.
FFMPEG_ENCODERS = (
    "libmp3lame", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le",
    "aac", "flac", "alac", "opus", "vorbis", "wrapped_avframe", "mjpeg", "png",
)
FFMPEG_DECODERS = (
    "mp1*", "mp2*", "mp3*", "aac*", "opus", "vorbis", "flac", "alac", "pcm_*",
    "wmav1", "wmav2", "wmapro", "wmalossless", "wmavoice", "ac3*", "eac3*", "dca",
    "amrnb", "amrwb", "adpcm_*", "ape", "tta", "wavpack", "tak", "mlp", "truehd",
    "dsd_*", "gsm*", "speex", "mpc7", "mpc8", "shorten", "atrac*", "cook",
    "real_144", "real_288", "mjpeg", "png",
)
FFMPEG_FILTERS = (
    "abuffer", "abuffersink", "acopy", "aformat", "anull", "anullsink", "aresample",
    "astats", "atrim", "channelmap", "pan", "volume", "buffer", "buffersink",
    "format", "null", "scale", "copy",
)
FFMPEG_BSFS = ("aac_adtstoasc", "extract_extradata", "null", "opus_metadata", "pcm_rechunk")


def ffmpeg_component_flags() -> list[str]:
    return [
        "--disable-avdevice", "--disable-hwaccels", "--disable-indevs", "--disable-outdevs",
        "--disable-encoders", "--enable-encoder=" + ",".join(FFMPEG_ENCODERS),
        "--disable-decoders", "--enable-decoder=" + ",".join(FFMPEG_DECODERS),
        "--disable-filters", "--enable-filter=" + ",".join(FFMPEG_FILTERS),
        "--disable-bsfs", "--enable-bsf=" + ",".join(FFMPEG_BSFS),
    ]


def _prune_build_tree(work: Path, prefix: Path) -> None:
    """Remove intermediate C build trees and static archives so caches stay compact."""
    for directory in (work / "source", prefix / "include", prefix / "share", prefix / "lib" / "pkgconfig"):
        if directory.exists():
            shutil.rmtree(directory)
    lib_dir = prefix / "lib"
    if lib_dir.is_dir():
        for artifact in list(lib_dir.glob("*.a")) + list(lib_dir.glob("*.la")):
            artifact.unlink(missing_ok=True)


def build(work: Path, jobs: int | None = None, *, rebuild: bool = False) -> Path:
    jobs = default_jobs() if jobs is None else max(1, jobs)
    work = work.resolve()
    marker = work / ".jamly-native-build"
    if work.exists() and any(work.iterdir()) and not marker.exists():
        raise ValueError("Choose an empty native-build directory: " + str(work))
    work.mkdir(parents=True, exist_ok=True)
    marker.touch()
    platform_key = sys.platform + "-" + platform.machine().lower()
    targets = {
        "darwin-arm64": ("darwin64-arm64-cc", "aarch64-apple-darwin"),
        "linux-x86_64": ("linux-x86_64", "x86_64-pc-linux-gnu"),
        "win32-amd64": ("mingw64", "x86_64-w64-mingw32"),
    }
    if platform_key not in targets:
        raise ValueError("Unsupported desktop build target: " + platform_key)
    identity = {
        "platform": platform_key, "sources": checksum(SOURCES_FILE),
        "builder": checksum(Path(__file__)),
    }
    prefix = work / "install"
    suffix = ".exe" if os.name == "nt" else ""
    executables = [prefix / "bin" / (name + suffix) for name in ("ffmpeg", "ffprobe")]
    binaries = executables + [sndfile_library(prefix)]
    stamp = work / "built.json"
    if not rebuild and stamp.exists():
        saved = json.loads(stamp.read_text())
        if (saved.get("inputs") == identity and all(path.is_file() for path in binaries)
                and saved.get("binaries") == {path.name: checksum(path) for path in binaries}):
            return prefix

    shell = os.environ.get("JAMLY_BUILD_SHELL") or shutil.which("bash")
    if not shell:
        raise RuntimeError("Building desktop audio tools requires Bash and a C build toolchain")
    base_env = dict(os.environ)
    if sys.platform == "darwin":
        base_env["MACOSX_DEPLOYMENT_TARGET"] = "13.0"
    log_path = work / "build.log"
    log_path.write_text("", encoding="utf-8")
    log_lock = threading.Lock()
    install_lock = threading.Lock()

    def posix(path: Path) -> str:
        if os.name != "nt":
            return str(path)
        return subprocess.check_output(
            [shell, "--noprofile", "--norc", "-c", 'cygpath -u "$1"', "jamly", str(path)],
            text=True,
        ).strip()

    def execute(command: list[str], cwd: Path, *, env: dict[str, str]):
        result = subprocess.run(
            [shell, "--noprofile", "--norc", "-c", shlex.join(command)],
            cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        with log_lock:
            with log_path.open("a", encoding="utf-8") as log:
                log.write("\n" + shlex.join(command) + "\n")
                if result.stdout:
                    log.write(result.stdout)
                log.flush()
        if result.returncode:
            tail = "\n".join((result.stdout or "").splitlines()[-60:])
            raise RuntimeError("Native build failed; see %s\n%s" % (log_path, tail))

    for generated in (prefix, work / "source"):
        if generated.exists():
            shutil.rmtree(generated)
    source = work / "source"
    source.mkdir()

    def fetch_and_extract(name: str) -> None:
        archive = download_source(name, work / "downloads")
        with tarfile.open(archive) as file:
            file.extractall(source, filter="data")

    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in [pool.submit(fetch_and_extract, name) for name in ("openssl", "lame", "ffmpeg", "libsndfile")]:
            future.result()

    root = posix(prefix)
    base_env["PKG_CONFIG_PATH"] = root + "/lib/pkgconfig"
    base_env["CFLAGS"] = "-O2 -fPIC"
    openssl_target, host = targets[platform_key]
    parallel = "-j%d" % jobs
    side_parallel = "-j%d" % max(1, jobs // 2)

    def build_openssl() -> None:
        env = dict(base_env)
        directory = source / SOURCES["openssl"]["directory"]
        execute(["perl", "Configure", openssl_target, "no-shared", "no-tests", "no-module",
                 "no-apps", "no-docs", "no-legacy", "--prefix=" + root, "--libdir=lib"],
                directory, env=env)
        execute(["make", parallel, "build_libs"], directory, env=env)
        with install_lock:
            execute(["make", "install_dev"], directory, env=env)

    def build_lame() -> None:
        env = dict(base_env)
        directory = source / SOURCES["lame"]["directory"]
        execute(["./configure", "--prefix=" + root, "--build=" + host, "--host=" + host,
                 "--disable-shared", "--enable-static", "--disable-frontend"],
                directory, env=env)
        execute(["make", side_parallel], directory, env=env)
        with install_lock:
            execute(["make", "install"], directory, env=env)

    def build_libsndfile() -> None:
        env = dict(base_env)
        if os.name == "nt":
            env["CFLAGS"] += " -std=gnu11"
        directory = source / SOURCES["libsndfile"]["directory"]
        execute(["./configure", "--prefix=" + root, "--build=" + host, "--host=" + host,
                 "--disable-static", "--enable-shared", "--disable-external-libs",
                 "--disable-mpeg", "--disable-alsa", "--disable-sqlite", "--disable-full-suite"],
                directory, env=env)
        execute(["make", side_parallel], directory, env=env)
        with install_lock:
            execute(["make", "install"], directory, env=env)

    def build_ffmpeg() -> None:
        env = dict(base_env)
        directory = source / SOURCES["ffmpeg"]["directory"]
        flags = [
            "./configure", "--prefix=" + root, "--disable-autodetect", "--disable-shared",
            "--enable-static", "--disable-doc", "--disable-debug", "--disable-ffplay",
            "--disable-x86asm", "--enable-libmp3lame", "--enable-openssl", "--enable-version3",
            "--pkg-config-flags=--static", "--extra-cflags=-I" + root + "/include",
            "--extra-ldflags=-L" + root + "/lib", *ffmpeg_component_flags(),
        ]
        if os.name == "nt":
            flags.extend(("--target-os=mingw32", "--arch=x86_64", "--disable-pthreads", "--enable-w32threads"))
        execute(flags, directory, env=env)
        execute(["make", parallel], directory, env=env)
        with install_lock:
            execute(["make", "install"], directory, env=env)

    print("Building pinned audio dependencies for " + platform_key, flush=True)
    # OpenSSL, LAME, and libsndfile are independent; overlapping their configure/compile phases
    # avoids leaving cores idle, especially during single-threaded MSYS2 configure scripts.
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_openssl = pool.submit(build_openssl)
        f_lame = pool.submit(build_lame)
        f_sndfile = pool.submit(build_libsndfile)
        f_openssl.result()
        f_lame.result()
        build_ffmpeg()
        f_sndfile.result()

    for binary in executables:
        subprocess.run([str(binary), "-version"], check=True, stdout=subprocess.DEVNULL)
    stamp.write_text(json.dumps({
        "inputs": identity, "binaries": {path.name: checksum(path) for path in binaries},
    }, indent=2))
    _prune_build_tree(work, prefix)
    return prefix


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build" / "native-audio")
    parser.add_argument("--jobs", type=int, default=default_jobs())
    options = parser.parse_args()
    print(build(options.work_dir, options.jobs))
