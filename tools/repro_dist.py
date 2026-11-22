#!/usr/bin/env python3
"""Normalize built distribution artifacts in ``dist/`` to canonical bytes.

``uv build`` stamps per-build metadata into every artifact, so two builds of the
same source differ byte-for-byte:

* wheels carry a wheel-record zip whose member timestamps (and platform
  ``create_system`` field) come from the host clock;
* sdists are gzipped tarballs whose gzip header mtime and tar member mtimes
  follow wall-clock time;
* the generated metadata files (wheel ``METADATA``/``WHEEL``/``RECORD``/
  ``entry_points.txt``/``top_level.txt``, sdist ``PKG-INFO``) are written by the
  build backend in the host's native text mode, so a Windows build ships them as
  CRLF while CI (Linux) ships LF.

All three make local builds differ from CI's and cause byte-identical re-uploads
to be rejected as duplicates on PyPI. This script makes every artifact in
``dist/`` (``*.whl`` and ``*.tar.gz``) deterministic regardless of host clock,
platform, or uv version: it fixes every timestamp to a fixed epoch, converts the
generated metadata files to LF, and rebuilds the wheel RECORD so its member
hashes/sizes stay valid. Because each package member's *content* is otherwise
preserved, installed bytes match PyPI byte-for-byte.

It is intentionally stdlib-only and idempotent: running it twice yields identical
bytes, and because it preserves each package member's content the wheel RECORD and
sdist internal hashes stay valid. Wire it in after every ``uv build`` (dev-check,
CI build jobs, PyPI publish) so local, CI and release builds match byte-for-byte.

Usage: ``python tools/repro_dist.py``  (reads/writes ``./dist``).
Honours ``SOURCE_DATE_EPOCH`` when set; otherwise pins a fixed repo epoch.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import os
import tarfile
import zipfile
from pathlib import Path

# ZIP stores timestamps as DOS format, which only supports dates from 1980-01-01;
# the tar/gzip sides use EPOCH directly. These are independent and both fixed.
EPOCH = int(os.environ.get("SOURCE_DATE_EPOCH", "1700000000"))
DATE_TIME = (1980, 1, 1, 0, 0, 0)
ZIP_VERSION_20 = getattr(zipfile, "ZIP_VERSION_20", 45)


# Metadata files a build backend generates in native text mode (CRLF on Windows).
# Only these are normalized to LF; binaries and already-LF source are left byte-identical.
def _metadata_name(base: str) -> bool:
    return base in {"METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD"}


def _maybe_lf(name: str, data: bytes) -> bytes:
    """Convert CRLF->LF for generated metadata text; pass every other byte through."""
    if _metadata_name(name.rsplit("/", 1)[-1]) or name.endswith("/PKG-INFO"):
        return data.replace(b"\r\n", b"\n")
    return data


def _b64_nopad(digest: bytes) -> str:
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _canonical_wheel(path: Path) -> None:
    """Re-zip a wheel with fixed mtimes, LF metadata text, and a valid RECORD.

    Member *content* is otherwise preserved exactly. Only zip bookkeeping
    (date_time, create_system/create_version), the generated metadata line endings,
    and the self-record are normalized. RECORD is rebuilt from the normalized so its
    SHA-256 sizes/hashes stay consistent with the bytes on disk.
    """
    with zipfile.ZipFile(path) as src:
        entries = [(zi, src.read(zi)) for zi in src.infolist()]

    tmp = path.with_suffix(path.suffix + ".tmp")
    written: list[tuple[str, bytes]] = []  # (name, normalized bytes), zip order minus RECORD
    rec_self = None
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for zi, data in entries:
            if zi.filename.endswith("dist-info/RECORD"):
                rec_self = zi.filename
                continue  # rebuilt below from the normalized members
            norm = _maybe_lf(zi.filename, data)
            info = zipfile.ZipInfo(zi.filename, date_time=DATE_TIME)
            info.compress_type = zi.compress_type
            info.external_attr = zi.external_attr & 0xFFFF0000  # keep file mode bits
            info.external_attr |= 0x10 << 16  # 0o100000: regular POSIX file
            info.create_system = 3  # Unix, host-independent
            info.create_version = ZIP_VERSION_20
            dst.writestr(info, norm)
            written.append((zi.filename, norm))
        rec_lines = [
            f"{name},sha256={_b64_nopad(hashlib.sha256(data).digest())},{len(data)}"
            for name, data in written
        ]
        rec_content = ("\n".join(rec_lines) + f"\n{rec_self},,\n").encode("utf-8")
        info = zipfile.ZipInfo(rec_self, date_time=DATE_TIME)
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o100644 << 16
        info.create_system = 3
        info.create_version = ZIP_VERSION_20
        dst.writestr(info, rec_content)
    tmp.replace(path)


def _canonical_sdist(path: Path) -> None:
    """Re-tar + re-gzip an sdist with a fixed epoch for every timestamp.

    Member order is sorted by name so the output does not depend on how uv laid out
    the tarball. Generated metadata (``PKG-INFO``) is converted to LF; all other
    members are byte-preserved.
    """
    raw = path.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as src:
        # Snapshot member data while the archive is still open.
        members = [(m, src.extractfile(m).read() if m.isfile() else None)
                   for m in src.getmembers()]
    members.sort(key=lambda md: md[0].name)  # ignore host layout order

    def _reset(member: tarfile.TarInfo) -> tarfile.TarInfo:
        member.mtime = EPOCH
        member.uid = member.gid = 0
        member.uname = member.gname = ""
        return member

    body = io.BytesIO()
    # USTAR avoids PAX extended headers, whose per-record content varies across
    # builds; all our sdist paths are short ASCII names it can represent directly.
    with tarfile.open(fileobj=body, mode="w", format=tarfile.USTAR_FORMAT) as dst:
        for member, data in members:
            reset = _reset(member)
            payload = _maybe_lf(member.name, data)
            # Normalizing PKG-INFO (CRLF->LF) changes the byte length; keep size so
            # USTAR framing stays consistent with the payload actually written.
            if data is not None and reset.isfile():
                reset.size = len(payload)
                add_src = io.BytesIO(payload)
            else:
                add_src = None
            dst.addfile(reset, add_src)

    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        gz.write(body.getvalue())
    path.write_bytes(buf.getvalue())


def main() -> int:
    dist = Path("dist")
    if not dist.is_dir():
        print(f"[repro_dist] no {dist}/ directory; nothing to normalize")
        return 0

    normalized: list[str] = []
    for path in sorted(dist.glob("*.whl")) + sorted(dist.glob("*.tar.gz")):
        _canonical_wheel(path) if path.suffix == ".whl" else _canonical_sdist(path)
        normalized.append(path.name)

    print(f"[repro_dist] normalized {len(normalized)} artifact(s): {', '.join(normalized)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
