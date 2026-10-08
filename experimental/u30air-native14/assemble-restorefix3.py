#!/usr/bin/env python3
"""Create a separate restorefix3 offline candidate from the immutable restorefix2 payload."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
WORKSPACE = HERE.parents[2]
OLD = HERE / "restorefix2-release"
OLD_ARCHIVE_NAME = "u30air-native14-firmware.tar.gz"
OLD_SOURCE_NAME = "corresponding-project-source.tar.gz"
VERSION = "u30air-native14-restorefix3-2026.10.08-test"
KREL = "7.2.8-u30air-native14"
RELEASE_NAME = "restorefix3-release"
ZIP_NAME = "u30air-native14-restorefix3-2026.10.08-test.zip"
ZIP_PREFIX = "u30air-native14-restorefix3"
UPDATER_PATH = "./opt/mu300/bin/mu300-update"
RUNTIME_UPDATER = HERE.parent / "runtime-update" / "mu300-update"

EXPECTED_OLD_ARCHIVE = "140be200ef1c6a0c9122d08e0191a6e3d6d8aa529e6cff25d52d7f1147b1b868"
EXPECTED_OLD_SOURCE = "daab8086ad771a807a827737b65707ab8973618a9abc80b7b2a55ece1de16bff"
EXPECTED_OLD_ZIP = "e73903f55eab744e7a8a75803a55c608a8d201f1c301f2c184f42a9e129f44d6"
EXPECTED_OLD_UPDATER = "fc64d98d0e76b2f20c9d5046cb08af5cf839f9e3defcdc5c94bf0eee7e82b955"
EXPECTED_UPDATER = "9a6f94fa86caa57dfcfa0594fcb357e36e39158513a8f22bf11174d9aa15b606"
EXPECTED_INSTALLER = "10eb29b03965be8593f3e5c9297e6a7670dc7ec00413a5f21dfdf8db00de2697"
EXPECTED_ROLLBACK = "4511f7e3559124d6987e5f2e3e0e3f702dbe4e27485ece9fefe28b6e58af5f5e"
EXPECTED_TEST = "8bd9df65b7f6ecc8d5ccf848aca32b825399f745fb5dd97d97bc0b1af462ed47"
EXPECTED_UPDATER_TEST = "7de8fc82f85092dc58a26e46a80195a7b68e8f1785bde854784216a002484fcd"
EXPECTED_TEST_LOG = "e9c07fcff776456ccd53ff58b3f24adaa40ca4eb492df1a3b979b9b4ae96643c"
EXPECTED_UPDATER_TEST_LOG = "a16982193439d11227e10dfbca8b8d5d08d633c3c177d2e2638126f79c45f176"
EXPECTED_OBSERVER = "d32f92573fba077912b9f9fb781eaa555065d9ff9c04efc4fdba2f8b1e2857ce"
EXPECTED_DISCHARGE = "20d66ba3266066331aab1994e9ceeb957e4dcd5457b283fa2705ab3e450bcbd1"
EXPECTED_AWAIT_DISCHARGE = "e9312d6c26af2d29dd6add48d53bdc0e2dea7a0ad3f9c84cbacac0b3b85152f7"
EXPECTED_FCC_STATUS = "f06c00c95a75a1aa82e0e1e818d54d39a1aa6f843da8c7851467a04981454fac"
EXPECTED_FCC_LIBRARY = "afc7805eab8531142e7c2b6e04d0bc94932aa2539b57edf8e73003ecea141000"
EXPECTED_FCC_SERVICE = "6f5c63cf61440b6ba2d7f64989c66a198743f5ad93e71252a187a5ed3d693da1"
EXPECTED_BUILTIN = "4f42d4a5a983557e8db9da4611e1aebe7382581b8a45ae9107cedc6dac7aa37a"
EXPECTED_BUILTIN_MODINFO = "aa2b5e3f03b7c4f9309d8246cdbbe676ac0df4fe7982a8e38f7bf0205f55aee7"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_sum_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        digest, name = line.split("  ", 1)
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"malformed SHA256SUMS line in {path}: {line!r}")
        if name in result or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
            raise ValueError(f"duplicate or unsafe checksum path in {path}: {name!r}")
        result[name] = digest
    return result


def verify_sum_file(base: Path, sum_path: Path, exclude: set[str] = frozenset()) -> None:
    expected = read_sum_file(sum_path)
    actual_names = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}
    actual_names -= exclude
    if actual_names != set(expected):
        raise ValueError(f"file set differs from {sum_path}: missing={set(expected)-actual_names}, extra={actual_names-set(expected)}")
    for name, digest in expected.items():
        if sha_file(base / Path(*PurePosixPath(name).parts)) != digest:
            raise ValueError(f"SHA-256 mismatch: {name}")


def validate_old_inputs() -> tuple[dict, bytes, bytes]:
    if not OLD.is_dir():
        raise FileNotFoundError(f"pinned prior release missing: {OLD}")
    verify_sum_file(OLD, OLD / "SHA256SUMS", exclude={"SHA256SUMS"})
    old_archive = OLD / OLD_ARCHIVE_NAME
    if sha_file(old_archive) != EXPECTED_OLD_ARCHIVE:
        raise ValueError("immutable prior firmware archive hash changed")
    delivery = json.loads((OLD / "delivery.json").read_text(encoding="utf-8"))
    if delivery.get("archive") != OLD_ARCHIVE_NAME or delivery.get("sha256") != EXPECTED_OLD_ARCHIVE:
        raise ValueError("prior delivery.json does not match the pinned firmware archive")
    old_source = OLD / OLD_SOURCE_NAME
    if sha_file(old_source) != EXPECTED_OLD_SOURCE:
        raise ValueError("immutable restorefix2 source archive hash changed")
    with tarfile.open(old_archive, "r:gz") as outer:
        package_manifest = outer.extractfile("package/BUILD-MANIFEST.json").read()
        package_sums = outer.extractfile("package/SHA256SUMS").read().decode("utf-8")
        package_files: dict[str, bytes] = {}
        for line in package_sums.splitlines():
            if not line:
                continue
            digest, name = line.split("  ", 1)
            member_name = "package/" + name
            payload = outer.extractfile(member_name).read()
            if sha_bytes(payload) != digest:
                raise ValueError(f"prior nested package checksum failed: {name}")
            package_files[name] = payload
        if "mu300-kernel-7.2.8-u30air-native14.tar.gz" not in package_files:
            raise ValueError("pinned prior package lacks the matching kernel bundle")
        if "mu300-openwrt-rootfs.tar.gz" not in package_files:
            raise ValueError("pinned prior package lacks the rootfs")
        if sha_bytes(package_files.get("mu300-update", b"")) != EXPECTED_OLD_UPDATER:
            raise ValueError("frozen restorefix2 package updater hash changed")
        with tarfile.open(fileobj=io.BytesIO(package_files["mu300-openwrt-rootfs.tar.gz"]), mode="r:gz") as rootfs:
            updater_members = [m for m in rootfs.getmembers() if m.name == UPDATER_PATH]
            if len(updater_members) != 1 or not updater_members[0].isreg() or not updater_members[0].mode & 0o111:
                raise ValueError("frozen restorefix2 rootfs updater is not exactly one executable regular file")
            root_updater = rootfs.extractfile(updater_members[0]).read()
            if sha_bytes(root_updater) != EXPECTED_OLD_UPDATER:
                raise ValueError("frozen restorefix2 rootfs updater hash changed")
    manifest = json.loads(package_manifest)
    if manifest.get("kernel_release") != KREL or manifest.get("version") != "u30air-native14-restorefix2-2026.10.07-test":
        raise ValueError("pinned prior manifest is not the expected immutable restorefix2 candidate")
    return manifest, package_files["mu300-kernel-7.2.8-u30air-native14.tar.gz"], package_files["mu300-openwrt-rootfs.tar.gz"]


def safe_extract_package(archive_path: Path, output: Path) -> None:
    seen: set[str] = set()
    with tarfile.open(archive_path, "r:gz") as outer:
        for member in outer.getmembers():
            if member.name == "package":
                output.mkdir(parents=True, exist_ok=True)
                continue
            if not member.name.startswith("package/"):
                continue
            relative = PurePosixPath(member.name).relative_to("package")
            if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                raise ValueError(f"unsafe package path: {member.name}")
            rel = relative.as_posix()
            if rel in seen:
                raise ValueError(f"duplicate package path: {rel}")
            seen.add(rel)
            target = output.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(member.mode & 0o777)
            elif member.isreg():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = outer.extractfile(member)
                if source is None:
                    raise ValueError(f"package file has no payload: {member.name}")
                with target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                target.chmod(member.mode & 0o777)
            else:
                raise ValueError(f"unsupported package entry type: {member.name}")


def rewrite_rootfs_payload(source: Path, destination: Path, old_version: str, updater: bytes) -> None:
    version_name = "./etc/mu300/image-version"
    version_matches = 0
    updater_matches = 0
    names: set[str] = set()
    new_bytes = (VERSION + "\n").encode("ascii")
    with tarfile.open(source, "r:gz") as original, tarfile.open(destination, "w:gz", format=tarfile.PAX_FORMAT) as rebuilt:
        for member in original.getmembers():
            if member.name in names:
                raise ValueError(f"duplicate rootfs tar member: {member.name}")
            names.add(member.name)
            updated = copy.copy(member)
            if member.name == version_name:
                version_matches += 1
                payload = original.extractfile(member).read()
                if payload.decode("utf-8").strip() != old_version:
                    raise ValueError("old rootfs image-version does not match its manifest")
                updated.size = len(new_bytes)
                rebuilt.addfile(updated, io.BytesIO(new_bytes))
            elif member.name == UPDATER_PATH:
                updater_matches += 1
                if not member.isreg() or not member.mode & 0o111:
                    raise ValueError("old rootfs updater is not an executable regular member")
                updated.size = len(updater)
                rebuilt.addfile(updated, io.BytesIO(updater))
            elif member.isreg():
                payload = original.extractfile(member)
                if payload is None:
                    raise ValueError(f"rootfs member has no data: {member.name}")
                rebuilt.addfile(updated, payload)
            else:
                rebuilt.addfile(updated)
    if version_matches != 1:
        raise ValueError(f"expected exactly one {version_name}, found {version_matches}")
    if updater_matches != 1:
        raise ValueError(f"expected exactly one {UPDATER_PATH}, found {updater_matches}")


def add_tree_member(archive: tarfile.TarFile, name: str, source: Path | bytes) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.mode = (source.stat().st_mode & 0o777) if isinstance(source, Path) else 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = 0
    info.size = source.stat().st_size if isinstance(source, Path) else len(source)
    if isinstance(source, Path):
        with source.open("rb") as stream:
            archive.addfile(info, stream)
    else:
        archive.addfile(info, io.BytesIO(source))


def rewrite_source_archive(old_source: Path, new_source: Path) -> None:
    report_dir = PROJECT / "device-validation"
    endpoint = report_dir / "native14-independent-charge-endpoint-20261007T1954HKT"
    discharge = report_dir / "native14-independent-discharge-terminal-20261007T1626HKT"
    required_inputs = {
        "install-device.sh": HERE / "install-device.sh",
        "rollback-device.sh": HERE / "rollback-device.sh",
        "test-deployment-compat.py": HERE / "test-deployment-compat.py",
        "test-mu300-update-idempotent.py": HERE / "test-mu300-update-idempotent.py",
        "mu300-update": RUNTIME_UPDATER,
        "observe-charge-to-full.sh": PROJECT / "observe-native13-charge-v2.sh",
        "observe-native14-discharge.sh": PROJECT / "observe-native14-discharge.sh",
        "observe-native14-await-discharge.sh": PROJECT / "observe-native14-await-discharge.sh",
        "observe-native14-fcc-status.sh": PROJECT / "observe-native14-fcc-status.sh",
        "README-restorefix3.md": HERE / "README-restorefix3.md",
        "RESTOREFIX3-REVIEW.md": HERE / "RESTOREFIX3-REVIEW.md",
        "assemble-restorefix3.py": HERE / "assemble-restorefix3.py",
        "verify-restorefix3-delivery.py": HERE / "verify-restorefix3-delivery.py",
        "firmware-deployment-audit-20261007.md": report_dir / "firmware-deployment-audit-20261007.md",
        "firmware-deployment-test-20261007.log": report_dir / "firmware-deployment-test-20261007.log",
        "updater-idempotency-audit-20261008.md": HERE / "updater-idempotency-audit-20261008.md",
        "test-deployment-compat-20261008.stdout.log": HERE / "test-deployment-compat-20261008.stdout.log",
        "test-deployment-compat-restorefix3-20261008.stdout.log": HERE / "test-deployment-compat-restorefix3-20261008.stdout.log",
        "test-mu300-update-idempotent-restorefix3-20261008.stdout.log": HERE / "test-mu300-update-idempotent-restorefix3-20261008.stdout.log",
        "device-module-tree-sha256.txt": endpoint / "device-module-tree-sha256.txt",
        "native14-full-endpoint-20261007-5083e56d.analysis.json": endpoint / "native14-full-endpoint-20261007-5083e56d.analysis.json",
        "native14-full-endpoint-20261007-5083e56d.csv": endpoint / "native14-full-endpoint-20261007-5083e56d.csv",
        "native14-full-endpoint-20261007-5083e56d.err": endpoint / "native14-full-endpoint-20261007-5083e56d.err",
        "native14-independent-charge-endpoint-REPORT.md": endpoint / "REPORT.md",
        "native14-independent-discharge-5083e56d.analysis.json": discharge / "native14-independent-discharge-5083e56d.analysis.json",
        "native14-independent-discharge-5083e56d.csv": discharge / "native14-independent-discharge-5083e56d.csv",
    }
    for label, path in required_inputs.items():
        if not path.is_file():
            raise FileNotFoundError(f"required source/evidence file missing ({label}): {path}")
    replacements: dict[str, Path | bytes] = {
        "native14/install-device.sh": HERE / "install-device.sh",
        "native14/rollback-device.sh": HERE / "rollback-device.sh",
        "native14/test-deployment-compat.py": HERE / "test-deployment-compat.py",
        "native14/test-mu300-update-idempotent.py": HERE / "test-mu300-update-idempotent.py",
        "native14/README.md": HERE / "README-restorefix3.md",
        "native14/README-restorefix3.md": HERE / "README-restorefix3.md",
        "native14/RESTOREFIX3-REVIEW.md": HERE / "RESTOREFIX3-REVIEW.md",
        "native14/assemble-restorefix3.py": HERE / "assemble-restorefix3.py",
        "native14/verify-restorefix3-delivery.py": HERE / "verify-restorefix3-delivery.py",
        "native14/observe-charge-to-full.sh": PROJECT / "observe-native13-charge-v2.sh",
        "native14/observe-native14-discharge.sh": PROJECT / "observe-native14-discharge.sh",
        "native14/observe-native14-await-discharge.sh": PROJECT / "observe-native14-await-discharge.sh",
        "native14/observe-native14-fcc-status.sh": PROJECT / "observe-native14-fcc-status.sh",
        "native14/overlay/opt/mu300/bin/mu300-update": RUNTIME_UPDATER,
        "project/rootfs/overlay/opt/mu300/bin/mu300-update": RUNTIME_UPDATER,
        "native14/device-validation/firmware-deployment-audit-20261007.md": report_dir / "firmware-deployment-audit-20261007.md",
        "native14/device-validation/firmware-deployment-test-20261007.log": report_dir / "firmware-deployment-test-20261007.log",
        "native14/device-validation/updater-idempotency-audit-20261008.md": HERE / "updater-idempotency-audit-20261008.md",
        "native14/device-validation/test-deployment-compat-20261008.stdout.log": HERE / "test-deployment-compat-20261008.stdout.log",
        "native14/device-validation/test-deployment-compat-restorefix3-20261008.stdout.log": HERE / "test-deployment-compat-restorefix3-20261008.stdout.log",
        "native14/device-validation/test-mu300-update-idempotent-restorefix3-20261008.stdout.log": HERE / "test-mu300-update-idempotent-restorefix3-20261008.stdout.log",
        "native14/device-validation/native14-independent-charge-endpoint-20261007T1954HKT/device-module-tree-sha256.txt": endpoint / "device-module-tree-sha256.txt",
        "native14/device-validation/native14-independent-charge-endpoint-20261007T1954HKT/native14-full-endpoint-20261007-5083e56d.analysis.json": endpoint / "native14-full-endpoint-20261007-5083e56d.analysis.json",
        "native14/device-validation/native14-independent-charge-endpoint-20261007T1954HKT/native14-full-endpoint-20261007-5083e56d.csv": endpoint / "native14-full-endpoint-20261007-5083e56d.csv",
        "native14/device-validation/native14-independent-charge-endpoint-20261007T1954HKT/native14-full-endpoint-20261007-5083e56d.err": endpoint / "native14-full-endpoint-20261007-5083e56d.err",
        "native14/device-validation/native14-independent-charge-endpoint-20261007T1954HKT/REPORT.md": endpoint / "REPORT.md",
        "native14/device-validation/native14-independent-discharge-terminal-20261007T1626HKT/native14-independent-discharge-5083e56d.analysis.json": discharge / "native14-independent-discharge-5083e56d.analysis.json",
        "native14/device-validation/native14-independent-discharge-terminal-20261007T1626HKT/native14-independent-discharge-5083e56d.csv": discharge / "native14-independent-discharge-5083e56d.csv",
    }
    overlay_version_member = "native14/overlay/etc/mu300/image-version"
    old_names: set[str] = set()
    with tarfile.open(old_source, "r:gz") as source_archive:
        source_members = source_archive.getmembers()
        for member in source_members:
            if member.name in old_names:
                raise ValueError(f"duplicate source archive member: {member.name}")
            old_names.add(member.name)
        if overlay_version_member in old_names:
            replacements[overlay_version_member] = (VERSION + "\n").encode("ascii")
        found = {name: 0 for name in replacements}
        with tarfile.open(new_source, "w:gz", format=tarfile.PAX_FORMAT) as output:
            for member in source_members:
                if member.name in replacements:
                    found[member.name] += 1
                    continue
                if member.name.startswith("/") or ".." in PurePosixPath(member.name).parts:
                    raise ValueError(f"unsafe old source member: {member.name}")
                stream = source_archive.extractfile(member) if member.isreg() else None
                output.addfile(copy.copy(member), stream)
            for name, file_path in replacements.items():
                if found[name] > 1:
                    raise ValueError(f"duplicate source replacement path: {name}")
                add_tree_member(output, name, file_path)
    # Ensure the two build/runtime source overlays both carry the patched updater.
    with tarfile.open(new_source, "r:gz") as source_archive:
        for name in (
            "native14/overlay/opt/mu300/bin/mu300-update",
            "project/rootfs/overlay/opt/mu300/bin/mu300-update",
        ):
            members = [m for m in source_archive.getmembers() if m.name == name]
            if len(members) != 1 or not members[0].isreg() or not members[0].mode & 0o111:
                raise ValueError(f"source updater is not exactly one executable regular file: {name}")
            payload = source_archive.extractfile(members[0]).read()
            if sha_bytes(payload) != EXPECTED_UPDATER:
                raise ValueError(f"source updater hash mismatch: {name}")
        # Ensure both native14 source locations carry the reviewed FCC runtime bytes.
        for name, expected in (
            ("native14/overlay/opt/mu300/bin/mu300-update", EXPECTED_UPDATER),
            ("project/rootfs/overlay/opt/mu300/bin/mu300-update", EXPECTED_UPDATER),
            ("native14/overlay/opt/mu300/lib/fcc-record.sh", EXPECTED_FCC_LIBRARY),
            ("native14/overlay/opt/mu300/bin/mu300-fcc-record", EXPECTED_FCC_SERVICE),
        ):
            matched = [m for m in source_archive.getmembers() if m.name == name]
            if len(matched) != 1 or not matched[0].isreg():
                raise ValueError(f"source runtime member is missing, duplicated, or nonregular: {name}")
            payload = source_archive.extractfile(matched[0]).read()
            if sha_bytes(payload) != expected:
                raise ValueError(f"source archive runtime hash mismatch: {name}")


def make_package_manifest(previous: dict, kernel_hash: str) -> dict:
    manifest = dict(previous)
    manifest.update({
        "version": VERSION,
        "kernel_release": KREL,
        "release_status": "offline candidate; restorefix3 has not been flashed",
        "kernel_bundle_sha256": kernel_hash,
        "hardware_fcc_learning_tested": True,
        "hardware_fcc_learning_tested_scope": "prior native14 runtime observation only; not this restorefix3 package",
        "hardware_boot_tested": False,
        "hardware_charging_tested": False,
        "independent_capacity_accuracy_verified": False,
        "runtime_restore_fix": "The FCC restore library/service bytes are carried in this candidate; runtime behavior was previously observed on a different native14 image. This full restorefix3 package has not been flashed.",
        "runtime_restore_sha256": {"library": EXPECTED_FCC_LIBRARY, "service": EXPECTED_FCC_SERVICE},
        "prior_runtime_observation": {
            "image_version": "u30air-native14-android-charge-2026.10.05-test",
            "qualified_full_endpoint": "13 samples over 120.54 seconds at 100%/Full, Good health, external input online; see device-validation/native14-independent-charge-endpoint-20261007T1954HKT/REPORT.md",
            "learned_model_mah": 3983,
            "scope": "existing native14 runtime, not the restorefix3 image",
        },
        "charge_endpoint_observer_sha256": EXPECTED_OBSERVER,
        "deployment_compatibility_tests": {
            "cases_passed": 40,
            "script_sha256": EXPECTED_TEST,
            "restorefix3_stdout_sha256": EXPECTED_TEST_LOG,
            "environment": "WSL Ubuntu-24.04; 39 simulated temporary-file installer/updater scenarios plus one real packaged-updater installer rollback scenario; Python JSON shim because OpenWrt jsonfilter is unavailable",
            "hardware_validation": False,
        },
        "updater_idempotency_tests": {
            "cases_passed": 3,
            "script_sha256": EXPECTED_UPDATER_TEST,
            "restorefix3_stdout_sha256": EXPECTED_UPDATER_TEST_LOG,
            "environment": "WSL Ubuntu-24.04; actual patched updater and checksummed kernel bundle against a temporary 64 MiB regular file, covering changed, already-current, and real missing-partition failure paths",
            "hardware_validation": False,
        },
        "future_discharge_observer_sha256": {
            "discharge": EXPECTED_DISCHARGE,
            "await_supply_change": EXPECTED_AWAIT_DISCHARGE,
            "fcc_status": EXPECTED_FCC_STATUS,
        },
        "module_tree_policy": "same KREL upgrade requires exact common paths/hashes; only modules.builtin and modules.builtin.modinfo may be added as an exact paired copy from the checksummed kernel bundle; rollback restores the complete original snapshot",
        "legacy_warning": "install-kernel-device.sh is a native11-to-native12 legacy tool; do not use with native14",
    })
    return manifest


def write_sums(directory: Path, filename: str = "SHA256SUMS") -> None:
    files = sorted(p for p in directory.rglob("*") if p.is_file() and p.name != filename)
    lines = [f"{sha_file(path)}  {path.relative_to(directory).as_posix()}\n" for path in files]
    (directory / filename).write_text("".join(lines), encoding="utf-8", newline="\n")


def write_release_archive(pkg_dir: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        archive.add(pkg_dir, arcname="package", recursive=True)


def mirror_release_to_zip(release: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path in sorted(release.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"refusing to package a release symlink: {path}")
            if path.is_file():
                relative = path.relative_to(release).as_posix()
                archive.write(path, f"{ZIP_PREFIX}/{relative}")


def assemble() -> dict:
    output = HERE / RELEASE_NAME
    zip_path = HERE / ZIP_NAME
    sidecar = Path(str(zip_path) + ".sha256")
    for path in (output, zip_path, sidecar):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite an existing artifact: {path}")
    previous, old_kernel, old_rootfs = validate_old_inputs()
    if not RUNTIME_UPDATER.is_file() or sha_file(RUNTIME_UPDATER) != EXPECTED_UPDATER:
        raise ValueError("patched mu300-update source hash does not match the reviewed fix")
    RUNTIME_UPDATER.chmod(0o755)
    source_installer = HERE / "install-device.sh"
    source_rollback = HERE / "rollback-device.sh"
    source_tests = HERE / "test-deployment-compat.py"
    source_updater_tests = HERE / "test-mu300-update-idempotent.py"
    observer = PROJECT / "observe-native13-charge-v2.sh"
    expected_hashes = (
        (source_installer, EXPECTED_INSTALLER, "installer"),
        (source_rollback, EXPECTED_ROLLBACK, "rollback"),
        (source_tests, EXPECTED_TEST, "test suite"),
        (source_updater_tests, EXPECTED_UPDATER_TEST, "updater fixture"),
        (HERE / "test-deployment-compat-restorefix3-20261008.stdout.log", EXPECTED_TEST_LOG, "restorefix3 installer test log"),
        (HERE / "test-mu300-update-idempotent-restorefix3-20261008.stdout.log", EXPECTED_UPDATER_TEST_LOG, "restorefix3 updater test log"),
        (observer, EXPECTED_OBSERVER, "strict charge observer"),
        (PROJECT / "observe-native14-discharge.sh", EXPECTED_DISCHARGE, "native14 discharge observer"),
        (PROJECT / "observe-native14-await-discharge.sh", EXPECTED_AWAIT_DISCHARGE, "native14 supply wait observer"),
        (PROJECT / "observe-native14-fcc-status.sh", EXPECTED_FCC_STATUS, "native14 FCC status observer"),
    )
    for path, expected, label in expected_hashes:
        if sha_file(path) != expected:
            raise ValueError(f"reviewed {label} changed: {path}")
    with tarfile.open(fileobj=__import__("io").BytesIO(old_kernel), mode="r:gz") as kernel_tar:
        kernel_members = kernel_tar.getnames()
        if kernel_tar.extractfile("./kernel.release").read().strip().decode() != KREL:
            raise ValueError("prior kernel bundle release does not match native14")
        if kernel_tar.extractfile("./devices").read().strip() != b"u30air":
            raise ValueError("prior kernel bundle is not targeted at U30 Air")
    kernel_hash = sha_bytes(old_kernel)

    work_parent = Path(tempfile.mkdtemp(prefix=".restorefix3-build-", dir=HERE))
    staged_release = work_parent / RELEASE_NAME
    package = work_parent / "package"
    try:
        staged_release.mkdir()
        package.mkdir()
        safe_extract_package(OLD / OLD_ARCHIVE_NAME, package)
        # The package must still be the checksummed artifact read above.
        package_sums = read_sum_file(package / "SHA256SUMS")
        for name, digest in package_sums.items():
            if sha_file(package / Path(*PurePosixPath(name).parts)) != digest:
                raise ValueError(f"staged old package changed during extraction: {name}")
        if sha_file(package / "mu300-kernel-7.2.8-u30air-native14.tar.gz") != kernel_hash:
            raise ValueError("staged native14 kernel bundle bytes changed")
        old_rootfs_path = package / "mu300-openwrt-rootfs.tar.gz"
        rewritten_rootfs = work_parent / "mu300-openwrt-rootfs.tar.gz"
        rewrite_rootfs_payload(old_rootfs_path, rewritten_rootfs, previous["version"], RUNTIME_UPDATER.read_bytes())
        shutil.copy2(rewritten_rootfs, old_rootfs_path)
        shutil.copy2(RUNTIME_UPDATER, package / "mu300-update")
        (package / "mu300-update").chmod(0o755)
        if sha_file(package / "mu300-update") != EXPECTED_UPDATER:
            raise ValueError("package mu300-update does not match patched source")

        # Add or replace release provenance and strictly-reviewed deployment helpers.
        package_manifest = make_package_manifest(previous, kernel_hash)
        (package / "BUILD-MANIFEST.json").write_text(
            json.dumps(package_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        shutil.copy2(source_installer, package / "install-device.sh")
        shutil.copy2(source_rollback, package / "rollback-device.sh")
        shutil.copy2(source_tests, package / "test-deployment-compat.py")
        shutil.copy2(source_updater_tests, package / "test-mu300-update-idempotent.py")
        shutil.copy2(observer, package / "observe-charge-to-full.sh")
        shutil.copy2(HERE / "README-restorefix3.md", package / "README.md")
        shutil.copy2(HERE / "RESTOREFIX3-REVIEW.md", package / "RESTOREFIX3-REVIEW.md")
        write_sums(package)
        release_archive = staged_release / OLD_ARCHIVE_NAME
        write_release_archive(package, release_archive)

        # Reuse all unchanged public recovery/source assets, but never copy old candidate outputs.
        replaced = {
            "BUILD-MANIFEST.json", "SHA256SUMS", "delivery.json", OLD_ARCHIVE_NAME,
            OLD_SOURCE_NAME, "README.md", "rollback-device.sh",
        }
        for item in OLD.iterdir():
            if item.is_file() and item.name not in replaced:
                shutil.copy2(item, staged_release / item.name)
        (staged_release / "BUILD-MANIFEST.json").write_bytes((package / "BUILD-MANIFEST.json").read_bytes())
        shutil.copy2(HERE / "README-restorefix3.md", staged_release / "README.md")
        shutil.copy2(HERE / "RESTOREFIX3-REVIEW.md", staged_release / "RESTOREFIX3-REVIEW.md")
        for source, name in (
            (source_installer, "install-device.sh"),
            (source_rollback, "rollback-device.sh"),
            (source_tests, "test-deployment-compat.py"),
            (source_updater_tests, "test-mu300-update-idempotent.py"),
            (RUNTIME_UPDATER, "mu300-update"),
            (observer, "observe-charge-to-full.sh"),
            (PROJECT / "observe-native14-discharge.sh", "observe-native14-discharge.sh"),
            (PROJECT / "observe-native14-await-discharge.sh", "observe-native14-await-discharge.sh"),
            (PROJECT / "observe-native14-fcc-status.sh", "observe-native14-fcc-status.sh"),
            (HERE / "assemble-restorefix3.py", "assemble-restorefix3.py"),
            (HERE / "verify-restorefix3-delivery.py", "verify-restorefix3-delivery.py"),
            (HERE / "README-restorefix3.md", "README-restorefix3.md"),
            (HERE / "RESTOREFIX3-REVIEW.md", "RESTOREFIX3-REVIEW.md"),
            (HERE / "updater-idempotency-audit-20261008.md", "updater-idempotency-audit-20261008.md"),
            (HERE / "test-deployment-compat-20261008.stdout.log", "test-deployment-compat-20261008.stdout.log"),
            (HERE / "test-deployment-compat-restorefix3-20261008.stdout.log", "test-deployment-compat-restorefix3-20261008.stdout.log"),
            (HERE / "test-mu300-update-idempotent-restorefix3-20261008.stdout.log", "test-mu300-update-idempotent-restorefix3-20261008.stdout.log"),
            (PROJECT / "device-validation" / "firmware-deployment-audit-20261007.md", "firmware-deployment-audit-20261007.md"),
            (PROJECT / "device-validation" / "firmware-deployment-test-20261007.log", "firmware-deployment-test-20261007.log"),
        ):
            shutil.copy2(source, staged_release / name)
        rewrite_source_archive(OLD / OLD_SOURCE_NAME, staged_release / OLD_SOURCE_NAME)

        # Archive only evidence needed to substantiate prior runtime observations and module compatibility.
        evidence = PROJECT / "device-validation"
        endpoint = evidence / "native14-independent-charge-endpoint-20261007T1954HKT"
        discharge = evidence / "native14-independent-discharge-terminal-20261007T1626HKT"
        for src, rel in (
            (endpoint / "device-module-tree-sha256.txt", "device-validation/native14-independent-charge-endpoint-20261007T1954HKT/device-module-tree-sha256.txt"),
            (endpoint / "REPORT.md", "device-validation/native14-independent-charge-endpoint-20261007T1954HKT/REPORT.md"),
            (endpoint / "native14-full-endpoint-20261007-5083e56d.analysis.json", "device-validation/native14-independent-charge-endpoint-20261007T1954HKT/native14-full-endpoint-20261007-5083e56d.analysis.json"),
            (endpoint / "native14-full-endpoint-20261007-5083e56d.csv", "device-validation/native14-independent-charge-endpoint-20261007T1954HKT/native14-full-endpoint-20261007-5083e56d.csv"),
            (endpoint / "native14-full-endpoint-20261007-5083e56d.err", "device-validation/native14-independent-charge-endpoint-20261007T1954HKT/native14-full-endpoint-20261007-5083e56d.err"),
            (discharge / "native14-independent-discharge-5083e56d.analysis.json", "device-validation/native14-independent-discharge-terminal-20261007T1626HKT/native14-independent-discharge-5083e56d.analysis.json"),
            (discharge / "native14-independent-discharge-5083e56d.csv", "device-validation/native14-independent-discharge-terminal-20261007T1626HKT/native14-independent-discharge-5083e56d.csv"),
        ):
            target = staged_release / Path(*PurePosixPath(rel).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)

        (staged_release / "delivery.json").write_text(
            json.dumps({"archive": OLD_ARCHIVE_NAME, "sha256": sha_file(release_archive)}, indent=2) + "\n",
            encoding="utf-8", newline="\n"
        )
        write_sums(staged_release)
        staged_zip = work_parent / ZIP_NAME
        mirror_release_to_zip(staged_release, staged_zip)
        staged_sidecar = Path(str(staged_zip) + ".sha256")
        staged_sidecar.write_text(f"{sha_file(staged_zip)}  {ZIP_NAME}\n", encoding="ascii", newline="\n")

        # Publish new names only after all staged output exists; never replace any previous artifact.
        if output.exists() or zip_path.exists() or sidecar.exists():
            raise FileExistsError("an output appeared during assembly; refusing to overwrite")
        published: list[Path] = []
        try:
            os.replace(staged_release, output)
            published.append(output)
            os.replace(staged_zip, zip_path)
            published.append(zip_path)
            os.replace(staged_sidecar, sidecar)
            published.append(sidecar)
        except Exception:
            # Move only this invocation's partial outputs back into its private staging tree.
            for path in reversed(published):
                if path.exists():
                    os.replace(path, work_parent / path.name)
            raise
        return {
            "release": str(output),
            "zip": str(zip_path),
            "zip_bytes": zip_path.stat().st_size,
            "zip_sha256": sha_file(zip_path),
            "kernel_bundle_sha256": kernel_hash,
            "version": VERSION,
        }
    except Exception:
        # Keep failed staging output for review; never erase evidence from a failed candidate build.
        quarantine_root = HERE / "validation-failed"
        quarantine_root.mkdir(exist_ok=True)
        quarantine = quarantine_root / f"{work_parent.name}-failed"
        if work_parent.exists() and not quarantine.exists():
            os.replace(work_parent, quarantine)
        raise
    else:
        shutil.rmtree(work_parent, ignore_errors=True)


if __name__ == "__main__":
    print(json.dumps(assemble(), indent=2))
