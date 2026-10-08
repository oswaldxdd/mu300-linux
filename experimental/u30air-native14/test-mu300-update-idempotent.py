#!/usr/bin/env python3
"""Exercise the actual mu300-update boot writer against a regular-file boot partition."""
from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
KREL = "7.2.8-u30air-native14"
KERNEL_NAME = f"mu300-kernel-{KREL}.tar.gz"
OLD_TAG = "local-u30air-native14-fcc"
CANDIDATE_TAG = "local-u30air-native14-restorefix3-2026.10.08-test"
BOOT_BYTES = 64 * 1024 * 1024
SECTOR_BYTES = 512
PAGE_BYTES = 4096


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_package_members(package_tar: Path) -> tuple[bytes, bytes]:
    with tarfile.open(package_tar, "r:gz") as package:
        checksums = package.extractfile("package/SHA256SUMS")
        if checksums is None:
            raise ValueError("firmware archive has no package/SHA256SUMS")
        entries: dict[str, str] = {}
        for line in checksums.read().decode("ascii").splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise ValueError(f"malformed package checksum row: {line!r}")
            name = parts[1].lstrip("*").removeprefix("./")
            entries[name] = parts[0]
        payloads = []
        for name in ("mu300-update", KERNEL_NAME):
            member = package.extractfile(f"package/{name}")
            if member is None:
                raise ValueError(f"firmware archive is missing package/{name}")
            payload = member.read()
            if entries.get(name) != digest(payload):
                raise ValueError(f"package checksum mismatch: {name}")
            payloads.append(payload)
    return payloads[0], payloads[1]


def local_payloads() -> tuple[bytes, bytes]:
    if (HERE / "mu300-update").is_file() and (HERE / KERNEL_NAME).is_file():
        base = HERE
    else:
        base = HERE.parent / "runtime-update"
    return (base.joinpath("mu300-update").read_bytes(),
            base.joinpath(KERNEL_NAME).read_bytes())


def extract_kernel(bundle: bytes) -> tuple[bytes, bytes, dict[str, bytes]]:
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
        krel = archive.extractfile("./kernel.release")
        devices = archive.extractfile("./devices")
        image = archive.extractfile("./Image")
        generic = archive.extractfile("./ramdisk-generic.lz4")
        if any(item is None for item in (krel, devices, image, generic)):
            raise ValueError("kernel bundle lacks required boot members")
        if krel.read().decode().strip() != KREL or "u30air" not in devices.read().decode().split():
            raise ValueError("kernel bundle release/device does not match the fixture")
        image_bytes = image.read()
        generic_bytes = generic.read()
        modules: dict[str, bytes] = {}
        for member in archive.getmembers():
            if member.isfile() and member.name.startswith("./modules/") and member.name.endswith(".ko"):
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"kernel module has no payload: {member.name}")
                modules[Path(member.name).name] = stream.read()
    if not modules:
        raise ValueError("kernel bundle contains no modules")
    return image_bytes, generic_bytes, modules


def sandbox_updater(source: bytes, root: Path) -> Path:
    text = source.decode("utf-8")
    replacements = (
        ("for u in /sys/class/block/*/uevent; do",
         'for u in "$TEST_ROOT"/sys/class/block/*/uevent; do'),
        ('echo "/dev/${d##*/}"', 'echo "$TEST_ROOT/dev/${d##*/}"'),
        ('cat "/sys/class/block/${dev##*/}/size"',
         'cat "$TEST_ROOT/sys/class/block/${dev##*/}/size"'),
        ("echo 3 > /proc/sys/vm/drop_caches 2>/dev/null",
         'echo 3 > "$TEST_ROOT/proc/sys/vm/drop_caches" 2>/dev/null'),
    )
    for old, new in replacements:
        if old not in text:
            raise ValueError(f"updater fixture could not bind path: {old}")
        text = text.replace(old, new)
    target = root / "mu300-update"
    target.write_text(text, encoding="utf-8", newline="\n")
    target.chmod(0o755)
    return target


def pad_page(data: bytes) -> bytes:
    return data + bytes((-len(data)) % PAGE_BYTES)


def build_stock_boot(path: Path, image: bytes) -> None:
    header = bytearray(PAGE_BYTES)
    header[:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, len(image))
    struct.pack_into("<I", header, 12, PAGE_BYTES)
    struct.pack_into("<I", header, 40, 4)
    device_ramdisk = b"\x02\x21\x4c\x18" + b"device-ramdisk-test"
    device_ramdisk = pad_page(device_ramdisk)
    body = bytes(header) + pad_page(image) + device_ramdisk
    vbmeta = b"V" * PAGE_BYTES
    footer = bytearray(64)
    footer[:4] = b"AVBf"
    struct.pack_into(">Q", footer, 12, len(body))
    struct.pack_into(">Q", footer, 28, len(vbmeta))
    if len(body) + len(vbmeta) + len(footer) >= BOOT_BYTES:
        raise ValueError("regular-file boot fixture exceeds the 64 MiB boot_b partition")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        output.write(body)
        output.write(vbmeta)
        remaining = BOOT_BYTES - len(body) - len(vbmeta) - len(footer)
        zeros = bytes(1024 * 1024)
        while remaining:
            block = min(remaining, len(zeros))
            output.write(zeros[:block])
            remaining -= block
        output.write(footer)


def prepare_fixture(root: Path, updater: bytes, bundle: bytes, image: bytes,
                    *, boot_partition: bool = True) -> tuple[Path, Path, Path, Path]:
    test_root = root / "fake-device"
    disk = test_root / "mnt/mu300-disk"
    (disk / "openwrt/etc/mu300").mkdir(parents=True)
    (disk / "openwrt/etc/mu300/image-version").write_text(CANDIDATE_TAG.removeprefix("local-") + "\n")
    (disk / "boot").mkdir()
    (disk / "boot/kernel").write_text("7.2\n")
    bin_dir = test_root / "opt/mu300/bin"
    bin_dir.mkdir(parents=True)
    device = bin_dir / "mu300-device"
    device.write_text("#!/bin/sh\nprintf '%s\\n' u30air\n")
    device.chmod(0o755)
    block_dir = test_root / "sys/class/block/boot_b"
    block_dir.mkdir(parents=True)
    (block_dir / "uevent").write_text("PARTNAME=boot_b\n" if boot_partition else "PARTNAME=not_boot_b\n")
    (block_dir / "size").write_text(f"{BOOT_BYTES // SECTOR_BYTES}\n")
    (test_root / "proc/sys/vm").mkdir(parents=True)
    (test_root / "proc/sys/vm/drop_caches").write_text("0\n")
    boot_dev = test_root / "dev/boot_b"
    build_stock_boot(boot_dev, image)
    bundle_path = root / KERNEL_NAME
    bundle_path.write_bytes(bundle)
    updater_path = sandbox_updater(updater, root)
    stub_dir = root / "command-stubs"
    stub_dir.mkdir()
    id_stub = stub_dir / "id"
    id_stub.write_text("#!/bin/sh\n[ \"${1:-}\" = -u ] && { echo 0; exit 0; }\nexit 1\n")
    id_stub.chmod(0o755)
    ping = shutil.which("ping")
    if ping:
        ping_stub = stub_dir / "ping"
        ping_stub.write_text("#!/bin/sh\nexit 1\n")
        ping_stub.chmod(0o755)
    return test_root, disk, boot_dev, updater_path


def run_boot(test_root: Path, disk: Path, updater: Path, bundle: Path, tag: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({
        "TEST_ROOT": str(test_root),
        "PATH": str(updater.parent / "command-stubs") + ":" + env.get("PATH", ""),
        "MU300_DISK": str(disk),
        "MU300_BIN": str(test_root / "opt/mu300/bin"),
        "MU300_KERNEL_BUNDLE": str(bundle),
        "MU300_RELEASE": tag,
    })
    return subprocess.run(["sh", str(updater), "boot"], env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)


def boot_body_digest(device: Path) -> tuple[str, int]:
    with device.open("rb") as source:
        source.seek(-64, os.SEEK_END)
        footer = source.read(64)
        size = struct.unpack_from(">Q", footer, 12)[0]
        source.seek(0)
        body = source.read(size)
    return digest(body), size


def test_up_to_date_and_changed_branch(updater_source: bytes, bundle: bytes) -> None:
    image, generic, modules = extract_kernel(bundle)
    with tempfile.TemporaryDirectory(prefix="mu300-updater-idempotent-") as temporary:
        root = Path(temporary)
        test_root, disk, boot_dev, updater = prepare_fixture(root, updater_source, bundle, image)
        bundle_path = root / KERNEL_NAME

        first = run_boot(test_root, disk, updater, bundle_path, OLD_TAG)
        if first.returncode != 0:
            raise AssertionError(f"actual boot update branch failed ({first.returncode}):\n{first.stdout}")
        if "boot: updated to " + OLD_TAG not in first.stdout or "Reboot to start the new kernel." not in first.stdout:
            raise AssertionError(f"BOOT_CHANGED update branch was not exercised:\n{first.stdout}")
        bootdir = disk / "boot"
        expected_id = digest(image + generic)
        if (bootdir / "installed.id").read_text().strip() != expected_id:
            raise AssertionError("actual updater did not record the kernel+generic-ramdisk ID")
        body_sha, body_size = boot_body_digest(boot_dev)
        if (bootdir / "written.sha256").read_text().strip() != body_sha:
            raise AssertionError("fake boot bytes do not match the updater's written.sha256 metadata")
        if (bootdir / "installed.tag").read_text().strip() != OLD_TAG:
            raise AssertionError("first updater run did not preserve the actual old boot tag")
        module_dir = disk / "openwrt/lib/modules" / KREL
        actual_modules = {path.name: path.read_bytes() for path in module_dir.glob("*.ko")}
        if actual_modules != modules:
            raise AssertionError("actual updater did not install exactly the kernel bundle module bytes")
        initial_boot_sha = digest(boot_dev.read_bytes())
        print(f"PASS actual package updater changed boot: BOOT_CHANGED path returned 0; body={body_size} bytes, modules={len(modules)}")

        second = run_boot(test_root, disk, updater, bundle_path, CANDIDATE_TAG)
        if second.returncode != 0:
            raise AssertionError(f"confirmed unchanged boot returned {second.returncode} (expected 0):\n{second.stdout}")
        if f"boot: already up to date ({OLD_TAG})" not in second.stdout:
            raise AssertionError(f"real already-up-to-date comparison was not exercised:\n{second.stdout}")
        if "Reboot to start the new kernel." in second.stdout:
            raise AssertionError("unchanged boot incorrectly requested a reboot")
        if digest(boot_dev.read_bytes()) != initial_boot_sha:
            raise AssertionError("already-up-to-date path modified the regular-file boot partition")
        if (bootdir / "installed.id").read_text().strip() != expected_id or \
                (bootdir / "written.sha256").read_text().strip() != body_sha:
            raise AssertionError("already-up-to-date path changed verified boot metadata")
        if (bootdir / "installed.tag").read_text().strip() != OLD_TAG:
            raise AssertionError("same-content no-op rewrote the historical boot tag")
        print("PASS actual package updater same Image+ramdisk ID and written boot SHA: no-op returned 0 without touching boot/metadata")


def test_real_failure_is_nonzero(updater_source: bytes, bundle: bytes) -> None:
    image, _, _ = extract_kernel(bundle)
    with tempfile.TemporaryDirectory(prefix="mu300-updater-failure-") as temporary:
        root = Path(temporary)
        test_root, disk, boot_dev, updater = prepare_fixture(
            root, updater_source, bundle, image, boot_partition=False)
        before = digest(boot_dev.read_bytes())
        result = run_boot(test_root, disk, updater, root / KERNEL_NAME, CANDIDATE_TAG)
        if result.returncode == 0:
            raise AssertionError(f"missing boot_b was incorrectly treated as success:\n{result.stdout}")
        if "boot: no boot_b partition found" not in result.stdout:
            raise AssertionError(f"expected real boot partition failure was not observed:\n{result.stdout}")
        if digest(boot_dev.read_bytes()) != before:
            raise AssertionError("failed boot lookup modified the regular-file partition")
        if (disk / "boot/installed.id").exists() or (disk / "boot/written.sha256").exists():
            raise AssertionError("failed boot lookup wrote success metadata")
        print("PASS actual package updater genuine missing-boot failure: exit remains nonzero; boot and metadata unchanged")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--firmware-package", type=Path,
                        help="read mu300-update and kernel bundle from a checksummed package archive")
    parser.add_argument("--updater", type=Path,
                        help="use this source updater with the package's verified kernel bundle")
    args = parser.parse_args()
    if args.firmware_package:
        updater_bytes, bundle = checked_package_members(args.firmware_package)
    else:
        updater_bytes, bundle = local_payloads()
    if args.updater:
        updater_bytes = args.updater.read_bytes()
    print(f"Updater SHA-256: {digest(updater_bytes)}")
    print(f"Kernel bundle SHA-256: {digest(bundle)}")
    test_up_to_date_and_changed_branch(updater_bytes, bundle)
    test_real_failure_is_nonzero(updater_bytes, bundle)
    print("PASS: actual updater tests used only temporary regular files; no block device or network was accessed")


if __name__ == "__main__":
    main()
