#!/usr/bin/env python3
"""Build the release-pinned MU300 native kmod capability provider APK.

This emits one unsigned APK v2 package for inclusion in its matching firmware image.
It never installs packages, changes an APK database, or adds kernel modules.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile


PKGNAME = "mu300-native-kmod-compat"
PKGVER = "7.2.9-r1"
PKGARCH = "aarch64_generic"
PKGDESC = "U30 Air native 7.2.9 kmod compatibility capabilities"
URL = "https://github.com/oswaldxdd/mu300-linux"
RELEASE_ID = "v2026.10.15-charge2-r2"
PROVIDES = (
    "kmod-dummy=7.2.9-r1",
    "kmod-inet-diag=7.2.9-r1",
    "kmod-nft-socket=7.2.9-r1",
    "kmod-nft-tproxy=7.2.9-r1",
)
PAYLOAD = {
    "usr/share/doc/mu300-native-kmod-compat/README": "docs/native-kmod-compat/README.md",
    "usr/share/doc/mu300-native-kmod-compat/capability-manifest.txt":
        "docs/native-kmod-compat/capability-manifest.txt",
}
GUARD_REL = "tools/native-kmod-compat/preinstall-guard.sh"
GUARD_TOKENS = (
    b"7.2.9-u30air-charge2",
    b"EXPECTED_VERMAGIC='7.2.9-u30air-charge2 SMP preempt mod_unload aarch64'",
    b"check_config NF_SOCKET_IPV4 y",
    b"check_config NF_SOCKET_IPV6 y",
    b"modinfo -F vermagic",
    b"strings \"$module\"",
    b"/^vermagic=/",
    b"count != 1",
    b"13dceaac167531650262f8d782b4e79df9a93cd6c8efd45dfc7bcbba0690ccce",
    b"81b6171e6c8b3ca081b08f83a9df50d87ac7a672b03667ee5bb93445ff414aa4",
    b"259fb82e25c352cfff6dadeabd80f3d61b74d08fa5d977a9efd903b594a775c1",
)
SOURCE_COMMIT_TOKEN = "@@SOURCE_COMMIT@@"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"error: {message}")


def read_source_file(root: Path, relative: str) -> bytes:
    path = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            fail(f"source input path contains a symlink: {current}")
    if path.is_symlink() or not path.is_file():
        fail(f"source input must be a regular file: {path}")
    return path.read_bytes()


def verify_source_snapshot(source: Path, source_commit: str, source_date_epoch: int) -> None:
    marker = source / ".mu300-test-source.json"
    if marker.is_symlink() or not marker.is_file():
        fail(f"source root is not an exported frozen snapshot: missing {marker}")
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read frozen source marker {marker}: {exc}")
    if metadata.get("source_commit") != source_commit:
        fail("--source-commit does not match the frozen source marker")
    if metadata.get("source_date_epoch") != source_date_epoch:
        fail("--source-date-epoch does not match the frozen source marker")


def render_manifest(data: bytes, source_commit: str) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"capability manifest is not UTF-8: {exc}")
    if text.count(SOURCE_COMMIT_TOKEN) != 1:
        fail("capability manifest must contain exactly one source commit placeholder")
    text = text.replace(SOURCE_COMMIT_TOKEN, source_commit)
    if "@@" in text:
        fail("capability manifest contains an unresolved placeholder")
    return text.encode("utf-8")


def read_payload(source: Path, source_commit: str) -> list[tuple[str, bytes, int, bool]]:
    files: dict[str, bytes] = {}
    for package_path, source_rel in PAYLOAD.items():
        data = read_source_file(source, source_rel)
        if source_rel.endswith("capability-manifest.txt"):
            data = render_manifest(data, source_commit)
        if not data:
            fail(f"package payload must be non-empty: {source_rel}")
        files[package_path] = data

    entries: list[tuple[str, bytes, int, bool]] = []
    directories = {
        PurePosixPath(name).parents[i].as_posix()
        for name in files
        for i in range(len(PurePosixPath(name).parents) - 1)
    }
    for directory in sorted(directories, key=lambda value: (value.count("/"), value)):
        entries.append((directory.rstrip("/") + "/", b"", 0o755, True))
    for rel in sorted(files):
        entries.append((rel, files[rel], 0o644, False))
    return entries


def read_guard(source: Path) -> bytes:
    path = source / GUARD_REL
    data = read_source_file(source, GUARD_REL)
    if not data.startswith(b"#!/bin/sh\n"):
        fail(f"guard must start with #!/bin/sh and use LF line endings: {path}")
    if b"\r" in data:
        fail(f"guard must use LF line endings: {path}")
    missing = [token.decode("ascii") for token in GUARD_TOKENS if token not in data]
    if missing:
        fail(f"guard is missing required strict check(s): {', '.join(missing)}")
    return data


def tar_stream(
    entries: list[tuple[str, bytes, int, bool]],
    *,
    epoch: int,
    cut_end_marker: bool,
    hash_regular_files: bool,
) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data, mode, is_dir in entries:
            member = tarfile.TarInfo(name)
            member.uid = 0
            member.gid = 0
            member.uname = "root"
            member.gname = "root"
            member.mode = mode
            member.mtime = epoch
            member.pax_headers = {}
            if is_dir:
                member.type = tarfile.DIRTYPE
                member.size = 0
                archive.addfile(member)
            else:
                member.type = tarfile.REGTYPE
                member.size = len(data)
                if hash_regular_files:
                    member.pax_headers["APK-TOOLS.checksum.SHA1"] = hashlib.sha1(data).hexdigest()
                archive.addfile(member, io.BytesIO(data))
        end_of_members = archive.offset
    tar_data = raw.getvalue()
    if cut_end_marker:
        tar_data = tar_data[:end_of_members]
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=compressed, mode="wb", compresslevel=9, mtime=0) as stream:
        stream.write(tar_data)
    return compressed.getvalue()


def package_info(*, size: int, datahash: str, epoch: int, source_commit: str) -> bytes:
    lines = [
        "# Generated by build-native-kmod-compat-apk.py; release-local provider package",
        f"pkgname = {PKGNAME}",
        f"pkgver = {PKGVER}",
        f"pkgdesc = {PKGDESC}",
        f"url = {URL}",
        f"builddate = {epoch}",
        f"packager = MU300 firmware build {RELEASE_ID} ({source_commit[:12]})",
        f"size = {size}",
        f"arch = {PKGARCH}",
        f"origin = {PKGNAME}",
        "depend = busybox",
    ]
    lines.extend(f"provides = {value}" for value in PROVIDES)
    lines.append(f"datahash = {datahash}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def build(source: Path, *, source_commit: str, source_date_epoch: int) -> bytes:
    entries = read_payload(source, source_commit)
    guard = read_guard(source)
    payload_size = sum(len(data) for _, data, _, is_dir in entries if not is_dir)
    data_stream = tar_stream(entries, epoch=source_date_epoch, cut_end_marker=False,
                              hash_regular_files=True)
    datahash = hashlib.sha256(data_stream).hexdigest()
    pkginfo = package_info(size=payload_size, datahash=datahash,
                           epoch=source_date_epoch, source_commit=source_commit)
    control_entries = [
        (".PKGINFO", pkginfo, 0o644, False),
        (".pre-install", guard, 0o755, False),
        (".pre-upgrade", guard, 0o755, False),
    ]
    control_stream = tar_stream(control_entries, epoch=source_date_epoch,
                                cut_end_marker=True, hash_regular_files=False)
    return control_stream + data_stream


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True,
                        help="frozen exported source snapshot root")
    parser.add_argument("--source-commit", required=True,
                        help="40-character Git commit recorded in the frozen source snapshot")
    parser.add_argument("--source-date-epoch", type=int, required=True,
                        help="commit timestamp of the frozen source snapshot")
    parser.add_argument("--output", type=Path, required=True,
                        help="new APK output path; existing paths are never overwritten")
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        fail("--source-commit must be a lowercase 40-character Git SHA")
    if args.source_date_epoch <= 0:
        fail("--source-date-epoch must be a positive timestamp")
    if args.source_root.is_symlink():
        fail("--source-root must not be a symlink")
    source = args.source_root.resolve(strict=True)
    if not source.is_dir():
        fail(f"--source-root is not a directory: {source}")
    verify_source_snapshot(source, args.source_commit, args.source_date_epoch)
    output = args.output.expanduser()
    if not output.is_absolute():
        output = Path.cwd() / output
    output = Path(os.path.abspath(output))
    if output.exists() or output.is_symlink():
        fail(f"refusing to overwrite output: {output}")

    package = build(source, source_commit=args.source_commit,
                    source_date_epoch=args.source_date_epoch)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("xb") as output_file:
            output_file.write(package)
            output_file.flush()
            os.fsync(output_file.fileno())
        output.chmod(0o644)
    except FileExistsError:
        fail(f"refusing to overwrite output: {output}")

    print(f"created unsigned APK v2 firmware provider: {output}")
    print(f"package={PKGNAME}-{PKGVER} arch={PKGARCH} release={RELEASE_ID}")
    print(f"source_commit={args.source_commit} source_date_epoch={args.source_date_epoch}")
    print(f"provides={','.join(PROVIDES)}")
    print("payload: documentation and capability manifest only; no kernel modules")
    print("pre-install/pre-upgrade guard remains enabled for device-side package operations")
    print(f"sha256={hashlib.sha256(package).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
