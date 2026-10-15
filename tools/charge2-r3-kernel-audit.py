"""Strict source and artifact checks for the U30 Air charge2 r3 kernel delta."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

BASE_COMMIT = "eb55c1751827b1454298c6b7df24505ff2139cb5"
KREL = "7.2.9-u30air-charge2"
ALLOWED = {
    "upstream/modules/sprd_wlan_combo/common/cfg80211.c",
    "upstream/modules/sprd_wlan_combo/sc2355/cmdevt.c",
    "upstream/modules/sprd_wlan_combo/sc2355/init.c",
    "upstream/modules/sprd_wlan_combo/sc2355/vendor.c",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load audit helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(kernel: Path, base_kernel: Path, source: Path, repo: Path,
           source_commit: str, module_provenance_path: Path,
           module_evidence: Path, original_provenance_path: Path,
           original_evidence: Path, base_kernel_sha256: str,
           expected_config_sha256: str) -> dict:
    """Bind r3 source, clean module build, and candidate bundle to reviewed inputs."""
    release_helper = _load("charge2_r3_test_helper", Path(__file__).with_name("build-charge2-test.py"))
    module_builder = _load("charge2_r3_module_builder", Path(__file__).with_name("build-charge2-sae-offload-kernel.py"))
    if sha256(base_kernel) != base_kernel_sha256:
        raise RuntimeError("base kernel does not match the audited r2 bundle")
    original = json.loads(original_provenance_path.read_text(encoding="utf-8"))
    fresh = json.loads(module_provenance_path.read_text(encoding="utf-8"))
    if original.get("candidate_kernel_sha256") != base_kernel_sha256:
        raise RuntimeError("original kernel provenance identifies a different base bundle")
    if original.get("kernel_release") != KREL or original.get("module_count") != release_helper.MODULE_COUNT:
        raise RuntimeError("original kernel provenance has the wrong release or module count")
    if fresh.get("source_commit") != source_commit or fresh.get("upstream_baseline_commit") != BASE_COMMIT:
        raise RuntimeError("fresh module provenance is bound to another source commit or baseline")
    if fresh.get("base_kernel_sha256") != base_kernel_sha256 or fresh.get("candidate_kernel_sha256") != sha256(kernel):
        raise RuntimeError("fresh module provenance identifies different kernel bundles")
    if fresh.get("kernel_release") != KREL or fresh.get("module_count") != release_helper.MODULE_COUNT:
        raise RuntimeError("fresh module provenance has the wrong release or module count")
    inputs = json.loads((source / "openwrt/sae-offload/inputs.json").read_text(encoding="utf-8"))
    if fresh.get("kernel_output_config_sha256") != expected_config_sha256:
        raise RuntimeError("fresh module build used a different Kbuild .config")
    if fresh.get("wcn_symbols_sha256") != inputs.get("wcn_symbols_sha256"):
        raise RuntimeError("fresh module provenance identifies a different WCN Module.symvers")
    if fresh.get("external_header_inputs") != inputs.get("wcn_external_header_files"):
        raise RuntimeError("fresh module provenance identifies different external WCN headers")
    module_sha = inputs.get("driver_sha256")
    if not module_sha or fresh.get("freshly_built_module_sha256") != module_sha:
        raise RuntimeError("freshly built driver does not match the reviewed module hash")
    log = module_evidence / "module-build.log"
    if not log.is_file() or sha256(log) != fresh.get("module_build_log_sha256"):
        raise RuntimeError("fresh module build log is missing or changed")

    changed = set(filter(None, subprocess.check_output(
        ["git", "-C", str(repo), "diff", "--name-only", BASE_COMMIT, source_commit, "--", "upstream"],
        text=True,
    ).splitlines()))
    if changed != ALLOWED:
        raise RuntimeError(f"upstream source delta is outside the four reviewed driver files: {sorted(changed)}")

    original_logs = original.get("build_logs")
    if not isinstance(original_logs, dict) or not original_logs:
        raise RuntimeError("original kernel provenance has no build-log hashes")
    for rel, expected in original_logs.items():
        log_path = original_evidence / "logs" / rel
        if not log_path.is_file() or sha256(log_path) != expected:
            raise RuntimeError(f"original kernel build log is missing or changed: {rel}")
    config = original_evidence / "kernel.config"
    if not config.is_file() or sha256(config) != expected_config_sha256 or sha256(config) != original.get("config_sha256"):
        raise RuntimeError("original effective kernel configuration does not match the audited pin")

    records = original.get("kernel_input_files")
    if not isinstance(records, list) or not records:
        raise RuntimeError("original kernel source input list is missing")
    record_map = {}
    for record in records:
        rel = str(record.get("path", ""))
        if not rel.startswith("upstream/") or ".." in Path(rel).parts or rel in record_map:
            raise RuntimeError(f"invalid or duplicate original kernel input path: {rel!r}")
        if record.get("build_sha256") != record.get("commit_sha256"):
            raise RuntimeError(f"original kernel input was not built from committed bytes: {rel}")
        record_map[rel] = record
    expected_files = {
        p.relative_to(source).as_posix()
        for p in (source / "upstream").rglob("*")
        if p.is_file() and not any(part.startswith("out-") for part in p.relative_to(source).parts)
    }
    if set(record_map) != expected_files:
        missing, extra = sorted(expected_files - set(record_map))[:5], sorted(set(record_map) - expected_files)[:5]
        raise RuntimeError(f"original kernel input coverage changed (missing={missing}, extra={extra})")
    current_hashes = {}
    for rel, record in record_map.items():
        if not (source / rel).is_file():
            raise RuntimeError(f"original kernel input is absent from final source: {rel}")
        current = sha256(source / rel)
        if rel in ALLOWED:
            baseline = subprocess.check_output(["git", "-C", str(repo), "show", f"{BASE_COMMIT}:{rel}"])
            if hashlib.sha256(baseline).hexdigest() != record["build_sha256"]:
                raise RuntimeError(f"original kernel evidence differs from baseline source: {rel}")
        elif current != record["build_sha256"]:
            raise RuntimeError(f"unreviewed upstream kernel source changed: {rel}")
        current_hashes[rel] = current

    fresh_inputs = fresh.get("module_source_inputs")
    if not isinstance(fresh_inputs, list) or not fresh_inputs:
        raise RuntimeError("fresh module provenance omits source hashes")
    module_root = "upstream/modules/sprd_wlan_combo/"
    fresh_map = {}
    for item in fresh_inputs:
        rel = str(item.get("path", ""))
        full = module_root + rel
        digest = str(item.get("sha256", ""))
        if not rel or ".." in Path(rel).parts or full in fresh_map or not digest:
            raise RuntimeError(f"invalid fresh module input: {rel!r}")
        if not (source / full).is_file() or sha256(source / full) != digest:
            raise RuntimeError(f"fresh module input differs from committed bytes: {full}")
        fresh_map[full] = digest
    expected_module_inputs = {
        p.relative_to(source).as_posix()
        for p in (source / "upstream/modules/sprd_wlan_combo").rglob("*")
        if p.is_file() and not p.is_symlink()
    }
    if set(fresh_map) != expected_module_inputs:
        raise RuntimeError("fresh module source-input manifest is incomplete")

    comparison = module_builder.compare_bundles(base_kernel, kernel)
    if comparison.get("changed_members") != ["./modules/sprd_wlan_combo.ko"]:
        raise RuntimeError("kernel tar members changed beyond sprd_wlan_combo.ko")
    temp = Path(tempfile.mkdtemp(prefix="mu300-r3-kernel-audit-"))
    try:
        old, new = temp / "base", temp / "candidate"
        old.mkdir(); new.mkdir()
        old_modules = release_helper.extract_kernel(base_kernel, old)
        new_modules = release_helper.extract_kernel(kernel, new)
        if len(old_modules) != release_helper.MODULE_COUNT or len(new_modules) != release_helper.MODULE_COUNT:
            raise RuntimeError("base or candidate kernel has the wrong module count")
        for name in ("Image", "ramdisk-generic.lz4"):
            if sha256(old / name) != sha256(new / name):
                raise RuntimeError(f"kernel {name} changed in r3")
        old_by_name = {p.name: p for p in old_modules}
        new_by_name = {p.name: p for p in new_modules}
        if set(old_by_name) != set(new_by_name):
            raise RuntimeError("base and candidate kernel module lists differ")
        for name in old_by_name:
            if name != "sprd_wlan_combo.ko" and sha256(old_by_name[name]) != sha256(new_by_name[name]):
                raise RuntimeError(f"kernel module changed beyond the reviewed Wi-Fi driver: {name}")
        if sha256(new_by_name["sprd_wlan_combo.ko"]) != module_sha:
            raise RuntimeError("kernel bundle does not contain the reviewed Wi-Fi module")
        image_hash = sha256(new / "Image")
        ramdisk_hash = sha256(new / "ramdisk-generic.lz4")
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    marker = json.loads((source / ".mu300-test-source.json").read_text(encoding="utf-8"))
    return {
        "source_commit": source_commit,
        "source_snapshot_sha256": marker["source_snapshot_sha256"],
        "kernel_release": KREL,
        "module_count": release_helper.MODULE_COUNT,
        "candidate_kernel_sha256": sha256(kernel),
        "base_kernel_sha256": sha256(base_kernel),
        "kernel_bundle_reused": False,
        "kernel_bundle_partially_rebuilt": True,
        "base_kernel_original_source_commit": original.get("source_commit"),
        "original_build_provenance_sha256": sha256(original_provenance_path),
        "kernel_source": original.get("kernel_source"),
        "compiler": original.get("compiler"),
        "config_sha256": original.get("config_sha256"),
        "original_build_logs": original_logs,
        "original_kernel_input_files": records,
        "final_source_kernel_input_sha256": current_hashes,
        "fresh_module_build_provenance_sha256": sha256(module_provenance_path),
        "fresh_module_build_log_sha256": fresh["module_build_log_sha256"],
        "fresh_module_sha256": module_sha,
        "kernel_image_sha256": image_hash,
        "generic_ramdisk_sha256": ramdisk_hash,
        "archive_comparison": comparison,
        "reused_kernel_member_count": len(comparison["members"]) - 1,
        "reuse_verification": "all original upstream inputs except the four reviewed Wi-Fi files are byte-identical; the fresh Wi-Fi module is the only changed kernel archive member",
    }
