#!/usr/bin/env python3
"""Assemble the formal U30 Air charge2 r3 package from reviewed, pinned inputs.

The audited kernel base is reused only after proving that a fresh SC2355 Wi-Fi
module is the sole changed kernel member and all other upstream inputs match
the original build. This script never accesses a device.
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


RELEASE_ID = "v2026.10.15-charge2-r3"
ZIP_NAME = "U30-Air-Openwrt-r3.zip"
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

def load_r3_kernel_audit():
    path = Path(__file__).with_name("charge2-r3-kernel-audit.py")
    spec = importlib.util.spec_from_file_location("mu300_charge2_r3_kernel_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load r3 kernel audit helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

r3_kernel_audit = load_r3_kernel_audit()


def sha256(path: Path) -> str:
    return helper.sha256(path)


def verify_r3_kernel(kernel: Path, base_kernel: Path, source: Path, repo: Path,
                     source_commit: str, module_provenance_path: Path,
                     module_evidence: Path, provenance_path: Path,
                     evidence: Path) -> dict:
    return r3_kernel_audit.verify(
        kernel, base_kernel, source, repo, source_commit, module_provenance_path,
        module_evidence, provenance_path, evidence, helper.PINS["candidate_kernel"],
        EXPECTED_CONFIG_SHA256,
    )

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
    if ($manifest.release_id -ne 'v2026.10.15-charge2-r3' -or
    $manifest.release_channel -ne 'stable' -or
    $manifest.test_candidate -isnot [System.Boolean] -or
    $manifest.test_candidate -ne $false -or
    $manifest.kernel_release -ne '7.2.9-u30air-charge2') {
    Stop-Install 'release manifest does not identify the exact formal r3 firmware.'
}
$sumFile = Join-Path $Release 'SHA256SUMS'
if (-not (Test-Path -LiteralPath $sumFile -PathType Leaf)) { Stop-Install 'release/SHA256SUMS is missing.' }
$expected = @{}
foreach ($line in [IO.File]::ReadAllLines($sumFile)) {
    if ($line -match '^([0-9a-fA-F]{64})\s+\*?([^\s]+)$') { $expected[$Matches[2]] = $Matches[1].ToLowerInvariant() }
}
$required = @('mu300-kernel.tar.gz','mu300-kernel-7.2.tar.gz','mu300-openwrt-luci-rootfs.tar.gz',
    'mu300-update','mu300-native-kmod-compat-7.2.9-r1.apk',
    'wpad-basic-mbedtls-2025.08.26~ca266cc2-r6.apk','hostapd-common-2025.08.26~ca266cc2-r6.apk',
    'BUILD-MANIFEST.json')
foreach ($name in $required) {
    $path = Join-Path $Release $name
    if (-not $expected.ContainsKey($name)) { Stop-Install "SHA256SUMS has no entry for $name." }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { Stop-Install "release/$name is missing." }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected[$name]) { Stop-Install "SHA-256 mismatch for release/$name." }
}
if ($VerifyOnly) { Write-Host 'Charge2 r3 firmware assets: SHA-256 verified.'; exit 0 }

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
    Write-Warning 'This release contains a new offline artifact. The exact r3 package has not been installed or booted as a whole.'
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
    parser.add_argument("--repo", type=Path, help="clean committed Git checkout; defaults to this script's repository")
    parser.add_argument("--source-commit", help="require this exact final source commit")
    parser.add_argument("--official-rootfs", type=Path, required=True)
    parser.add_argument("--rollback-kernel", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True, help="candidate kernel bundle with the freshly built Wi-Fi module")
    parser.add_argument("--base-kernel", type=Path, required=True, help="audited r2 kernel bundle used as binary reuse base")
    parser.add_argument("--module-provenance", type=Path, required=True, help="fresh Wi-Fi module build provenance JSON")
    parser.add_argument("--module-evidence", type=Path, required=True, help="fresh module build log and provenance directory")
    parser.add_argument("--wpad-apk", type=Path, required=True)
    parser.add_argument("--hostapd-common-apk", type=Path, required=True)
    parser.add_argument("--wifi-build-evidence", type=Path, required=True,
                        help="clean SDK build log, input pins, and r6 APK provenance")
    parser.add_argument("--apk-index", type=Path, required=True, help="signed OpenWrt packages.adb")
    parser.add_argument("--apk", type=Path, required=True, help="pinned ucode-mod-socket APK")
    parser.add_argument("--kernel-provenance", type=Path, required=True,
                        help="provenance from the original audited kernel build")
    parser.add_argument("--kernel-evidence", type=Path, required=True,
                        help="original .config/compiler/log evidence")
    parser.add_argument("--out", type=Path, required=True, help="new formal package directory; must not exist")
    args = parser.parse_args()

    if args.repo is None:
        candidate = Path(__file__).resolve().parents[1]
        if (candidate / ".git").exists():
            args.repo = candidate
        else:
            raise RuntimeError("run from the source repository or pass --repo")
    args.repo = args.repo.resolve()
    args.out = args.out.resolve()
    args.wifi_build_evidence = args.wifi_build_evidence.resolve()
    if args.out.exists():
        raise RuntimeError(f"output already exists: {args.out}")
    if not args.wifi_build_evidence.is_dir():
        raise RuntimeError("Wi-Fi SDK build evidence directory is missing")

    for name, path in (("official_openwrt_rootfs", args.official_rootfs),
                       ("official_rollback_kernel", args.rollback_kernel),
                       ("candidate_kernel", args.base_kernel),
                       ("openwrt_base_index", args.apk_index),
                       ("ucode_mod_socket_apk", args.apk)):
        helper.require_pin(path.resolve(), name)
    if args.apk.name != helper.APK_NAME:
        raise RuntimeError(f"ucode-mod-socket APK filename must be {helper.APK_NAME}")
    if args.wpad_apk.name != "wpad-basic-mbedtls-2025.08.26~ca266cc2-r6.apk":
        raise RuntimeError("wpad APK filename must identify the patched r6 build")
    if args.hostapd_common_apk.name != "hostapd-common-2025.08.26~ca266cc2-r6.apk":
        raise RuntimeError("hostapd-common APK filename must match the patched wpad r6 build")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{RELEASE_ID}-", dir=args.out.parent))
    package = stage / RELEASE_ID
    package.mkdir()
    try:
        repo = args.repo.resolve() if args.repo else None
        source, source_commit, source_epoch, source_tree_sha = helper.export_source(
            repo, None, args.source_commit, package,
        )
        source_marker = json.loads((source / ".mu300-test-source.json").read_text(encoding="utf-8"))
        source_snapshot_sha = source_marker["source_snapshot_sha256"]
        evidence = args.kernel_evidence.resolve()
        if not evidence.is_dir() or not any(path.is_file() for path in evidence.rglob("*")):
            raise RuntimeError("original kernel evidence directory is empty")
        kernel_provenance = verify_r3_kernel(
            args.kernel.resolve(), args.base_kernel.resolve(), source, repo,
            source_commit, args.module_provenance.resolve(), args.module_evidence.resolve(),
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
        native_kernel_audit["kernel_bundle_sha256"] = sha256(args.kernel.resolve())
        wifi_inputs = json.loads((source / "openwrt/sae-offload/inputs.json").read_text(encoding="utf-8"))
        for key, relative in (
            ("sae_result_patch_sha256", "openwrt/sae-offload/999-unisoc-sae-offload.patch"),
            ("ap_uc_patch_sha256", "openwrt/sae-offload/ap-uc-sae-compat.patch"),
            ("sdk_config_sha256", "openwrt/sae-offload/sdk.config"),
        ):
            if wifi_inputs.get(key) != sha256(source / relative):
                raise RuntimeError(f"reviewed Wi-Fi source pin mismatch: {relative}")
        for key, path in (("wpad_apk_sha256", args.wpad_apk),
                          ("hostapd_common_apk_sha256", args.hostapd_common_apk)):
            if wifi_inputs.get(key) != sha256(path.resolve()):
                raise RuntimeError(f"pinned Wi-Fi APK hash mismatch for {path.name}")
        wifi_evidence_path = args.wifi_build_evidence / "wifi-build-provenance.json"
        wifi_build_log = args.wifi_build_evidence / "sdk-build.log"
        wifi_evidence = json.loads(wifi_evidence_path.read_text(encoding="utf-8"))
        expected_wifi_evidence = {
            "official_sdk_archive_sha256": wifi_inputs["sdk_archive_sha256"],
            "sdk_feed_commit": wifi_inputs["sdk_feed_commit"],
            "sdk_config_sha256": wifi_inputs["sdk_config_sha256"],
            "base_hostapd_recipe_sha256": wifi_inputs["sdk_recipe_sha256"],
            "base_wpad_init_sha256": wifi_inputs["sdk_init_sha256"],
            "hostapd_patch_sha256": wifi_inputs["sae_result_patch_sha256"],
            "wpad_binary_sha256": wifi_inputs["wpad_sha256"],
        }
        if any(wifi_evidence.get(key) != value for key, value in expected_wifi_evidence.items()):
            raise RuntimeError("Wi-Fi SDK provenance differs from the reviewed input pins")
        if not wifi_build_log.is_file() or sha256(wifi_build_log) != wifi_evidence.get("sdk_build_log_sha256"):
            raise RuntimeError("Wi-Fi SDK build log is missing or changed")
        for key, path in (("wpad_apk", args.wpad_apk), ("hostapd_common_apk", args.hostapd_common_apk)):
            record = wifi_evidence.get(key, {})
            if record.get("name") != path.name or record.get("sha256") != sha256(path):
                raise RuntimeError(f"Wi-Fi SDK provenance does not identify {path.name}")
        for name in ("sdk.config", "sdk-patches.sha256", "999-unisoc-sae-offload.patch"):
            evidence_file = args.wifi_build_evidence / name
            if not evidence_file.is_file():
                raise RuntimeError(f"Wi-Fi SDK build evidence is missing {name}")
            source_file = source / "openwrt/sae-offload" / name
            if evidence_file.read_bytes() != source_file.read_bytes():
                raise RuntimeError(f"Wi-Fi SDK evidence differs from committed source: {name}")
        rootfs, packages = helper.make_rootfs(
            source, args.official_rootfs.resolve(), kernel_dir, modules,
            args.apk_index.resolve(), args.apk.resolve(), stage, provider_apk,
            wifi_apks=[args.hostapd_common_apk.resolve(), args.wpad_apk.resolve()],
            wifi_expected_versions={
                "wpad-basic-mbedtls": wifi_inputs["wpad_package_version"],
                "hostapd-common": wifi_inputs["hostapd_common_package_version"],
            },
        )
        component_paths = {
            "ap_uc_sha256": rootfs / "usr/share/ucode/wifi/ap.uc",
            "wpad_sha256": rootfs / "usr/sbin/wpad",
            "wpad_init_sha256": rootfs / "etc/init.d/wpad",
        }
        for key, path in component_paths.items():
            if not path.is_file() or sha256(path) != wifi_inputs[key]:
                raise RuntimeError(f"rootfs {path} does not match its reviewed SHA-256")
        init_text = component_paths["wpad_init_sha256"].read_text(encoding="utf-8")
        env_marker = "U30AIR_SAE_OFFLOAD=pmk-first-v1"
        hostapd_command = init_text.find("procd_set_param command /usr/sbin/hostapd")
        supplicant_command = init_text.find("procd_set_param command /usr/sbin/wpa_supplicant")
        env_position = init_text.find(env_marker)
        if (init_text.count(env_marker) != 1 or hostapd_command < 0 or
                not (hostapd_command < env_position < supplicant_command)):
            raise RuntimeError("SAE result environment is not limited to the hostapd procd instance")
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
        shutil.copy2(args.wpad_apk, release / args.wpad_apk.name)
        shutil.copy2(args.hostapd_common_apk, release / args.hostapd_common_apk.name)

        input_dir = package / "provenance/inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.apk_index, input_dir / "packages.adb")
        shutil.copy2(args.apk, input_dir / helper.APK_NAME)
        shutil.copy2(provider_apk, input_dir / NATIVE_APK_NAME)
        shutil.copy2(args.official_rootfs, input_dir / "official-openwrt-rootfs.tar.gz")
        shutil.copy2(args.base_kernel, input_dir / "mu300-kernel-7.2-base.tar.gz")
        shutil.copy2(args.wpad_apk, input_dir / args.wpad_apk.name)
        shutil.copy2(args.hostapd_common_apk, input_dir / args.hostapd_common_apk.name)
        shutil.copy2(source / "openwrt/sae-offload/inputs.json", input_dir / "sae-offload-inputs.json")

        kernel_evidence_dest = package / "provenance/kernel-build"
        shutil.copytree(evidence, kernel_evidence_dest, dirs_exist_ok=True)
        shutil.copy2(args.kernel_provenance, kernel_evidence_dest / "BUILD-PROVENANCE-ORIGINAL.json")
        (kernel_evidence_dest / "BUILD-PROVENANCE.json").write_text(
            json.dumps(kernel_provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        shutil.copytree(args.module_evidence, package / "provenance/kernel-module-build")
        shutil.copytree(args.wifi_build_evidence, package / "provenance/wifi-build")

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
            "kernel_bundle_sha256": sha256(args.kernel.resolve()),
            "kernel_bundle_base_sha256": helper.PINS["candidate_kernel"],
            "kernel_bundle_reused": False,
            "kernel_bundle_partially_rebuilt": True,
            "kernel_bundle_original_source_commit": kernel_provenance["base_kernel_original_source_commit"],
            "kernel_and_modules_freshly_built": False,
            "wifi_driver_module_freshly_built": True,
            "kernel_members_changed": kernel_provenance["archive_comparison"]["changed_members"],
            "kernel_members_reused": kernel_provenance["reused_kernel_member_count"],
            "openwrt_rootfs_sha256": sha256(release / "mu300-openwrt-luci-rootfs.tar.gz"),
            "official_input_sha256": {
                "openwrt_rootfs": helper.PINS["official_openwrt_rootfs"],
                "rollback_kernel": helper.PINS["official_rollback_kernel"],
                "signed_packages_adb": helper.PINS["openwrt_base_index"],
                "ucode_mod_socket_apk": helper.PINS["ucode_mod_socket_apk"],
                "openwrt_sdk_archive": wifi_inputs["sdk_archive_sha256"],
                "wpad_apk": wifi_inputs["wpad_apk_sha256"],
                "hostapd_common_apk": wifi_inputs["hostapd_common_apk_sha256"],
            },
            "wireless_compatibility": {
                "mode": "WPA2-PSK/WPA3-SAE mixed mode",
                "wpad_package_version": wifi_inputs["wpad_package_version"],
                "wpad_apk_sha256": wifi_inputs["wpad_apk_sha256"],
                "hostapd_common_package_version": wifi_inputs["hostapd_common_package_version"],
                "hostapd_common_apk_sha256": wifi_inputs["hostapd_common_apk_sha256"],
                "wpad_binary_sha256": wifi_inputs["wpad_sha256"],
                "wpad_init_sha256": wifi_inputs["wpad_init_sha256"],
                "ap_generator_sha256": wifi_inputs["ap_uc_sha256"],
                "driver_module_sha256": wifi_inputs["driver_sha256"],
                "service_environment_scope": "hostapd procd instance only",
                "device_component_hash_validation": {
                    "driver_module_matches_device_test": True,
                    "ap_generator_matches_device_test": True,
                    "wpad_init_matches_device_test": True,
                    "r3_wpad_binary_matches_device_test": False,
                    "r3_wpad_note": "r3 adds an SAE-only PWE guard after the device-tested wpad build; that corrected binary has not been tested on the full release image",
                },
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
            "kernel_input_file_count": len(kernel_provenance["original_kernel_input_files"]),
            "kernel_build_log_sha256": kernel_provenance["original_build_logs"],
            "fresh_module_build_log_sha256": kernel_provenance["fresh_module_build_log_sha256"],
            "generic_ramdisk_sha256": kernel_provenance["generic_ramdisk_sha256"],
            "hardware_validation": False,
            "device_install_or_boot_performed": False,
            "validation_note": (
                "This exact r3 package was not installed or booted as a whole. The driver, AP generator, and service init match device-tested hashes. The r3 wpad binary includes an additional SAE-only PWE guard regression fix and was not device-tested at this exact hash."
            ),
        }
        (release / "BUILD-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (release / "RELEASE-NOTES.md").write_text(
            render_release_doc(source, "RELEASE-NOTES-CHARGE2-R3.md"), encoding="utf-8"
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
            "kernel_bundle_update": {
                "base_sha256": helper.PINS["candidate_kernel"],
                "candidate_sha256": sha256(args.kernel.resolve()),
                "original_source_commit": kernel_provenance["base_kernel_original_source_commit"],
                "final_source_commit": source_commit,
                "verification": kernel_provenance["reuse_verification"],
                "changed_members": kernel_provenance["archive_comparison"]["changed_members"],
                "reused_member_count": kernel_provenance["reused_kernel_member_count"],
                "kernel_image_sha256": kernel_provenance["kernel_image_sha256"],
                "generic_ramdisk_sha256": kernel_provenance["generic_ramdisk_sha256"],
                "fresh_module_sha256": kernel_provenance["fresh_module_sha256"],
            },
            "inputs": {
                name: {"sha256": helper.PINS[key], "path": path.name}
                for name, key, path in (
                    ("official_openwrt_rootfs", "official_openwrt_rootfs", args.official_rootfs),
                    ("official_rollback_kernel", "official_rollback_kernel", args.rollback_kernel),
                    ("candidate_kernel", "candidate_kernel", args.base_kernel),
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
            "wifi_components": {
                "wpad_apk": {"sha256": sha256(args.wpad_apk), "path": args.wpad_apk.name},
                "hostapd_common_apk": {"sha256": sha256(args.hostapd_common_apk), "path": args.hostapd_common_apk.name},
                "component_hashes": {
                    key: wifi_inputs[key] for key in (
                        "wpad_sha256", "wpad_init_sha256", "ap_uc_sha256", "driver_sha256"
                    )
                },
                "sdk_inputs": wifi_inputs,
                "sdk_build_provenance_sha256": sha256(wifi_evidence_path),
                "sdk_build_log_sha256": sha256(wifi_build_log),
            },
            "package_recipe": "source/tools/build-charge2-release-r3.py",
            "kernel_rebuild_recipe": "source/tools/build-charge2-sae-offload-kernel.py",
            "original_kernel_provenance_sha256": sha256(args.kernel_provenance),
            "fresh_module_provenance_sha256": sha256(args.module_provenance),
        }
        (package / "provenance/BUILD-INPUTS.json").write_text(
            json.dumps(inputs, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (package / "BUILD-CHARGE2-RELEASE.md").write_text(
            render_release_doc(source, "BUILD-CHARGE2-RELEASE-R3.md"), encoding="utf-8"
        )
        (package / "README.md").write_text(
            render_release_doc(source, "README-CHARGE2-RELEASE-R3.md"), encoding="utf-8"
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
        print(f"kernel base source commit: {kernel_provenance['base_kernel_original_source_commit']}")
        print(f"kernel bundle SHA-256: {sha256(args.kernel)}")
        print(f"fresh Wi-Fi module SHA-256: {kernel_provenance['fresh_module_sha256']}")
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
        print(f"build-charge2-release-r3: {exc}", file=sys.stderr)
        raise SystemExit(1)
