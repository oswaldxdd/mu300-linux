#!/usr/bin/env python3
"""Build a separately identified U30 Air charge2 test package from pinned inputs.

This is the test-package assembler. It does not compile the kernel. The candidate kernel
bundle must have been freshly built for the source snapshot and must match the pinned hash
below. Run on Linux or WSL with Python 3.12+, GNU tar/gzip/patch, qemu-user and the APK inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile


RELEASE_ID = "charge2-test1-20261012"
KREL = "7.2.9-u30air-charge2"
MODULE_COUNT = 379
PINS = {
    "official_openwrt_rootfs": "8ff2008f00b891c021b139e5f2cd225f54718957176a827c677c055560229eff",
    "official_rollback_kernel": "2f38180a0f417fbc0343e6773e8e458edbbe5293143fbab938952e516c6b58eb",
    "candidate_kernel": "fd87b29a0720331447d9b9fb2cad044cb8b190cc5b3c3c5670a513af039aa71e",
    "openwrt_base_index": "bd10218cc2d3c35b8957f55d60d5cedf6bd392aec7deafc5634c4acb4ffcd779",
    "ucode_mod_socket_apk": "e72f7d7f5017e3f300f006da37027f19cb2157178b4e88f851afb2e48e1cc35e",
}
APK_NAME = "ucode-mod-socket-2026.01.16~85922056-r2.apk"
APK_REPO = "https://downloads.openwrt.org/releases/25.12.5/packages/aarch64_generic/base/"
BASE_REPO = "https://downloads.openwrt.org/releases/25.12.5/packages/aarch64_generic/base/packages.adb"
MAIN_SERVICES = (
    "mu300-accounts", "mu300-vendor", "mu300-hw", "mu300-post", "mu300-toolkit",
    "mu300-atd", "mu300-modem-log", "mu300-wifi-client", "mu300-buttons",
    "mu300-cpu", "mu300-traffic", "mu300-power", "mu300-fcc-record", "mu300-user-packages",
)
LUCI_SERVICES = ("unisoc-modem-ui", "mu300-smsd", "mu300-atd-dash", "mu300-ndp")
DOC_RELEASE_ID = "@@MU300_CHARGE2_RELEASE_ID@@"
DOC_KERNEL_RELEASE = "@@MU300_CHARGE2_KERNEL_RELEASE@@"


def package_doc(source: Path, name: str) -> str:
    """Render a frozen-source Markdown template with this package's identifiers."""
    path = source / "docs" / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"package documentation template is missing: {path}") from exc
    text = text.replace(DOC_RELEASE_ID, RELEASE_ID).replace(DOC_KERNEL_RELEASE, KREL)
    if "@@MU300_CHARGE2_" in text:
        raise RuntimeError(f"unresolved package documentation placeholder in {path}")
    return text


def init_priority(init: Path, service: str, variable: str, *, required: bool) -> str | None:
    """Read one two-digit rc.common priority, allowing an optional trailing comment."""
    text = init.read_text(encoding="utf-8", errors="replace")
    assignments = re.findall(rf"(?m)^{variable}=.*$", text)
    if not assignments and not required:
        return None
    if len(assignments) != 1:
        raise RuntimeError(f"init service must have one {variable}= value: {service}")
    match = re.fullmatch(rf"{variable}=([0-9]{{2}})(?:\s+#.*)?\s*", assignments[0])
    if not match:
        raise RuntimeError(f"init service must have a two-digit {variable}= value: {service}")
    return match.group(1)


def ensure_init_link(rcdir: Path, prefix: str, priority: str, service: str) -> Path:
    """Create or retain one rc.d symlink; replace stale symlinks but refuse regular files."""
    link = rcdir / f"{prefix}{priority}{service}"
    target = Path("../init.d") / service
    if link.is_symlink():
        if os.readlink(link) == target.as_posix():
            return link
        link.unlink()
    elif link.exists():
        raise RuntimeError(f"refusing to replace non-symlink init link: {link}")
    link.symlink_to(target)
    return link


def enable_init_services(root: Path) -> None:
    """Enable boot links and optional shutdown links from rc.common priorities."""
    rcdir = root / "etc/rc.d"
    rcdir.mkdir(parents=True, exist_ok=True)
    for service in MAIN_SERVICES + LUCI_SERVICES:
        init = root / "etc/init.d" / service
        if not init.is_file():
            raise RuntimeError(f"required OpenWrt init service missing: {init}")
        if service in {"mu300-cpu", "mu300-traffic", "mu300-user-packages", "unisoc-modem-ui",
                       "mu300-smsd", "mu300-atd-dash", "mu300-ndp"}:
            init.chmod(0o755)
        if not os.access(init, os.X_OK):
            raise RuntimeError(f"OpenWrt init service is not executable: {init}")
        start = init_priority(init, service, "START", required=True)
        ensure_init_link(rcdir, "S", start, service)
        stop = init_priority(init, service, "STOP", required=False)
        if stop is not None:
            ensure_init_link(rcdir, "K", stop, service)

    fcc_link = rcdir / "S20mu300-fcc-record"
    if not fcc_link.is_symlink() or os.readlink(fcc_link) != "../init.d/mu300-fcc-record":
        raise RuntimeError("the U30 Air FCC service is not enabled at its START=20 boot link")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        stdout=None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, env=env, check=True, stdout=stdout)


def require_pin(path: Path, name: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"required input is missing: {path}")
    actual = sha256(path)
    expected = PINS[name]
    if actual != expected:
        raise RuntimeError(f"{name} SHA-256 mismatch: expected {expected}, got {actual}")


def file_mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def tree_hash(root: Path) -> str:
    """Hash exported source names, Git modes and contents, excluding our generated marker."""
    h = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        if rel == ".mu300-test-source.json" or path.is_dir():
            continue
        if path.is_symlink():
            kind, payload = b"L", os.readlink(path).encode()
        elif path.is_file():
            kind, payload = b"F", path.read_bytes()
        else:
            continue
        h.update(kind + b"\0" + rel.encode() + b"\0")
        h.update(f"{file_mode(path):04o}".encode() + b"\0")
        h.update(hashlib.sha256(payload).digest())
    return h.hexdigest()


def export_source(repo: Path | None, source_tree: Path | None, source_commit: str | None,
                  stage: Path, source_archive: Path | None = None,
                  source_metadata: Path | None = None) -> tuple[Path, str, int, str]:
    dest = stage / "source"
    if repo is not None:
        git = shutil.which("git")
        if not git:
            raise RuntimeError("git is required to export a clean source commit")
        status = subprocess.check_output(
            [git, "-C", str(repo), "status", "--porcelain", "--untracked-files=all"], text=True
        )
        if status.strip():
            raise RuntimeError("source worktree is dirty; commit the complete source and rerun")
        head = subprocess.check_output([git, "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        if source_commit and source_commit != head:
            raise RuntimeError(f"requested source commit {source_commit} is not HEAD ({head})")
        commit_time = int(subprocess.check_output(
            [git, "-C", str(repo), "show", "-s", "--format=%ct", head], text=True
        ).strip())
        snapshot = stage / "source.snapshot.tar"
        with snapshot.open("wb") as stream:
            run([git, "-C", str(repo), "archive", "--format=tar", head], stdout=stream)
        dest.mkdir()
        with tarfile.open(snapshot, "r:") as archive:
            for member in archive.getmembers():
                name = Path(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise RuntimeError(f"unsafe source archive member: {member.name}")
            archive.extractall(dest, filter="data")
        snapshot_hash = sha256(snapshot)
    elif source_archive is not None:
        if source_metadata is None or not source_metadata.is_file():
            raise RuntimeError("--source-archive requires the clean Git export metadata JSON")
        metadata = json.loads(source_metadata.read_text(encoding="utf-8"))
        if metadata.get("source_worktree_clean") is not True:
            raise RuntimeError("source archive metadata does not confirm a clean committed worktree")
        snapshot_hash = sha256(source_archive)
        if snapshot_hash != metadata.get("source_snapshot_sha256"):
            raise RuntimeError("source archive does not match its export metadata SHA-256")
        head = str(metadata.get("source_commit", ""))
        commit_time = int(metadata.get("source_date_epoch", 0))
        if not re.fullmatch(r"[0-9a-f]{40}", head) or commit_time <= 0:
            raise RuntimeError("source export metadata has an invalid commit or timestamp")
        if source_commit and source_commit != head:
            raise RuntimeError(f"source archive is for {head}, not requested commit {source_commit}")
        dest.mkdir()
        with tarfile.open(source_archive, "r:") as archive:
            for member in archive.getmembers():
                name = Path(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise RuntimeError(f"unsafe source archive member: {member.name}")
            archive.extractall(dest, filter="data")
    else:
        if source_tree is None:
            raise RuntimeError("supply a clean Git checkout, --source-archive or --source-tree")
        source_tree = source_tree.resolve()
        marker = source_tree / ".mu300-test-source.json"
        if not marker.is_file():
            raise RuntimeError("--source-tree must be a previously exported package source tree")
        marker_data = json.loads(marker.read_text(encoding="utf-8"))
        head = str(marker_data["source_commit"])
        commit_time = int(marker_data["source_date_epoch"])
        expected_tree = str(marker_data["source_tree_sha256"])
        actual_tree = tree_hash(source_tree)
        if actual_tree != expected_tree:
            raise RuntimeError(f"exported source tree mismatch: expected {expected_tree}, got {actual_tree}")
        if source_commit and source_commit != head:
            raise RuntimeError(f"source tree is for {head}, not requested commit {source_commit}")
        shutil.copytree(source_tree, dest, symlinks=True, ignore=shutil.ignore_patterns(".mu300-test-source.json"))
        snapshot_hash = marker_data["source_snapshot_sha256"]

    # This package carries the active source tree, not frozen experimental release snapshots.
    shutil.rmtree(dest / "experimental", ignore_errors=True)
    if not (dest / "rootfs/overlay/opt/mu300/bin/mu300-update").is_file():
        raise RuntimeError("source snapshot is missing the charge2 updater")
    if not (dest / "tools/build-charge2-test.py").is_file():
        raise RuntimeError("source snapshot is missing tools/build-charge2-test.py")
    source_tree_sha = tree_hash(dest)
    marker_data = {
        "source_commit": head,
        "source_date_epoch": commit_time,
        "source_snapshot_sha256": snapshot_hash,
        "source_tree_sha256": source_tree_sha,
    }
    (dest / ".mu300-test-source.json").write_text(
        json.dumps(marker_data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return dest, head, commit_time, source_tree_sha


def verify_kernel_provenance(path: Path, source: Path, source_commit: str,
                             source_snapshot_sha256: str, evidence: Path) -> dict:
    if not path.is_file():
        raise RuntimeError(f"kernel build provenance is missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("source_commit") != source_commit:
        raise RuntimeError(
            f"kernel was built for source commit {data.get('source_commit')}, not {source_commit}"
        )
    if data.get("source_snapshot_sha256") != source_snapshot_sha256:
        raise RuntimeError("kernel build evidence refers to a different exported source archive")
    if data.get("candidate_kernel_sha256") != PINS["candidate_kernel"]:
        raise RuntimeError("kernel build provenance names a different candidate bundle hash")
    if data.get("kernel_release") != KREL or data.get("module_count") != MODULE_COUNT:
        raise RuntimeError("kernel build provenance has the wrong kernel release or module count")
    for required in ("kernel_source", "compiler", "config_sha256", "build_logs", "kernel_input_files"):
        if not data.get(required):
            raise RuntimeError(f"kernel build provenance is missing {required}")
    if not isinstance(data["build_logs"], dict):
        raise RuntimeError("build_logs must map log paths to SHA-256 values")
    for rel, expected in data["build_logs"].items():
        log = evidence / "logs" / rel
        if not log.is_file() or sha256(log) != expected:
            raise RuntimeError(f"kernel build log is missing or has changed: {rel}")
    files = data["kernel_input_files"]
    if not isinstance(files, list) or not files:
        raise RuntimeError("kernel_input_files must be a non-empty list")
    seen: set[str] = set()
    for record in files:
        rel = str(record.get("path", ""))
        if not rel.startswith("upstream/") or ".." in Path(rel).parts or rel in seen:
            raise RuntimeError(f"invalid or duplicate kernel input path: {rel!r}")
        seen.add(rel)
        build_hash = str(record.get("build_sha256", ""))
        committed_hash = str(record.get("commit_sha256", ""))
        source_file = source / rel
        if not source_file.is_file():
            raise RuntimeError(f"kernel input is absent from committed source: {rel}")
        actual = sha256(source_file)
        if not build_hash or build_hash != committed_hash or actual != committed_hash:
            raise RuntimeError(
                f"kernel build input differs from final source commit: {rel} "
                f"(build={build_hash}, commit={committed_hash}, source={actual})"
            )
    expected_files = {
        p.relative_to(source).as_posix()
        for p in (source / "upstream").rglob("*")
        if p.is_file() and not any(part.startswith("out-") for part in p.relative_to(source).parts)
    }
    if seen != expected_files:
        missing = sorted(expected_files - seen)[:5]
        extra = sorted(seen - expected_files)[:5]
        raise RuntimeError(f"kernel input manifest is incomplete (missing={missing}, extra={extra})")
    for prefix in ("upstream/modules/", "upstream/port/", "upstream/u30air/", "upstream/dts/", "upstream/patches/"):
        if not any(path.startswith(prefix) for path in seen):
            raise RuntimeError(f"kernel input manifest omits {prefix}")
    for required in ("upstream/build.sh", "upstream/build-modules.sh", "upstream/mu300-mainline.config",
                     "upstream/u30air/config"):
        if required not in seen:
            raise RuntimeError(f"kernel input manifest omits {required}")
    return data


def extract_kernel(bundle: Path, dest: Path) -> list[Path]:
    with tarfile.open(bundle, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise RuntimeError(f"unsafe kernel bundle member: {member.name}")
        archive.extractall(dest, filter="data")
    release = (dest / "kernel.release").read_text(encoding="ascii").strip()
    devices = (dest / "devices").read_text(encoding="ascii").split()
    if release != KREL or devices != ["u30air"]:
        raise RuntimeError(f"kernel metadata mismatch: release={release!r}, devices={devices!r}")
    modules = sorted((dest / "modules").glob("*.ko"))
    if len(modules) != MODULE_COUNT:
        raise RuntimeError(f"expected {MODULE_COUNT} kernel modules, found {len(modules)}")
    for module in modules:
        data = module.read_bytes()
        if len(data) < 20 or data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
            raise RuntimeError(f"not an AArch64 ELF module: {module.name}")
        match = re.search(rb"vermagic=([^ \x00]+)", data)
        if not match or match.group(1).decode("ascii", "replace") != KREL:
            raise RuntimeError(f"module vermagic mismatch: {module.name}")
    if not (dest / "Image").is_file() or not (dest / "ramdisk-generic.lz4").is_file():
        raise RuntimeError("kernel bundle is missing its Image or generic boot ramdisk")
    return modules


def copy_overlay(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"required overlay is missing: {source}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True, copy_function=shutil.copy2)


def apk_call(root: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    qemu = shutil.which("qemu-aarch64-static")
    if not qemu:
        raise RuntimeError("qemu-aarch64-static is required for the AArch64 APK tools")
    apk = root / "usr/bin/apk"
    return run([qemu, "-L", str(root), str(apk), "--root", str(root), *args], env=env)


def install_socket_apk(root: Path, apk_index: Path, apk_file: Path, work: Path) -> None:
    repo = root / "tmp/mu300-test-localrepo"
    repo.mkdir(parents=True, exist_ok=True)
    shutil.copy2(apk_index, repo / "packages.adb")
    shutil.copy2(apk_file, repo / APK_NAME)
    repo_file = root / "etc/apk/mu300-test-repositories"
    repo_file.write_text("file:///tmp/mu300-test-localrepo/packages.adb\n", encoding="ascii")
    env = os.environ.copy()
    env.update({"IPKG_NO_SCRIPT": "1", "LC_ALL": "C"})
    apk_call(root, ["--repositories-file", str(repo_file), "update"], env)
    apk_call(root, ["--repositories-file", str(repo_file), "--force-no-chroot", "--preserve-env", "add",
                    "ucode-mod-socket"], env)
    installed = subprocess.check_output(
        [shutil.which("qemu-aarch64-static"), "-L", str(root), str(root / "usr/bin/apk"),
         "--root", str(root), "info", "-e", "ucode-mod-socket"], env=env, text=True
    ).strip()
    if installed != "ucode-mod-socket":
        raise RuntimeError(f"APK did not report ucode-mod-socket installed: {installed!r}")
    if not (root / "usr/lib/ucode/socket.so").is_file():
        raise RuntimeError("ucode-mod-socket did not install usr/lib/ucode/socket.so")
    repo_file.unlink(missing_ok=True)
    shutil.rmtree(repo, ignore_errors=True)


NATIVE_KMOD_PROVIDER = "mu300-native-kmod-compat"
NATIVE_KMOD_PROVIDER_VERSION = "7.2.9-r1"
NATIVE_KMOD_PROVIDES = (
    "kmod-dummy=7.2.9-r1",
    "kmod-inet-diag=7.2.9-r1",
    "kmod-nft-socket=7.2.9-r1",
    "kmod-nft-tproxy=7.2.9-r1",
)


def install_native_provider_apk(root: Path, apk_file: Path) -> None:
    """Register the firmware-local, unsigned provider without executing package scripts."""
    env = os.environ.copy()
    env.update({"IPKG_NO_SCRIPT": "1", "LC_ALL": "C"})
    # apk-tools 3.0.5 accepts `no` for its BOOL option; `false` is rejected.
    apk_call(root, ["--allow-untrusted", "--scripts=no", "add", str(apk_file)], env)

    installed_db = root / "lib/apk/db/installed"
    records = installed_db.read_text(encoding="utf-8", errors="replace").split("\n\n")
    record = next((item for item in records
                   if f"\nP:{NATIVE_KMOD_PROVIDER}\n" in f"\n{item}\n"), None)
    if record is None:
        raise RuntimeError("native kmod provider is absent from the APK installed database")
    lines = set(record.splitlines())
    if f"V:{NATIVE_KMOD_PROVIDER_VERSION}" not in lines or "A:aarch64_generic" not in lines:
        raise RuntimeError("native kmod provider version or architecture differs from the frozen package")
    # APK stores package names as uppercase P: and versioned virtual provides
    # as lowercase p: records. A single p: record may contain several tokens.
    provides = {
        token
        for line in lines
        if line.startswith("p:")
        for token in line[2:].split()
    }
    missing = sorted(set(NATIVE_KMOD_PROVIDES) - provides)
    if missing:
        raise RuntimeError(f"native kmod provider is missing installed provides records: {missing}")

    # Ask APK's own solver to resolve every exact versioned capability from this rootfs DB.
    for capability in NATIVE_KMOD_PROVIDES:
        apk_call(root, ["--no-network", "--simulate", "add", capability], env)

    installed = subprocess.check_output(
        [shutil.which("qemu-aarch64-static"), "-L", str(root), str(root / "usr/bin/apk"),
         "--root", str(root), "info", "-e", NATIVE_KMOD_PROVIDER],
        env=env, text=True,
    ).strip()
    if installed != NATIVE_KMOD_PROVIDER:
        raise RuntimeError(f"APK did not report the native provider installed: {installed!r}")


def patch_if_needed(root: Path, source: Path, rel: str, marker: str) -> None:
    target = root / rel
    if target.is_file() and marker in target.read_text(encoding="utf-8", errors="replace"):
        return
    patch_file = source / "openwrt/patches" / ("wifi-scripts-no-dfs.patch" if "hostapd" in rel else "fw4-sipa-offload.patch")
    run(["patch", "--batch", "--fuzz=0", "-d", str(root), "-p1", "-i", str(patch_file)])
    if not target.is_file() or marker not in target.read_text(encoding="utf-8", errors="replace"):
        raise RuntimeError(f"required OpenWrt patch did not apply: {patch_file.name}")


def make_rootfs(source: Path, rootfs_base: Path, kernel: Path, modules: list[Path],
                apk_index: Path, apk_file: Path, stage: Path,
                native_provider_apk: Path | None = None) -> tuple[Path, list[str]]:
    root = stage / "rootfs"
    root.mkdir()
    run(["tar", "-xzf", str(rootfs_base), "-C", str(root)])

    # Apply the complete active charge2 runtime. Ubuntu-only systemd and udev assets are deliberately excluded.
    copy_overlay(source / "rootfs/overlay/opt/mu300", root / "opt/mu300")
    copy_overlay(source / "openwrt/overlay", root)
    copy_overlay(source / "openwrt/luci-overlay", root)
    luci = source / "openwrt/luci-app-mu300"
    copy_overlay(luci / "root", root)
    copy_overlay(luci / "htdocs", root / "www")

    for rel, marker in (
        ("usr/share/ucode/fw4.uc", "sipa_eth0"),
        ("usr/share/ucode/wifi/hostapd.uc", "device_no_dfs(config);"),
    ):
        patch_if_needed(root, source, rel, marker)

    lmo_dir = root / "usr/lib/lua/luci/i18n"
    lmo_dir.mkdir(parents=True, exist_ok=True)
    languages = {line.split("\t", 1)[0]: line.split("\t", 1)[1].strip()
                 for line in (source / "openwrt/luci-languages.tsv").read_text(encoding="utf-8").splitlines()
                 if "\t" in line}
    for language in ("tr", "zh_Hans"):
        code = languages.get(language)
        if not code:
            raise RuntimeError(f"no LuCI language mapping for {language}")
        output = lmo_dir / f"mu300.{code}.lmo"
        run([sys.executable, str(source / "tools/po2lmo.py"),
             str(luci / f"po/{language}/mu300.po"), str(output)])
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"empty LuCI translation catalog: {output.name}")
        output.chmod(0o644)

    # The release branch ships the Aurora theme and language catalogs in its pinned base image.
    if not (root / "www/luci-static/aurora/main.css").is_file():
        raise RuntimeError("pinned OpenWrt rootfs is missing Aurora theme assets")
    for lang in ("tr", "zh-cn"):
        if not (lmo_dir / f"base.{lang}.lmo").is_file():
            raise RuntimeError(f"pinned OpenWrt rootfs is missing LuCI base catalog {lang}")
    for firmware in ("regulatory.db", "regulatory.db.p7s"):
        if not (root / "lib/firmware" / firmware).is_file():
            raise RuntimeError(f"pinned OpenWrt rootfs is missing /lib/firmware/{firmware}")

    install_socket_apk(root, apk_index, apk_file, stage)
    if native_provider_apk is not None:
        install_native_provider_apk(root, native_provider_apk)
    (root / "etc/mu300").mkdir(parents=True, exist_ok=True)
    (root / "etc/mu300/image-version").write_text(RELEASE_ID + "\n", encoding="ascii")

    # OpenWrt 25.12 has the previous kernel's modules in its base image. This test image boots only 7.2.9.
    shutil.rmtree(root / "lib/modules", ignore_errors=True)
    (root / "lib/modules" / KREL).mkdir(parents=True)
    for module in modules:
        shutil.copy2(module, root / "lib/modules" / KREL / module.name)
    for metadata in ("modules.builtin", "modules.builtin.modinfo"):
        if (kernel / metadata).is_file():
            shutil.copy2(kernel / metadata, root / "lib/modules" / KREL / metadata)
    shutil.rmtree(root / "boot", ignore_errors=True)

    # Preserve the project's MU300 sysupgrade entry point and disable handlers owned by mu300-buttons.
    sysupgrade = root / "sbin/sysupgrade"
    mu300_sysupgrade = root / "usr/libexec/mu300-sysupgrade"
    if not mu300_sysupgrade.is_file():
        raise RuntimeError("OpenWrt MU300 sysupgrade helper is missing")
    if sysupgrade.exists() or sysupgrade.is_symlink():
        sysupgrade.rename(root / "sbin/sysupgrade.openwrt")
    shutil.copy2(mu300_sysupgrade, sysupgrade)
    sysupgrade.chmod(0o755)
    button_dir = root / "etc/rc.button"
    button_dir.mkdir(parents=True, exist_ok=True)
    for button in ("power", "wps", "rfkill"):
        path = button_dir / button
        path.write_text("#!/bin/sh\n# mu300-buttons owns the keys.\nexit 0\n", encoding="ascii")
        path.chmod(0o755)

    # Init enablement mirrors device-specific boot order and applies declared STOP values on shutdown.
    enable_init_services(root)

    # Runtime scripts whose Git executable bit is required by procd/LuCI.
    executables = (
        "opt/mu300/bin/mu300-user-packages", "opt/mu300/bin/mu300-sms",
        "opt/mu300/bin/mu300-smsd", "opt/mu300/bin/ra-deprecate",
        "etc/hotplug.d/ntp/20-mu300-clock", "usr/libexec/rpcd/mu300dash",
    )
    for rel in executables:
        path = root / rel
        if not path.is_file():
            raise RuntimeError(f"required runtime executable missing: /{rel}")
        path.chmod(0o755)
    for path in sorted((root / "usr/libexec/unisoc-modem").iterdir()):
        if path.is_file() and not path.is_symlink():
            path.chmod(0o755)
    if not (root / "etc/hotplug.d/ntp/20-mu300-clock").is_file():
        raise RuntimeError("required OpenWrt NTP hook is missing")
    if (root / "etc/systemd").exists() or (root / "etc/udev").exists():
        raise RuntimeError("Ubuntu systemd or udev assets leaked into the OpenWrt rootfs")

    # Every documented command must resolve from PATH in OpenWrt.
    command_file = root / "opt/mu300/lib/path-commands"
    if not command_file.is_file():
        raise RuntimeError("MU300 path-commands list is missing")
    command_names = [line.strip() for line in command_file.read_text(encoding="utf-8").splitlines()
                     if line.strip() and not line.lstrip().startswith("#")]
    for name in command_names:
        target = root / "opt/mu300/bin" / name
        if not target.exists():
            raise RuntimeError(f"path-commands entry has no target: {name}")
        link = root / "usr/bin" / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.unlink(missing_ok=True)
        link.symlink_to(Path("/opt/mu300/bin") / name)
    sms_link = root / "usr/bin/mu300-sms"
    sms_link.unlink(missing_ok=True)
    sms_link.symlink_to("/opt/mu300/bin/mu300-sms")

    installed_db = root / "lib/apk/db/installed"
    if not installed_db.is_file():
        raise RuntimeError("APK installed package database is missing")
    packages = sorted({line[2:].strip() for line in installed_db.read_text(encoding="utf-8", errors="replace").splitlines()
                       if line.startswith("P:") and line[2:].strip()})
    if "ucode-mod-socket" not in packages:
        raise RuntimeError("ucode-mod-socket is not recorded in the installed APK database")
    (root / "etc/mu300/image-packages").write_text("".join(p + "\n" for p in packages), encoding="utf-8")
    apk_list = subprocess.check_output(
        [shutil.which("qemu-aarch64-static"), "-L", str(root), str(root / "usr/bin/apk"),
         "--root", str(root), "list", "--installed"],
        env={**os.environ, "LC_ALL": "C"}, text=True,
    )
    (root / "etc/mu300/packages.txt").write_text("\n".join(sorted(apk_list.splitlines())) + "\n", encoding="utf-8")
    if not (root / "etc/mu300/packages.txt").stat().st_size:
        raise RuntimeError("APK package inventory is empty")

    return root, packages


def make_tar(root: Path, output: Path) -> None:
    with output.open("wb") as destination:
        tar = subprocess.Popen(
            ["tar", "--sort=name", "--numeric-owner", "--owner=0", "--group=0", "--mtime=@0",
             "--pax-option=delete=atime,delete=ctime", "-cf", "-", "-C", str(root), "."],
            stdout=subprocess.PIPE,
        )
        assert tar.stdout is not None
        gzip = subprocess.Popen(["gzip", "-n"], stdin=tar.stdout, stdout=destination)
        tar.stdout.close()
        if gzip.wait() != 0 or tar.wait() != 0:
            raise RuntimeError("failed to create reproducible OpenWrt rootfs tarball")


def write_hash_manifest(root: Path, output: Path) -> None:
    lines = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == output:
            continue
        rel = path.relative_to(root).as_posix()
        lines.append(f"{sha256(path)}  {rel}")
    output.write_text("\n".join(lines) + "\n", encoding="ascii")


def make_zip(root: Path, zip_path: Path, epoch: int) -> None:
    import datetime
    dt = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    date_time = (max(dt.year, 1980), dt.month, dt.day, dt.hour, dt.minute, dt.second - dt.second % 2)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        for path in sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink()):
            rel = root.name + "/" + path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(rel, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            if path.is_symlink():
                mode = stat.S_IFLNK | file_mode(path)
                payload = os.readlink(path).encode()
            else:
                mode = stat.S_IFREG | file_mode(path)
                payload = path.read_bytes()
            info.external_attr = mode << 16
            zf.writestr(info, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--repo", type=Path, help="clean committed Git checkout; defaults to this script's repository")
    source_group.add_argument("--source-tree", type=Path, help="previously exported source tree from a test package")
    source_group.add_argument("--source-archive", type=Path, help="clean Git archive from export-charge2-test-source.ps1")
    parser.add_argument("--source-metadata", type=Path, help="clean-export metadata JSON for --source-archive")
    parser.add_argument("--source-commit", help="require this exact source commit")
    parser.add_argument("--official-rootfs", type=Path, required=True, help="pinned OpenWrt/LuCI base rootfs tar.gz")
    parser.add_argument("--rollback-kernel", type=Path, required=True, help="pinned official mu300-kernel.tar.gz")
    parser.add_argument("--kernel", type=Path, required=True, help="freshly built pinned mu300-kernel-7.2.tar.gz")
    parser.add_argument("--apk-index", type=Path, required=True, help="signed OpenWrt 25.12.5 packages.adb")
    parser.add_argument("--apk", type=Path, required=True, help=f"pinned {APK_NAME}")
    parser.add_argument("--kernel-provenance", type=Path, required=True,
                        help="JSON evidence linking the compiled inputs to the final source commit")
    parser.add_argument("--kernel-evidence", type=Path, required=True,
                        help="directory with .config, compiler details and complete kernel build logs")
    parser.add_argument("--out", type=Path, required=True, help="new output directory; must not exist")
    args = parser.parse_args()

    if args.repo is None and args.source_tree is None and args.source_archive is None:
        candidate = Path(__file__).resolve().parents[1]
        if (candidate / ".git").exists():
            args.repo = candidate
        elif (candidate / ".mu300-test-source.json").is_file():
            args.source_tree = candidate
        else:
            raise RuntimeError("run from the source repository or pass --repo/--source-tree")
    args.out = args.out.resolve()
    if args.out.exists():
        raise RuntimeError(f"output already exists: {args.out}")
    for name, key in (("official rootfs", "official_openwrt_rootfs"),
                      ("rollback kernel", "official_rollback_kernel"),
                      ("candidate kernel", "candidate_kernel"),
                      ("signed OpenWrt package index", "openwrt_base_index"),
                      ("ucode-mod-socket package", "ucode_mod_socket_apk")):
        require_pin(getattr(args, {"official rootfs": "official_rootfs", "rollback kernel": "rollback_kernel",
                                   "candidate kernel": "kernel", "signed OpenWrt package index": "apk_index",
                                   "ucode-mod-socket package": "apk"}[name]), key)
    if args.apk.name != APK_NAME:
        raise RuntimeError(f"APK filename must be {APK_NAME}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = args.out.parent
    stage = Path(tempfile.mkdtemp(prefix=f".{RELEASE_ID}-", dir=stage_parent))
    package = stage / RELEASE_ID
    package.mkdir()
    try:
        repo = args.repo.resolve() if args.repo else None
        source_tree = args.source_tree.resolve() if args.source_tree else None
        source, source_commit, source_epoch, source_tree_sha = export_source(
            repo, source_tree, args.source_commit, package,
            args.source_archive.resolve() if args.source_archive else None,
            args.source_metadata.resolve() if args.source_metadata else None,
        )
        evidence = args.kernel_evidence.resolve()
        if not evidence.is_dir() or not any(p.is_file() for p in evidence.rglob("*")):
            raise RuntimeError("kernel build evidence directory is empty")
        source_snapshot = json.loads((source / ".mu300-test-source.json").read_text(encoding="utf-8"))[
            "source_snapshot_sha256"
        ]
        kernel_provenance = verify_kernel_provenance(
            args.kernel_provenance.resolve(), source, source_commit, source_snapshot, evidence
        )
        kernel_dir = stage / "kernel-unpacked"
        kernel_dir.mkdir()
        modules = extract_kernel(args.kernel.resolve(), kernel_dir)
        rootfs, packages = make_rootfs(source, args.official_rootfs.resolve(), kernel_dir, modules,
                                       args.apk_index.resolve(), args.apk.resolve(), stage)

        release = package / "release"
        release.mkdir()
        shutil.copy2(args.rollback_kernel, release / "mu300-kernel.tar.gz")
        shutil.copy2(args.kernel, release / "mu300-kernel-7.2.tar.gz")
        input_dir = package / "provenance" / "inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.apk_index, input_dir / "packages.adb")
        shutil.copy2(args.apk, input_dir / APK_NAME)
        input_rootfs = package / "provenance" / "inputs" / "official-openwrt-rootfs.tar.gz"
        shutil.copy2(args.official_rootfs, input_rootfs)
        kernel_evidence = package / "provenance/kernel-build"
        kernel_evidence.mkdir(parents=True, exist_ok=True)
        shutil.copytree(evidence, kernel_evidence, dirs_exist_ok=True)
        shutil.copy2(args.kernel_provenance, kernel_evidence / "BUILD-PROVENANCE.json")
        shutil.copy2(source / "rootfs/overlay/opt/mu300/bin/mu300-update", release / "mu300-update")
        make_tar(rootfs, release / "mu300-openwrt-luci-rootfs.tar.gz")

        if sha256(release / "mu300-update") != sha256(rootfs / "opt/mu300/bin/mu300-update"):
            raise RuntimeError("standalone updater differs from the OpenWrt rootfs updater")
        rootfs_modules = sorted((rootfs / "lib/modules" / KREL).glob("*.ko"))
        if len(rootfs_modules) != MODULE_COUNT:
            raise RuntimeError(f"rootfs contains {len(rootfs_modules)} modules, expected {MODULE_COUNT}")

        manifest = {
            "release_id": RELEASE_ID,
            "test_candidate": True,
            "stable_release_base": "v2026.10.15-charge2",
            "source_commit": source_commit,
            "source_tree_sha256": source_tree_sha,
            "source_snapshot_sha256": json.loads((source / ".mu300-test-source.json").read_text())["source_snapshot_sha256"],
            "kernel_release": KREL,
            "kernel_module_count": MODULE_COUNT,
            "kernel_bundle_sha256": PINS["candidate_kernel"],
            "openwrt_rootfs_sha256": sha256(release / "mu300-openwrt-luci-rootfs.tar.gz"),
            "official_input_sha256": {
                "openwrt_rootfs": PINS["official_openwrt_rootfs"],
                "rollback_kernel": PINS["official_rollback_kernel"],
                "signed_packages_adb": PINS["openwrt_base_index"],
                "ucode_mod_socket_apk": PINS["ucode_mod_socket_apk"],
            },
            "runtime_package_count": len(packages),
            "runtime_packages_include": ["ucode-mod-socket"],
            "kernel_and_modules_freshly_built": True,
            "kernel_image_sha256": sha256(kernel_dir / "Image"),
            "kernel_config_sha256": kernel_provenance["config_sha256"],
            "kernel_source": kernel_provenance["kernel_source"],
            "kernel_compiler": kernel_provenance["compiler"],
            "kernel_input_file_count": len(kernel_provenance["kernel_input_files"]),
            "kernel_build_log_sha256": kernel_provenance.get("build_logs", {}),
            "hardware_validation": False,
            "device_install_or_boot_performed": False,
            "validation_note": "Build-time and static package checks only; no device boot or hardware validation is claimed.",
        }
        (release / "BUILD-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        release_hash_files = sorted(p for p in release.iterdir() if p.is_file())
        (release / "SHA256SUMS").write_text(
            "".join(f"{sha256(path)}  {path.name}\n" for path in release_hash_files), encoding="ascii"
        )

        # Reproducibility and evidence for the independently reviewable package.
        (package / "provenance" / "SOURCE-COMMIT.txt").write_text(
            f"{source_commit}\n", encoding="ascii"
        )
        inputs = {
            "source_commit": source_commit,
            "source_tree_sha256": source_tree_sha,
            "source_snapshot_sha256": manifest["source_snapshot_sha256"],
            "source_date_epoch": source_epoch,
            "inputs": {name: {"sha256": PINS[key], "path": str(Path(value).name)} for name, key, value in (
                ("official_openwrt_rootfs", "official_openwrt_rootfs", args.official_rootfs),
                ("official_rollback_kernel", "official_rollback_kernel", args.rollback_kernel),
                ("candidate_kernel", "candidate_kernel", args.kernel),
                ("signed_openwrt_packages_adb", "openwrt_base_index", args.apk_index),
                ("ucode_mod_socket_apk", "ucode_mod_socket_apk", args.apk),
            )},
            "package_recipe": "source/tools/build-charge2-test.py",
            "kernel_provenance_sha256": sha256(args.kernel_provenance),
            "kernel_evidence_files": sorted(
                p.relative_to(evidence).as_posix() for p in evidence.rglob("*") if p.is_file()
            ),
        }
        (package / "provenance/BUILD-INPUTS.json").write_text(
            json.dumps(inputs, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (package / "BUILD-CHARGE2-TEST.md").write_text(
            package_doc(source, "BUILD-CHARGE2-TEST.md.in"), encoding="utf-8"
        )
        (package / "README.md").write_text(
            package_doc(source, "README-CHARGE2-TEST.md.in"), encoding="utf-8"
        )
        wrapper = package / "install-u30air-host.ps1"
        wrapper.write_text(host_wrapper(), encoding="utf-8-sig", newline="\r\n")

        # Top-level manifest hashes every delivered file except itself.
        write_hash_manifest(package, package / "PACKAGE-FILES.sha256")
        # Pin all package timestamps to the source commit for repeatable ZIP metadata.
        for path in sorted(package.rglob("*"), reverse=True):
            try:
                os.utime(path, (source_epoch, source_epoch), follow_symlinks=False)
            except (NotImplementedError, OSError):
                pass
        os.utime(package, (source_epoch, source_epoch))
        zip_path = stage_parent / f"{RELEASE_ID}.zip"
        if zip_path.exists():
            raise RuntimeError(f"zip output already exists: {zip_path}")
        package.rename(args.out)
        make_zip(args.out, zip_path, source_epoch)
        print(f"package: {args.out}")
        print(f"zip: {zip_path}")
        print(f"source commit: {source_commit}")
        print(f"kernel SHA-256: {PINS['candidate_kernel']}")
        print(f"OpenWrt rootfs SHA-256: {sha256(args.out / 'release/mu300-openwrt-luci-rootfs.tar.gz')}")
        print(f"module count: {MODULE_COUNT}")
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def host_wrapper() -> str:
    return r'''[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$VerifyOnly,
    [string]$Work = '',
    [ValidateSet('en','tr','zh')][string]$Lang = 'zh'
)

$ErrorActionPreference = 'Stop'
$Package = $PSScriptRoot
$Release = Join-Path $Package 'release'
function Stop-Install([string]$Message) { throw "U30 Air test package: $Message" }

$manifestPath = Join-Path $Release 'BUILD-MANIFEST.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { Stop-Install 'release/BUILD-MANIFEST.json is missing.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.release_id -ne 'charge2-test1-20261012' -or -not $manifest.test_candidate) {
    Stop-Install 'release manifest does not identify this exact test candidate.'
}
$sumFile = Join-Path $Release 'SHA256SUMS'
if (-not (Test-Path -LiteralPath $sumFile -PathType Leaf)) { Stop-Install 'release/SHA256SUMS is missing.' }
$expected = @{}
foreach ($line in [IO.File]::ReadAllLines($sumFile)) {
    if ($line -match '^([0-9a-fA-F]{64})\s+\*?([^\s]+)$') { $expected[$Matches[2]] = $Matches[1].ToLowerInvariant() }
}
$required = @('mu300-kernel.tar.gz','mu300-kernel-7.2.tar.gz','mu300-openwrt-luci-rootfs.tar.gz','mu300-update','BUILD-MANIFEST.json')
foreach ($name in $required) {
    $path = Join-Path $Release $name
    if (-not $expected.ContainsKey($name)) { Stop-Install "SHA256SUMS has no entry for $name." }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { Stop-Install "release/$name is missing." }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected[$name]) { Stop-Install "SHA-256 mismatch for release/$name." }
}
if ($VerifyOnly) { Write-Host 'Test firmware assets: SHA-256 verified.'; exit 0 }

# Serialize calls made through this offline wrapper on this host. Direct invocation of source/install.ps1 or another package is outside this lock.
$mutex = $null
$ownsMutex = $false
try {
    $mutex = [System.Threading.Mutex]::new($false, 'Global\MU300-U30Air-TestInstaller')
    try { $ownsMutex = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
}
catch {
    if ($mutex) { $mutex.Dispose() }
    Stop-Install "could not acquire the host install lock: $($_.Exception.Message)"
}
if (-not $ownsMutex) {
    if ($mutex) { $mutex.Dispose() }
    Stop-Install 'another test-package installer is already running on this host.'
}

$exitCode = 1
try {
    foreach ($tool in @('adb','tar')) {
        if (-not (Get-Command $tool -CommandType Application -ErrorAction SilentlyContinue)) { Stop-Install "required host tool '$tool' is missing." }
    }
    $python = $null
    foreach ($name in @('python','python3','py')) {
        $candidate = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($candidate) {
            & $candidate.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $python = $candidate.Source; break }
        }
    }
    if (-not $python) { Stop-Install 'Python 3.8 or newer is required.' }
    & $python -c 'import lz4.block' 2>$null
    if ($LASTEXITCODE -ne 0) { Stop-Install 'the Python lz4 module is required.' }

    $invokeArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $Package 'source\install.ps1'),
        '-U30AirHostPackage','-NoSelfUpdate','-ReleaseDirectory',$Release,'-Lang',$Lang)
    if ($Check) { $invokeArgs += '-Check' }
    if ($Work) { $invokeArgs += @('-Work',$Work) }
    Write-Warning 'This is a test firmware candidate. Device boot and hardware validation are not claimed.'
    & powershell.exe @invokeArgs
    $exitCode = $LASTEXITCODE
}
finally {
    if ($ownsMutex -and $mutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
exit $exitCode
'''


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"build-charge2-test: {exc}", file=sys.stderr)
        raise SystemExit(1)
