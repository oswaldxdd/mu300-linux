#!/usr/bin/env python3
"""Assemble the formal U30 Air charge2 r2 package from reviewed, pinned inputs.

The kernel bundle is reused only when every recorded kernel source input in the
original build evidence is byte-identical in the final clean source commit.
This script never compiles the kernel and never accesses a device.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


RELEASE_ID = "v2026.10.15-charge2-r2"
ZIP_NAME = "U30 Air Openwrt -r2.zip"
RELEASE_CHANNEL = "stable"
KREL = "7.2.9-u30air-charge2"
MODULE_COUNT = 379
NATIVE_APK_NAME = "mu300-native-kmod-compat-7.2.9-r1.apk"
EXPECTED_CONFIG_SHA256 = "7f87982af61c7a9a5577babac21b3055042fcba513c89dae95138a56afe6795e"
EXPECTED_BUILTINS_SHA256 = "9f37862d34d1f5cd1596fc9d718e257dd3f2174b31f81cb8447096cd7f940d74"
EXPECTED_VERMAGIC = "7.2.9-u30air-charge2 SMP preempt mod_unload aarch64"
REQUIRED_CONFIG = {
    "CONFIG_DUMMY": "m",
    "CONFIG_INET_DIAG": "y",
    "CONFIG_INET_TCP_DIAG": "y",
    "CONFIG_INET_UDP_DIAG": "m",
    "CONFIG_INET_RAW_DIAG": "m",
    "CONFIG_NFT_SOCKET": "y",
    "CONFIG_NFT_TPROXY": "y",
    "CONFIG_NF_SOCKET_IPV4": "y",
    "CONFIG_NF_SOCKET_IPV6": "y",
    "CONFIG_NF_TPROXY_IPV4": "y",
    "CONFIG_NF_TPROXY_IPV6": "y",
}
REQUIRED_MODULES = {
    "dummy.ko": "13dceaac167531650262f8d782b4e79df9a93cd6c8efd45dfc7bcbba0690ccce",
    "udp_diag.ko": "81b6171e6c8b3ca081b08f83a9df50d87ac7a672b03667ee5bb93445ff414aa4",
    "raw_diag.ko": "259fb82e25c352cfff6dadeabd80f3d61b74d08fa5d977a9efd903b594a775c1",
}
REQUIRED_BUILTINS = (
    "kernel/net/netfilter/nft_socket.ko",
    "kernel/net/netfilter/nft_tproxy.ko",
    "kernel/net/ipv4/inet_diag.ko",
    "kernel/net/ipv4/tcp_diag.ko",
    "kernel/net/ipv4/netfilter/nf_socket_ipv4.ko",
    "kernel/net/ipv4/netfilter/nf_tproxy_ipv4.ko",
    "kernel/net/ipv6/netfilter/nf_socket_ipv6.ko",
    "kernel/net/ipv6/netfilter/nf_tproxy_ipv6.ko",
)


def load_test_builder():
    path = Path(__file__).with_name("build-charge2-test.py")
    spec = importlib.util.spec_from_file_location("mu300_charge2_test_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load shared charge2 build helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load_test_builder()
helper.RELEASE_ID = RELEASE_ID


def sha256(path: Path) -> str:
    return helper.sha256(path)


def verify_reused_kernel(kernel: Path, source: Path, source_commit: str,
                         source_snapshot_sha256: str, provenance_path: Path,
                         evidence: Path) -> dict:
    """Bind an audited prior kernel build to this commit's unchanged upstream tree."""
    helper.require_pin(kernel, "candidate_kernel")
    if not provenance_path.is_file():
        raise RuntimeError(f"original kernel build provenance is missing: {provenance_path}")
    original = json.loads(provenance_path.read_text(encoding="utf-8"))
    if original.get("candidate_kernel_sha256") != helper.PINS["candidate_kernel"]:
        raise RuntimeError("original kernel provenance names a different kernel bundle")
    if original.get("kernel_release") != KREL or original.get("module_count") != MODULE_COUNT:
        raise RuntimeError("original kernel provenance has the wrong release or module count")
    for required in ("source_commit", "source_snapshot_sha256", "kernel_source", "compiler",
                     "config_sha256", "build_logs", "kernel_input_files"):
        if not original.get(required):
            raise RuntimeError(f"original kernel provenance is missing {required}")

    build_logs = original["build_logs"]
    if not isinstance(build_logs, dict):
        raise RuntimeError("original kernel build_logs must map paths to SHA-256 values")
    for rel, expected in build_logs.items():
        log = evidence / "logs" / rel
        if not log.is_file() or sha256(log) != expected:
            raise RuntimeError(f"original kernel build log is missing or changed: {rel}")

    records = original["kernel_input_files"]
    if not isinstance(records, list) or not records:
        raise RuntimeError("original kernel input manifest must be a non-empty list")
    seen: set[str] = set()
    current_hashes: dict[str, str] = {}
    for record in records:
        rel = str(record.get("path", ""))
        if not rel.startswith("upstream/") or ".." in Path(rel).parts or rel in seen:
            raise RuntimeError(f"invalid or duplicate upstream input path: {rel!r}")
        seen.add(rel)
        build_sha = str(record.get("build_sha256", ""))
        original_commit_sha = str(record.get("commit_sha256", ""))
        final_path = source / rel
        if not final_path.is_file():
            raise RuntimeError(f"reused kernel input is absent from final source: {rel}")
        final_sha = sha256(final_path)
        if not build_sha or build_sha != original_commit_sha or final_sha != build_sha:
            raise RuntimeError(
                f"kernel input changed since original build: {rel} "
                f"(build={build_sha}, original={original_commit_sha}, final={final_sha})"
            )
        current_hashes[rel] = final_sha

    expected_files = {
        item.relative_to(source).as_posix()
        for item in (source / "upstream").rglob("*")
        if item.is_file() and not any(part.startswith("out-") for part in item.relative_to(source).parts)
    }
    if seen != expected_files:
        missing, extra = sorted(expected_files - seen)[:5], sorted(seen - expected_files)[:5]
        raise RuntimeError(f"kernel input coverage differs (missing={missing}, extra={extra})")
    for prefix in ("upstream/modules/", "upstream/port/", "upstream/u30air/",
                   "upstream/dts/", "upstream/patches/"):
        if not any(path.startswith(prefix) for path in seen):
            raise RuntimeError(f"kernel input manifest omits {prefix}")

    kernel_dir = Path(tempfile.mkdtemp(prefix="mu300-r2-kernel-check-"))
    try:
        modules = helper.extract_kernel(kernel, kernel_dir)
        if len(modules) != MODULE_COUNT:
            raise RuntimeError(f"reused kernel has {len(modules)} modules, expected {MODULE_COUNT}")
        image_sha = sha256(kernel_dir / "Image")
    finally:
        shutil.rmtree(kernel_dir, ignore_errors=True)

    return {
        "source_commit": source_commit,
        "source_snapshot_sha256": source_snapshot_sha256,
        "kernel_release": KREL,
        "module_count": MODULE_COUNT,
        "candidate_kernel_sha256": helper.PINS["candidate_kernel"],
        "kernel_bundle_reused": True,
        "kernel_bundle_original_source_commit": original["source_commit"],
        "kernel_bundle_original_source_snapshot_sha256": original["source_snapshot_sha256"],
        "original_build_provenance_sha256": sha256(provenance_path),
        "kernel_source": original["kernel_source"],
        "compiler": original["compiler"],
        "config_sha256": original["config_sha256"],
        "build_logs": original["build_logs"],
        "kernel_input_files": records,
        "final_source_kernel_input_sha256": current_hashes,
        "kernel_image_sha256": image_sha,
        "reuse_verification": "all recorded upstream kernel inputs match the final committed source byte-for-byte",
    }


def verify_native_provider_guard_inputs(source: Path, kernel_dir: Path, evidence: Path) -> dict:
    """Check every fact consumed by the provider's target-side guard offline."""
    config = evidence / "kernel.config"
    if not config.is_file():
        raise RuntimeError(f"native provider audit requires effective kernel config: {config}")
    config_sha = sha256(config)
    if config_sha != EXPECTED_CONFIG_SHA256:
        raise RuntimeError(f"kernel.config SHA-256 mismatch: {config_sha}")
    config_lines = config.read_text(encoding="utf-8", errors="strict").splitlines()
    values = {}
    for symbol in REQUIRED_CONFIG:
        matches = [line for line in config_lines
                   if line.startswith(symbol + "=") or line == f"# {symbol} is not set"]
        if len(matches) != 1:
            raise RuntimeError(f"kernel.config must contain one value for {symbol}")
        value = "n" if matches[0].startswith("# ") else matches[0].split("=", 1)[1]
        if value != REQUIRED_CONFIG[symbol]:
            raise RuntimeError(f"{symbol}={value}, expected {REQUIRED_CONFIG[symbol]}")
        values[symbol] = value

    builtin_file = kernel_dir / "modules.builtin"
    if not builtin_file.is_file():
        raise RuntimeError("candidate kernel bundle has no modules.builtin list")
    builtin_sha = sha256(builtin_file)
    if builtin_sha != EXPECTED_BUILTINS_SHA256:
        raise RuntimeError(f"modules.builtin SHA-256 mismatch: {builtin_sha}")
    builtin_entries = builtin_file.read_text(encoding="utf-8", errors="strict").splitlines()
    missing_builtins = sorted(set(REQUIRED_BUILTINS) - set(builtin_entries))
    if missing_builtins:
        raise RuntimeError(f"modules.builtin omits native capability entries: {missing_builtins}")

    module_results = {}
    for name, expected_sha in REQUIRED_MODULES.items():
        path = kernel_dir / "modules" / name
        if not path.is_file():
            raise RuntimeError(f"native provider guard module is absent: {path}")
        actual_sha = sha256(path)
        if actual_sha != expected_sha:
            raise RuntimeError(f"{name} SHA-256 mismatch: expected {expected_sha}, got {actual_sha}")
        data = path.read_bytes()
        match = re.search(rb"vermagic=([^\x00]+)", data)
        actual_vermagic = match.group(1).decode("ascii", "replace") if match else ""
        if actual_vermagic != EXPECTED_VERMAGIC:
            raise RuntimeError(f"{name} vermagic mismatch: {actual_vermagic!r}")
        module_results[name] = {"sha256": actual_sha, "vermagic": actual_vermagic}

    guard = source / "tools/native-kmod-compat/preinstall-guard.sh"
    if not guard.is_file():
        raise RuntimeError("native compatibility package guard is missing from source")
    guard_text = guard.read_text(encoding="utf-8", errors="strict")
    required_guard_facts = [
        KREL,
        f"EXPECTED_VERMAGIC='{EXPECTED_VERMAGIC}'",
        EXPECTED_CONFIG_SHA256,
        EXPECTED_BUILTINS_SHA256,
        *REQUIRED_MODULES.values(),
        *[f"check_config {key.removeprefix('CONFIG_')} {value}" for key, value in REQUIRED_CONFIG.items()],
        *[f"check_builtin {entry}" for entry in REQUIRED_BUILTINS],
    ]
    missing_guard = [fact for fact in required_guard_facts if fact not in guard_text]
    if missing_guard:
        raise RuntimeError(f"native provider guard does not enforce audited kernel facts: {missing_guard}")

    return {
        "kernel_release": KREL,
        "kernel_bundle_sha256": helper.PINS["candidate_kernel"],
        "effective_config_sha256": config_sha,
        "config_values": values,
        "modules_builtin_sha256": builtin_sha,
        "required_builtin_entries": list(REQUIRED_BUILTINS),
        "module_sha256_and_vermagic": module_results,
        "source_guard_sha256": sha256(guard),
        "guard_matches_offline_audit": True,
    }


def render_release_doc(source: Path, template_name: str) -> str:
    return helper.package_doc(source, template_name)


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
function Stop-Install([string]$Message) { throw "U30 Air charge2 release: $Message" }

$manifestPath = Join-Path $Release 'BUILD-MANIFEST.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { Stop-Install 'release/BUILD-MANIFEST.json is missing.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.release_id -ne 'v2026.10.15-charge2-r2' -or
    $manifest.release_channel -ne 'stable' -or
    $manifest.test_candidate -isnot [System.Boolean] -or
    $manifest.test_candidate -ne $false -or
    $manifest.kernel_release -ne '7.2.9-u30air-charge2') {
    Stop-Install 'release manifest does not identify the exact formal r2 firmware.'
}
$sumFile = Join-Path $Release 'SHA256SUMS'
if (-not (Test-Path -LiteralPath $sumFile -PathType Leaf)) { Stop-Install 'release/SHA256SUMS is missing.' }
$expected = @{}
foreach ($line in [IO.File]::ReadAllLines($sumFile)) {
    if ($line -match '^([0-9a-fA-F]{64})\s+\*?([^\s]+)$') { $expected[$Matches[2]] = $Matches[1].ToLowerInvariant() }
}
$required = @('mu300-kernel.tar.gz','mu300-kernel-7.2.tar.gz','mu300-openwrt-luci-rootfs.tar.gz',
    'mu300-update','mu300-native-kmod-compat-7.2.9-r1.apk','BUILD-MANIFEST.json')
foreach ($name in $required) {
    $path = Join-Path $Release $name
    if (-not $expected.ContainsKey($name)) { Stop-Install "SHA256SUMS has no entry for $name." }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { Stop-Install "release/$name is missing." }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected[$name]) { Stop-Install "SHA-256 mismatch for release/$name." }
}
if ($VerifyOnly) { Write-Host 'Charge2 r2 firmware assets: SHA-256 verified.'; exit 0 }

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
    Stop-Install 'another U30 Air offline package installer is already running on this host.'
}

$exitCode = 1
try {
    foreach ($tool in @('adb','tar','pwsh.exe')) {
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
    Write-Warning 'This release contains a new offline artifact. The exact r2 package has not been installed or booted as a whole.'
    $pwsh = Get-Command 'pwsh.exe' -CommandType Application | Select-Object -First 1
    & $pwsh.Source @invokeArgs
    $exitCode = $LASTEXITCODE
}
finally {
    if ($ownsMutex -and $mutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
exit $exitCode
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--repo", type=Path, help="clean committed Git checkout; defaults to this script's repository")
    source_group.add_argument("--source-archive", type=Path, help="clean Git archive plus matching export metadata")
    parser.add_argument("--source-metadata", type=Path, help="export metadata JSON required with --source-archive")
    parser.add_argument("--source-commit", help="require this exact final source commit")
    parser.add_argument("--official-rootfs", type=Path, required=True)
    parser.add_argument("--rollback-kernel", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True, help="audited 7.2.9 candidate bundle to reuse")
    parser.add_argument("--apk-index", type=Path, required=True, help="signed OpenWrt packages.adb")
    parser.add_argument("--apk", type=Path, required=True, help="pinned ucode-mod-socket APK")
    parser.add_argument("--kernel-provenance", type=Path, required=True,
                        help="provenance from the original audited kernel build")
    parser.add_argument("--kernel-evidence", type=Path, required=True,
                        help="original .config/compiler/log evidence")
    parser.add_argument("--out", type=Path, required=True, help="new formal package directory; must not exist")
    args = parser.parse_args()

    if args.repo is None and args.source_archive is None:
        candidate = Path(__file__).resolve().parents[1]
        if (candidate / ".git").exists():
            args.repo = candidate
        else:
            raise RuntimeError("run from the source repository or pass --repo/--source-archive")
    if args.source_archive and not args.source_metadata:
        raise RuntimeError("--source-archive requires --source-metadata")
    args.out = args.out.resolve()
    if args.out.exists():
        raise RuntimeError(f"output already exists: {args.out}")

    for name, path in (("official_openwrt_rootfs", args.official_rootfs),
                       ("official_rollback_kernel", args.rollback_kernel),
                       ("candidate_kernel", args.kernel),
                       ("openwrt_base_index", args.apk_index),
                       ("ucode_mod_socket_apk", args.apk)):
        helper.require_pin(path.resolve(), name)
    if args.apk.name != helper.APK_NAME:
        raise RuntimeError(f"ucode-mod-socket APK filename must be {helper.APK_NAME}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{RELEASE_ID}-", dir=args.out.parent))
    package = stage / RELEASE_ID
    package.mkdir()
    try:
        repo = args.repo.resolve() if args.repo else None
        source, source_commit, source_epoch, source_tree_sha = helper.export_source(
            repo, None, args.source_commit, package,
            args.source_archive.resolve() if args.source_archive else None,
            args.source_metadata.resolve() if args.source_metadata else None,
        )
        source_marker = json.loads((source / ".mu300-test-source.json").read_text(encoding="utf-8"))
        source_snapshot_sha = source_marker["source_snapshot_sha256"]
        evidence = args.kernel_evidence.resolve()
        if not evidence.is_dir() or not any(path.is_file() for path in evidence.rglob("*")):
            raise RuntimeError("original kernel evidence directory is empty")
        kernel_provenance = verify_reused_kernel(
            args.kernel.resolve(), source, source_commit, source_snapshot_sha,
            args.kernel_provenance.resolve(), evidence,
        )

        provider_apk = stage / NATIVE_APK_NAME
        subprocess.run([
            sys.executable, str(source / "tools/build-native-kmod-compat-apk.py"),
            "--source-root", str(source), "--source-commit", source_commit,
            "--source-date-epoch", str(source_epoch), "--output", str(provider_apk),
        ], check=True)
        provider_sha = sha256(provider_apk)

        kernel_dir = stage / "kernel-unpacked"
        kernel_dir.mkdir()
        modules = helper.extract_kernel(args.kernel.resolve(), kernel_dir)
        native_kernel_audit = verify_native_provider_guard_inputs(source, kernel_dir, evidence)
        rootfs, packages = helper.make_rootfs(
            source, args.official_rootfs.resolve(), kernel_dir, modules,
            args.apk_index.resolve(), args.apk.resolve(), stage, provider_apk,
        )
        k10 = rootfs / "etc/rc.d/K10mu300-traffic"
        if not k10.is_symlink() or os.readlink(k10) != "../init.d/mu300-traffic":
            raise RuntimeError("rootfs is missing the mu300-traffic STOP=10 shutdown link")
        s20 = rootfs / "etc/rc.d/S20mu300-fcc-record"
        if not s20.is_symlink() or os.readlink(s20) != "../init.d/mu300-fcc-record":
            raise RuntimeError("rootfs is missing the FCC START=20 boot link")
        if (rootfs / "etc/mu300/image-version").read_text(encoding="ascii").strip() != RELEASE_ID:
            raise RuntimeError("OpenWrt image-version does not match this release ID")

        release = package / "release"
        release.mkdir()
        shutil.copy2(args.rollback_kernel, release / "mu300-kernel.tar.gz")
        shutil.copy2(args.kernel, release / "mu300-kernel-7.2.tar.gz")
        shutil.copy2(provider_apk, release / NATIVE_APK_NAME)

        input_dir = package / "provenance/inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.apk_index, input_dir / "packages.adb")
        shutil.copy2(args.apk, input_dir / helper.APK_NAME)
        shutil.copy2(provider_apk, input_dir / NATIVE_APK_NAME)
        shutil.copy2(args.official_rootfs, input_dir / "official-openwrt-rootfs.tar.gz")

        kernel_evidence_dest = package / "provenance/kernel-build"
        shutil.copytree(evidence, kernel_evidence_dest, dirs_exist_ok=True)
        shutil.copy2(args.kernel_provenance, kernel_evidence_dest / "BUILD-PROVENANCE-ORIGINAL.json")
        (kernel_evidence_dest / "BUILD-PROVENANCE.json").write_text(
            json.dumps(kernel_provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        shutil.copy2(source / "rootfs/overlay/opt/mu300/bin/mu300-update", release / "mu300-update")
        helper.make_tar(rootfs, release / "mu300-openwrt-luci-rootfs.tar.gz")
        if sha256(release / "mu300-update") != sha256(rootfs / "opt/mu300/bin/mu300-update"):
            raise RuntimeError("standalone updater differs from the OpenWrt rootfs updater")
        rootfs_modules = sorted((rootfs / "lib/modules" / KREL).glob("*.ko"))
        if len(rootfs_modules) != MODULE_COUNT:
            raise RuntimeError(f"rootfs contains {len(rootfs_modules)} modules, expected {MODULE_COUNT}")

        manifest = {
            "release_id": RELEASE_ID,
            "release_channel": RELEASE_CHANNEL,
            "test_candidate": False,
            "stable_release_base": "v2026.10.15-charge2",
            "source_commit": source_commit,
            "source_tree_sha256": source_tree_sha,
            "source_snapshot_sha256": source_snapshot_sha,
            "kernel_release": KREL,
            "kernel_module_count": MODULE_COUNT,
            "kernel_bundle_sha256": helper.PINS["candidate_kernel"],
            "kernel_bundle_reused": True,
            "kernel_bundle_original_source_commit": kernel_provenance["kernel_bundle_original_source_commit"],
            "kernel_and_modules_freshly_built": False,
            "openwrt_rootfs_sha256": sha256(release / "mu300-openwrt-luci-rootfs.tar.gz"),
            "official_input_sha256": {
                "openwrt_rootfs": helper.PINS["official_openwrt_rootfs"],
                "rollback_kernel": helper.PINS["official_rollback_kernel"],
                "signed_packages_adb": helper.PINS["openwrt_base_index"],
                "ucode_mod_socket_apk": helper.PINS["ucode_mod_socket_apk"],
            },
            "runtime_package_count": len(packages),
            "runtime_packages_include": ["ucode-mod-socket", "mu300-native-kmod-compat"],
            "native_kmod_provider": {
                "package": "mu300-native-kmod-compat",
                "version": "7.2.9-r1",
                "architecture": "aarch64_generic",
                "sha256": provider_sha,
                "provides": list(helper.NATIVE_KMOD_PROVIDES),
                "embedded_in_rootfs": True,
                "kernel_modules_in_package": False,
                "signature": "unsigned project-local package; per-command allowance used only during image assembly",
                "guard_kernel_audit": native_kernel_audit,
            },
            "native_kmod_kernel_audit": native_kernel_audit,
            "kernel_image_sha256": kernel_provenance["kernel_image_sha256"],
            "kernel_config_sha256": kernel_provenance["config_sha256"],
            "kernel_source": kernel_provenance["kernel_source"],
            "kernel_compiler": kernel_provenance["compiler"],
            "kernel_input_file_count": len(kernel_provenance["kernel_input_files"]),
            "kernel_build_log_sha256": kernel_provenance["build_logs"],
            "hardware_validation": False,
            "device_install_or_boot_performed": False,
            "validation_note": (
                "This exact r2 package was not installed or booted as a whole. The prior test candidate was "
                "evaluated separately; this r2 artifact adds the native capability provider and shutdown link."
            ),
        }
        (release / "BUILD-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (release / "RELEASE-NOTES.md").write_text(
            render_release_doc(source, "RELEASE-NOTES-CHARGE2-R2.md.in"), encoding="utf-8"
        )
        (release / "SHA256SUMS").write_text(
            "".join(f"{sha256(path)}  {path.name}\n" for path in sorted(release.iterdir())
                    if path.is_file() and path.name != "SHA256SUMS"), encoding="ascii"
        )

        inputs = {
            "release_id": RELEASE_ID,
            "source_commit": source_commit,
            "source_tree_sha256": source_tree_sha,
            "source_snapshot_sha256": source_snapshot_sha,
            "source_date_epoch": source_epoch,
            "kernel_bundle_reuse": {
                "sha256": helper.PINS["candidate_kernel"],
                "original_source_commit": kernel_provenance["kernel_bundle_original_source_commit"],
                "final_source_commit": source_commit,
                "verification": kernel_provenance["reuse_verification"],
                "all_kernel_input_files_match": True,
            },
            "inputs": {
                name: {"sha256": helper.PINS[key], "path": path.name}
                for name, key, path in (
                    ("official_openwrt_rootfs", "official_openwrt_rootfs", args.official_rootfs),
                    ("official_rollback_kernel", "official_rollback_kernel", args.rollback_kernel),
                    ("candidate_kernel", "candidate_kernel", args.kernel),
                    ("signed_openwrt_packages_adb", "openwrt_base_index", args.apk_index),
                    ("ucode_mod_socket_apk", "ucode_mod_socket_apk", args.apk),
                )
            },
            "native_kmod_provider": {
                "source_recipe": "source/tools/build-native-kmod-compat-apk.py",
                "sha256": provider_sha,
                "payload": "docs and capability manifest only; no .ko files",
                "offline_guard_inputs_audited": native_kernel_audit,
            },
            "package_recipe": "source/tools/build-charge2-release.py",
            "original_kernel_provenance_sha256": sha256(args.kernel_provenance),
        }
        (package / "provenance/BUILD-INPUTS.json").write_text(
            json.dumps(inputs, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (package / "BUILD-CHARGE2-RELEASE.md").write_text(
            render_release_doc(source, "BUILD-CHARGE2-RELEASE.md.in"), encoding="utf-8"
        )
        (package / "README.md").write_text(
            render_release_doc(source, "README-CHARGE2-RELEASE.md.in"), encoding="utf-8"
        )
        wrapper = package / "install-u30air-host.ps1"
        wrapper.write_text(host_wrapper(), encoding="utf-8-sig", newline="\r\n")
        helper.write_hash_manifest(package, package / "PACKAGE-FILES.sha256")

        for path in sorted(package.rglob("*"), reverse=True):
            try:
                os.utime(path, (source_epoch, source_epoch), follow_symlinks=False)
            except (NotImplementedError, OSError):
                pass
        os.utime(package, (source_epoch, source_epoch))
        zip_path = args.out.parent / ZIP_NAME
        if zip_path.exists():
            raise RuntimeError(f"zip output already exists: {zip_path}")
        package.rename(args.out)
        helper.make_zip(args.out, zip_path, source_epoch)
        print(f"package: {args.out}")
        print(f"zip: {zip_path}")
        print(f"source commit: {source_commit}")
        print(f"kernel reused from source commit: {kernel_provenance['kernel_bundle_original_source_commit']}")
        print(f"kernel bundle SHA-256: {helper.PINS['candidate_kernel']}")
        print(f"native provider SHA-256: {provider_sha}")
        print(f"OpenWrt rootfs SHA-256: {manifest['openwrt_rootfs_sha256']}")
        print(f"module count: {MODULE_COUNT}")
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, tarfile.TarError,
            json.JSONDecodeError) as exc:
        print(f"build-charge2-release: {exc}", file=sys.stderr)
        raise SystemExit(1)
