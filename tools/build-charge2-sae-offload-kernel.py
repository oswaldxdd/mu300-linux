#!/usr/bin/env python3
"""Freshly rebuild SC2355 Wi-Fi module and replace only that member in the audited kernel bundle."""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

BASE_COMMIT = "eb55c1751827b1454298c6b7df24505ff2139cb5"
KREL = "7.2.9-u30air-charge2"
MODULE_MEMBER = "./modules/sprd_wlan_combo.ko"
ALLOWED_UPSTREAM_CHANGES = {
    "upstream/modules/sprd_wlan_combo/common/cfg80211.c",
    "upstream/modules/sprd_wlan_combo/sc2355/cmdevt.c",
    "upstream/modules/sprd_wlan_combo/sc2355/init.c",
    "upstream/modules/sprd_wlan_combo/sc2355/vendor.c",
}
HELPER_SPEC = importlib.util.spec_from_file_location(
    "charge2_r3_helper", Path(__file__).with_name("build-charge2-test.py"))
if HELPER_SPEC is None or HELPER_SPEC.loader is None:
    raise RuntimeError("cannot import shared kernel bundle validator")
helper = importlib.util.module_from_spec(HELPER_SPEC)
HELPER_SPEC.loader.exec_module(helper)


def sha256(data: bytes | Path) -> str:
    h = hashlib.sha256()
    if isinstance(data, Path):
        with data.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
    else:
        h.update(data)
    return h.hexdigest()


def run(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        log: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    if log is not None:
        with log.open("a", encoding="utf-8") as stream:
            stream.write("$ " + " ".join(args) + "\n")
            stream.write(result.stdout)
            if not result.stdout.endswith("\n"):
                stream.write("\n")
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout[-4000:]}")
    return result.stdout.strip()


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def safe_archive_index(path: Path) -> tuple[list[tarfile.TarInfo], dict[str, str]]:
    entries = []
    content_hashes = {}
    with tarfile.open(path, "r:gz") as archive:
        seen = set()
        for info in archive.getmembers():
            name = info.name
            parts = Path(name).parts
            if Path(name).is_absolute() or ".." in parts or name in seen:
                raise RuntimeError(f"unsafe or duplicate kernel archive entry: {name}")
            seen.add(name)
            entries.append(copy.copy(info))
            if info.isfile():
                stream = archive.extractfile(info)
                if stream is None:
                    raise RuntimeError(f"cannot read kernel archive member: {name}")
                content_hashes[name] = sha256(stream.read())
    return entries, content_hashes


def copy_archive_with_module(base: Path, module: Path, output: Path) -> dict:
    with tarfile.open(base, "r:gz") as source:
        members = source.getmembers()
        targets = [item for item in members if item.name == MODULE_MEMBER]
        if len(targets) != 1 or not targets[0].isfile():
            raise RuntimeError(f"base kernel must contain exactly one {MODULE_MEMBER}")
        with output.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as target:
                    for info in members:
                        updated = copy.copy(info)
                        if info.name == MODULE_MEMBER:
                            payload = module.read_bytes()
                            updated.size = len(payload)
                            target.addfile(updated, io.BytesIO(payload))
                        elif info.isfile():
                            stream = source.extractfile(info)
                            if stream is None:
                                raise RuntimeError(f"cannot copy kernel member {info.name}")
                            target.addfile(updated, stream)
                        else:
                            target.addfile(updated)
    return {item.name: item for item in members}


def compare_bundles(base: Path, candidate: Path) -> dict:
    with tarfile.open(base, "r:gz") as old, tarfile.open(candidate, "r:gz") as new:
        old_items = old.getmembers()
        new_items = new.getmembers()
        if [item.name for item in old_items] != [item.name for item in new_items]:
            raise RuntimeError("new kernel bundle changed the archive member list or order")
        changed = []
        result = {}
        for left, right in zip(old_items, new_items):
            fields = ("name", "type", "mode", "uid", "gid", "uname", "gname", "linkname",
                      "devmajor", "devminor", "mtime", "pax_headers")
            metadata_equal = all(getattr(left, field) == getattr(right, field) for field in fields)
            if left.isfile() and right.isfile():
                a, b = old.extractfile(left), new.extractfile(right)
                if a is None or b is None:
                    raise RuntimeError(f"cannot read compared member: {left.name}")
                left_hash, right_hash = sha256(a.read()), sha256(b.read())
            else:
                left_hash = right_hash = ""
            content_equal = left_hash == right_hash and left.size == right.size
            if not metadata_equal or not content_equal:
                changed.append(left.name)
            if left.name != MODULE_MEMBER and (not metadata_equal or not content_equal):
                raise RuntimeError(f"unexpected kernel bundle change outside Wi-Fi module: {left.name}")
            result[left.name] = {
                "type": "file" if left.isfile() else "directory-or-link",
                "base_sha256": left_hash or None,
                "candidate_sha256": right_hash or None,
                "metadata_unchanged": metadata_equal,
                "content_unchanged": content_equal,
            }
        if changed != [MODULE_MEMBER]:
            raise RuntimeError(f"expected only {MODULE_MEMBER} to change, got {changed}")
        return {"changed_members": changed, "members": result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base-kernel", type=Path, required=True)
    parser.add_argument("--base-provenance", type=Path, required=True)
    parser.add_argument("--base-evidence", type=Path, required=True)
    parser.add_argument("--kernel-tree", type=Path, required=True)
    parser.add_argument("--kernel-output", type=Path, required=True)
    parser.add_argument("--wcn-symbols", type=Path, required=True)
    parser.add_argument("--output-kernel", type=Path, required=True)
    parser.add_argument("--evidence-out", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo.resolve()
    for name in ("base_kernel", "base_provenance", "base_evidence", "kernel_tree",
                 "kernel_output", "wcn_symbols"):
        value = getattr(args, name).resolve()
        setattr(args, name, value)
        if not value.exists():
            raise RuntimeError(f"{name} does not exist: {value}")
    args.output_kernel = args.output_kernel.resolve()
    args.evidence_out = args.evidence_out.resolve()
    if args.output_kernel.exists() or args.evidence_out.exists():
        raise RuntimeError("new kernel output and module evidence paths must not already exist")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("source worktree must be clean and committed before a fresh kernel build")
    head = git(repo, "rev-parse", "HEAD")
    if git(repo, "cat-file", "-t", BASE_COMMIT) != "commit":
        raise RuntimeError("reviewed source baseline commit is missing")
    changed = set(filter(None, git(repo, "diff", "--name-only", BASE_COMMIT, head, "--", "upstream").splitlines()))
    if changed != ALLOWED_UPSTREAM_CHANGES:
        raise RuntimeError(f"upstream diff differs from the four reviewed Wi-Fi files: {sorted(changed)}")

    inputs = json.loads((repo / "openwrt/sae-offload/inputs.json").read_text(encoding="utf-8"))
    helper.require_pin(args.base_kernel, "candidate_kernel")
    if sha256(args.base_kernel) != inputs["base_kernel_sha256"]:
        raise RuntimeError("base kernel does not match the SAE release input pin")
    original = json.loads(args.base_provenance.read_text(encoding="utf-8"))
    if original.get("candidate_kernel_sha256") != inputs["base_kernel_sha256"]:
        raise RuntimeError("base kernel provenance names a different kernel bundle")
    if original.get("kernel_release") != KREL or original.get("module_count") != helper.MODULE_COUNT:
        raise RuntimeError("base kernel provenance has the wrong kernel release or module count")
    for rel, expected_hash in original.get("build_logs", {}).items():
        log_path = args.base_evidence / "logs" / rel
        if not log_path.is_file() or sha256(log_path) != expected_hash:
            raise RuntimeError(f"original kernel build log is missing or changed: {rel}")
    config = args.base_evidence / "kernel.config"
    if not config.is_file() or sha256(config) != original.get("config_sha256"):
        raise RuntimeError("original kernel configuration evidence is missing or changed")
    kbuild_config = args.kernel_output / ".config"
    if not kbuild_config.is_file() or sha256(kbuild_config) != original.get("config_sha256"):
        raise RuntimeError("actual Kbuild output .config differs from the audited kernel configuration")
    if sha256(args.wcn_symbols) != inputs.get("wcn_symbols_sha256"):
        raise RuntimeError("WCN Module.symvers differs from the trusted build-input pin")
    wcn_root = args.wcn_symbols.parent
    header_root = wcn_root / "kinclude"
    expected_headers = inputs.get("wcn_external_header_files")
    if not isinstance(expected_headers, list) or not expected_headers:
        raise RuntimeError("trusted external WCN header pins are missing")
    header_pins = {}
    for item in expected_headers:
        rel = str(item.get("path", ""))
        digest = str(item.get("sha256", ""))
        rel_path = Path(rel)
        if (not rel.startswith("kinclude/") or rel_path.is_absolute() or
                ".." in rel_path.parts or rel in header_pins or not digest):
            raise RuntimeError(f"invalid or duplicate external WCN header pin: {rel!r}")
        path = wcn_root / rel_path
        if not path.is_file() or path.is_symlink() or sha256(path) != digest:
            raise RuntimeError(f"external WCN header differs from trusted pin: {rel}")
        header_pins[rel] = digest
    actual_headers = {
        "kinclude/" + path.relative_to(header_root).as_posix()
        for path in header_root.rglob("*") if path.is_file() and not path.is_symlink()
    }
    if actual_headers != set(header_pins):
        raise RuntimeError("external WCN kinclude file set differs from the trusted input manifest")
    if any(path.is_symlink() for path in header_root.rglob("*")):
        raise RuntimeError("external WCN kinclude tree contains an unpinned symlink")
    records = original.get("kernel_input_files")
    if not isinstance(records, list) or not records:
        raise RuntimeError("base kernel provenance has no source-input records")
    record_map = {item.get("path"): item for item in records}
    if set(record_map) != {
        p.relative_to(repo).as_posix() for p in (repo / "upstream").rglob("*")
        if p.is_file() and not any(part.startswith("out-") for part in p.relative_to(repo).parts)
    }:
        raise RuntimeError("base provenance input coverage differs from the upstream tree")
    for rel, record in record_map.items():
        baseline_hash = record.get("commit_sha256")
        if baseline_hash != record.get("build_sha256"):
            raise RuntimeError(f"base kernel was not built from committed bytes: {rel}")
        if rel in ALLOWED_UPSTREAM_CHANGES:
            baseline_bytes = subprocess.check_output(["git", "-C", str(repo), "show", f"{BASE_COMMIT}:{rel}"])
            if sha256(baseline_bytes) != baseline_hash:
                raise RuntimeError(f"baseline Git bytes disagree with original kernel evidence: {rel}")
        elif sha256(repo / rel) != baseline_hash:
            raise RuntimeError(f"unreviewed upstream source change: {rel}")
    for rel in ALLOWED_UPSTREAM_CHANGES:
        if not (repo / rel).is_file():
            raise RuntimeError(f"reviewed driver source file is missing: {rel}")

    evidence = args.evidence_out
    evidence.mkdir(parents=True)
    log = evidence / "module-build.log"
    log.write_text("U30 Air r3 fresh SC2355 module build\n", encoding="utf-8")
    actual_release = run(["make", "-s", "-C", str(args.kernel_tree), f"O={args.kernel_output}",
                         "ARCH=arm64", "CROSS_COMPILE=aarch64-linux-gnu-", "kernelrelease"])
    if actual_release != KREL:
        raise RuntimeError(f"kernel tree reports {actual_release}, expected {KREL}")

    temp = Path(tempfile.mkdtemp(prefix="u30air-r3-module-", dir=args.wcn_symbols.parent.parent))
    try:
        (temp / "wcn_bsp").symlink_to(args.wcn_symbols.parent, target_is_directory=True)
        module_src = temp / "sprd_wlan_combo"
        shutil.copytree(repo / "upstream/modules/sprd_wlan_combo", module_src, symlinks=True)
        source_inputs = []
        for p in sorted(module_src.rglob("*")):
            if p.is_file() and not p.is_symlink():
                source_inputs.append({
                    "path": p.relative_to(module_src).as_posix(),
                    "sha256": sha256(p),
                })
        committed_inputs = []
        for item in source_inputs:
            rel = "upstream/modules/sprd_wlan_combo/" + item["path"]
            if not (repo / rel).is_file() or sha256(repo / rel) != item["sha256"]:
                raise RuntimeError(f"copied module build input differs from committed source: {rel}")
            committed_inputs.append({"path": item["path"], "sha256": item["sha256"]})
        env = os.environ.copy()
        env.update({"ARCH": "arm64", "CROSS_COMPILE": "aarch64-linux-gnu-"})
        common = ["make", "-C", str(args.kernel_tree), f"O={args.kernel_output}",
                  "ARCH=arm64", "CROSS_COMPILE=aarch64-linux-gnu-",
                  f"M={module_src}", f"KBUILD_EXTRA_SYMBOLS={args.wcn_symbols}"]
        run(common + ["-j8", "clean"], env=env, log=log)
        run(common + ["-j8", "modules"], env=env, log=log)
        module = module_src / "sprd_wlan_combo.ko"
        if not module.is_file():
            raise RuntimeError("fresh module build produced no sprd_wlan_combo.ko")
        run(["aarch64-linux-gnu-strip", "--strip-debug", str(module)], log=log)
        module_hash = sha256(module)
        if module_hash != inputs["driver_sha256"]:
            raise RuntimeError(f"fresh module SHA-256 {module_hash} differs from approved {inputs['driver_sha256']}")
        data = module.read_bytes()
        if data[:6] != b"\x7fELF\x02\x01" or int.from_bytes(data[18:20], "little") != 183:
            raise RuntimeError("fresh module is not an AArch64 ELF")
        if f"vermagic={KREL} SMP preempt mod_unload aarch64".encode() not in data:
            raise RuntimeError("fresh module vermagic differs from the target U30 Air kernel")

        args.output_kernel.parent.mkdir(parents=True, exist_ok=True)
        copy_archive_with_module(args.base_kernel, module, args.output_kernel)
        comparison = compare_bundles(args.base_kernel, args.output_kernel)
        unpacked = temp / "candidate-kernel"
        unpacked.mkdir()
        modules = helper.extract_kernel(args.output_kernel, unpacked)
        if len(modules) != helper.MODULE_COUNT:
            raise RuntimeError("candidate kernel module count changed")
        if sha256(unpacked / "modules/sprd_wlan_combo.ko") != module_hash:
            raise RuntimeError("candidate kernel does not contain the freshly built Wi-Fi module")

        compiler = {
            "gcc": run(["aarch64-linux-gnu-gcc", "--version"]).splitlines()[0],
            "strip": run(["aarch64-linux-gnu-strip", "--version"]).splitlines()[0],
            "make": run(["make", "--version"]).splitlines()[0],
            "kernel_release": actual_release,
        }
        provenance = {
            "schema": 1,
            "source_commit": head,
            "upstream_baseline_commit": BASE_COMMIT,
            "reviewed_upstream_changes": sorted(ALLOWED_UPSTREAM_CHANGES),
            "base_kernel_sha256": sha256(args.base_kernel),
            "candidate_kernel_sha256": sha256(args.output_kernel),
            "kernel_release": KREL,
            "module_count": len(modules),
            "kernel_output_config_sha256": sha256(args.kernel_output / ".config"),
            "wcn_symbols_sha256": sha256(args.wcn_symbols),
            "external_header_inputs": expected_headers,
            "freshly_rebuilt_module": MODULE_MEMBER.removeprefix("./"),
            "freshly_built_module_sha256": module_hash,
            "module_build_log_sha256": sha256(log),
            "module_source_inputs": committed_inputs,
            "compiler": compiler,
            "archive_comparison": comparison,
            "kernel_image_sha256": sha256(unpacked / "Image"),
            "ramdisk_sha256": sha256(unpacked / "ramdisk-generic.lz4"),
            "other_modules_unchanged": True,
        }
        (evidence / "MODULE-BUILD-PROVENANCE.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"candidate kernel: {args.output_kernel}")
        print(f"candidate kernel SHA-256: {provenance['candidate_kernel_sha256']}")
        print(f"fresh module SHA-256: {module_hash}")
        print(f"module build log: {log}")
        print(f"module provenance: {evidence / 'MODULE-BUILD-PROVENANCE.json'}")
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError,
            tarfile.TarError) as exc:
        print(f"build-charge2-sae-offload-kernel: {exc}", file=sys.stderr)
        raise SystemExit(1)
