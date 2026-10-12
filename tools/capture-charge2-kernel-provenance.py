#!/usr/bin/env python3
"""Verify kernel build inputs against a clean source commit and collect build evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile


KREL = "7.2.9-u30air-charge2"
KERNEL_SHA256 = "fd87b29a0720331447d9b9fb2cad044cb8b190cc5b3c3c5670a513af039aa71e"
KERNEL_SOURCE_SHA256 = "b4c5dfbe51a364a6c7f03869200f88c8e1f77403539005f14b7fc6bc91b8d8ba"
MODULE_COUNT = 379


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def bundle_file_sha(path: Path, member_name: str) -> str:
    with tarfile.open(path, "r:gz") as archive:
        candidates = [member for member in archive.getmembers() if Path(member.name).name == member_name]
        if len(candidates) != 1:
            raise RuntimeError(f"kernel bundle has {len(candidates)} {member_name} entries")
        stream = archive.extractfile(candidates[0])
        if stream is None:
            raise RuntimeError(f"kernel bundle member is not a file: {member_name}")
        h = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
        return h.hexdigest()


def run(args: list[str]) -> str:
    return subprocess.check_output(args, text=True).strip()


def source_files(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            continue
        if any(part.startswith("out-") for part in Path(rel).parts):
            continue
        if path.is_file():
            found[rel] = sha256(path)
    return found


def compiler_info() -> dict[str, str]:
    info = {"uname": run(["uname", "-a"])}
    for label, command in (
        ("gcc", ["aarch64-linux-gnu-gcc", "--version"]),
        ("linker", ["aarch64-linux-gnu-ld", "--version"]),
        ("make", ["make", "--version"]),
        ("python", [sys.executable, "--version"]),
    ):
        info[label] = subprocess.check_output(command, text=True).splitlines()[0]
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True, help="git archive exported from a clean worktree")
    parser.add_argument("--source-metadata", type=Path, required=True, help="metadata emitted by export-charge2-test-source.ps1")
    parser.add_argument("--build-root", type=Path, required=True, help="isolated WSL build root")
    parser.add_argument("--kernel-bundle", type=Path, required=True, help="candidate mu300-kernel-7.2.tar.gz")
    parser.add_argument("--out", type=Path, required=True, help="new evidence directory")
    args = parser.parse_args()
    archive_path, metadata_path, build_root, bundle, out = (
        p.resolve() for p in (args.source_archive, args.source_metadata, args.build_root, args.kernel_bundle, args.out)
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("source_worktree_clean") is not True:
        raise RuntimeError("source archive metadata does not confirm a clean Git worktree")
    source_commit = str(metadata.get("source_commit", ""))
    if len(source_commit) != 40:
        raise RuntimeError("source export metadata has an invalid commit id")
    if sha256(archive_path) != metadata.get("source_snapshot_sha256"):
        raise RuntimeError("source archive hash does not match export metadata")
    if out.exists():
        raise RuntimeError(f"evidence output already exists: {out}")
    if sha256(bundle) != KERNEL_SHA256:
        raise RuntimeError("candidate kernel bundle hash does not match the pinned test artifact")

    with tempfile.TemporaryDirectory(prefix="charge2-source-snapshot-") as temp:
        source_root = Path(temp) / "source"
        source_root.mkdir()
        with tarfile.open(archive_path, "r:") as archive:
            for member in archive.getmembers():
                name = Path(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise RuntimeError(f"unsafe source archive member: {member.name}")
            archive.extractall(source_root, filter="data")
        committed = source_files(source_root / "upstream")
        compiled = source_files(build_root / "project/upstream")
        if committed != compiled:
            missing = sorted(set(committed) - set(compiled))[:10]
            extra = sorted(set(compiled) - set(committed))[:10]
            changed = sorted(path for path in committed.keys() & compiled.keys() if committed[path] != compiled[path])[:10]
            raise RuntimeError(f"kernel source differs from final commit (missing={missing}, extra={extra}, changed={changed})")
        required = ("modules/", "port/", "u30air/", "dts/", "patches/")
        for prefix in required:
            if not any(path.startswith(prefix) for path in committed):
                raise RuntimeError(f"compiled source does not contain required kernel inputs: {prefix}")

    outdir = build_root / "src/out-7.2.9"
    kernel_source = build_root / "src/linux-7.2.9"
    module_out = build_root / "project/upstream/out-7.2"
    config = outdir / ".config"
    image = outdir / "arch/arm64/boot/Image"
    dtb = outdir / "ums9620-mu300.dtb"
    if not config.is_file() or not image.is_file() or not dtb.is_file():
        raise RuntimeError("kernel output is missing .config, Image or U30 Air DTB")
    config_hash = sha256(config)
    bundle_image_hash = bundle_file_sha(bundle, "Image")
    wrap_script = build_root / "project/upstream/wrap-image.py"
    if not wrap_script.is_file():
        raise RuntimeError("the kernel source tree is missing its LK Image wrapper")
    with tempfile.TemporaryDirectory(prefix="charge2-wrapped-image-") as temp:
        wrapped_image = Path(temp) / "Image.lk"
        subprocess.run([sys.executable, str(wrap_script), str(image), str(wrapped_image)], check=True)
        expected_bundle_image_hash = sha256(wrapped_image)
    if bundle_image_hash != expected_bundle_image_hash:
        raise RuntimeError("the bundle LK-wrapped Image differs from wrap-image.py applied to the freshly compiled Image")
    config_text = config.read_text(errors="replace")
    required_config = (
        'CONFIG_LOCALVERSION="-u30air-charge2"',
        "CONFIG_CHARGER_SGM41511_NATIVE=y", "CONFIG_USB_ROLE_SWITCH=y",
        "CONFIG_USB_DWC3=y", "CONFIG_USB_DWC3_DUAL_ROLE=y",
        "CONFIG_SPRD_TYPEC_TCPM=y", "CONFIG_SC27XX_PD=y",
        "CONFIG_USB_SC27XX_TYPEC=y", "CONFIG_USB_RTL8152=y",
    )
    for option in required_config:
        if option not in config_text:
            raise RuntimeError(f"kernel .config is missing {option}")
    modules = sorted((module_out / "modules").glob("*.ko"))
    if len(modules) != MODULE_COUNT:
        raise RuntimeError(f"expected {MODULE_COUNT} built modules, found {len(modules)}")
    if sha256(bundle) != KERNEL_SHA256:
        raise RuntimeError("candidate kernel bundle hash changed during provenance capture")

    logs = {
        "kernel-image-in-tree.log": build_root / "logs/kernel-image-in-tree.log",
        "kernel-build.log": outdir / "build.log",
        "kernel-modules.log": outdir / "modules.log",
    }
    vendor_logs = sorted((module_out / "modules").glob("*.log"))
    for path in vendor_logs:
        logs[f"vendor-modules/{path.name}"] = path
    if any(not path.is_file() for path in logs.values()):
        raise RuntimeError("one or more kernel build logs are missing")

    out.mkdir(parents=True)
    shutil.copy2(config, out / "kernel.config")
    for name, path in logs.items():
        target = out / "logs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    (out / "kernel-source.txt").write_text(
        f"version: Linux 7.2.9\n"
        f"url: https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.9.tar.xz\n"
        f"sha256: {KERNEL_SOURCE_SHA256}\n"
        "checksum_source: kernel.org sha256sums.asc (the upstream build script checks the archive before extraction)\n",
        encoding="utf-8",
    )
    environment = compiler_info()
    shutil.copy2(metadata_path, out / "SOURCE-EXPORT-METADATA.json")
    (out / "compiler-and-host.json").write_text(
        json.dumps(environment, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (out / "artifact-hashes.txt").open("w", encoding="ascii") as f:
        f.write(f"{sha256(bundle)}  mu300-kernel-7.2.tar.gz\n")
        f.write(f"{sha256(image)}  Image (fresh kernel build)\n")
        f.write(f"{bundle_image_hash}  Image (LK-wrapped in kernel bundle)\n")
        f.write(f"{sha256(dtb)}  ums9620-mu300.dtb\n")
        f.write(f"{config_hash}  kernel.config\n")

    log_hashes = {name: sha256(path) for name, path in logs.items()}
    provenance = {
        "source_commit": source_commit,
        "source_snapshot_sha256": metadata["source_snapshot_sha256"],
        "candidate_kernel_sha256": KERNEL_SHA256,
        "kernel_release": KREL,
        "kernel_source": {
            "version": "7.2.9",
            "url": "https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.9.tar.xz",
            "sha256": KERNEL_SOURCE_SHA256,
            "checksum_source": "kernel.org sha256sums.asc",
        },
        "compiler": environment,
        "config_sha256": config_hash,
        "kernel_image_sha256": sha256(image),
        "kernel_bundle_image_sha256": bundle_image_hash,
        "kernel_bundle_image_wrap_verified": "upstream/wrap-image.py output matched byte-for-byte",
        "u30air_dtb_sha256": sha256(dtb),
        "module_count": len(modules),
        "build_logs": log_hashes,
        "kernel_input_files": [
            {"path": f"upstream/{rel}", "build_sha256": compiled[rel], "commit_sha256": committed[rel]}
            for rel in sorted(committed)
        ],
    }
    (out / "BUILD-PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"source commit: {source_commit}")
    print(f"matching kernel inputs: {len(committed)} files")
    print(f"config SHA-256: {config_hash}")
    print(f"kernel bundle SHA-256: {KERNEL_SHA256}")
    print(f"evidence: {out}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"capture-charge2-kernel-provenance: {exc}", file=sys.stderr)
        raise SystemExit(1)
