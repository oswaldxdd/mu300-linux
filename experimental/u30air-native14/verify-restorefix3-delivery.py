#!/usr/bin/env python3
"""Verify the restorefix3 candidate, nested payloads, source evidence, and privacy gates."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile

HERE = Path(__file__).resolve().parent
RELEASE_NAME = "restorefix3-release"
if HERE.name == RELEASE_NAME:
    RELEASE = HERE
    FIRMWARE = HERE.parent
else:
    FIRMWARE = HERE
    RELEASE = FIRMWARE / RELEASE_NAME
OLD_RELEASE = FIRMWARE / "restorefix2-release"
ZIP_NAME = "u30air-native14-restorefix3-2026.10.08-test.zip"
ZIP_PREFIX = "u30air-native14-restorefix3"
ARCHIVE_NAME = "u30air-native14-firmware.tar.gz"
SOURCE_NAME = "corresponding-project-source.tar.gz"
LINUX_SOURCE_NAME = "corresponding-linux-7.2.8-source.tar.gz"
VERSION = "u30air-native14-restorefix3-2026.10.08-test"
KREL = "7.2.8-u30air-native14"
UPDATER_PATH = "./opt/mu300/bin/mu300-update"
EXPECTED_OLD_ARCHIVE = "140be200ef1c6a0c9122d08e0191a6e3d6d8aa529e6cff25d52d7f1147b1b868"
EXPECTED_OLD_PROJECT_SOURCE = "daab8086ad771a807a827737b65707ab8973618a9abc80b7b2a55ece1de16bff"
EXPECTED_OLD_LINUX_SOURCE = "7065c5e06fbe81e497918366142f997bfe8b84c6222682ebbb6bc988fe35028e"
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
EXPECTED_DEVICE_MODULE_INVENTORY = "283cd2c0e00846971e4e7e24e8a29233c03a82e31885b48ab73a6bb1820c3264"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_stream(stream) -> str:
    h = hashlib.sha256()
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            return h.hexdigest()
        h.update(block)


def digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return digest_stream(stream)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def parse_sums(payload: bytes | str) -> dict[str, str]:
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        digest, name = line.split("  ", 1)
        pure = PurePosixPath(name)
        check(len(digest) == 64 and re.fullmatch(r"[0-9a-f]{64}", digest) is not None, f"bad checksum: {line}")
        check(not pure.is_absolute() and ".." not in pure.parts and name not in result, f"unsafe/duplicate path: {name}")
        result[name] = digest
    return result


def verify_release_sums() -> dict[str, str]:
    sums = parse_sums((RELEASE / "SHA256SUMS").read_bytes())
    actual = {p.relative_to(RELEASE).as_posix() for p in RELEASE.rglob("*") if p.is_file() and p.name != "SHA256SUMS"}
    check(actual == set(sums), f"release file set mismatch: missing={set(sums)-actual}; extra={actual-set(sums)}")
    for name, expected in sums.items():
        check(digest_file(RELEASE / Path(*PurePosixPath(name).parts)) == expected, f"release SHA256 mismatch: {name}")
    return sums


def package_members(archive_path: Path) -> tuple[dict[str, bytes], dict[str, str]]:
    result: dict[str, bytes] = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        names = [member.name for member in archive.getmembers()]
        check(len(names) == len(set(names)), "duplicate inner package tar entries")
        for member in archive.getmembers():
            if member.name == "package" or member.isdir():
                continue
            check(member.name.startswith("package/"), f"unexpected inner package path: {member.name}")
            relative = PurePosixPath(member.name).relative_to("package")
            check(not relative.is_absolute() and ".." not in relative.parts and member.isreg(), f"unsafe/nonregular package entry: {member.name}")
            result[relative.as_posix()] = archive.extractfile(member).read()
    sums = parse_sums(result["SHA256SUMS"])
    actual = set(result) - {"SHA256SUMS"}
    check(actual == set(sums), f"nested package file set mismatch: missing={set(sums)-actual}; extra={actual-set(sums)}")
    for name, expected in sums.items():
        check(digest_bytes(result[name]) == expected, f"nested package SHA256 mismatch: {name}")
    return result, sums


def compare_rootfs_members(old_payload: bytes, new_payload: bytes) -> tuple[dict[str, bytes], dict[str, bytes]]:
    def collect(payload: bytes) -> tuple[dict[str, bytes], dict[str, tuple]]:
        contents: dict[str, bytes] = {}
        metadata: dict[str, tuple] = {}
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            check(len(names) == len(set(names)), "duplicate rootfs tar entries")
            for member in members:
                check(not member.name.startswith("/") and ".." not in PurePosixPath(member.name).parts, f"unsafe rootfs path: {member.name}")
                metadata[member.name] = (
                    member.type, member.mode, member.uid, member.gid, member.uname, member.gname,
                    member.mtime, member.linkname, member.devmajor, member.devminor, dict(member.pax_headers),
                )
                if member.isreg():
                    contents[member.name] = archive.extractfile(member).read()
        return contents, metadata

    old_files, old_metadata = collect(old_payload)
    new_files, new_metadata = collect(new_payload)
    check(set(old_metadata) == set(new_metadata), "restorefix3 rootfs member paths differ from prior candidate")
    version_path = "./etc/mu300/image-version"
    updater_path = UPDATER_PATH
    allowed_changes = {version_path, updater_path}
    check(version_path in old_files and version_path in new_files, "rootfs image-version missing")
    check(updater_path in old_files and updater_path in new_files, "rootfs updater missing")
    check(old_files[version_path].strip() == b"u30air-native14-restorefix2-2026.10.07-test", "unexpected previous rootfs version")
    check(new_files[version_path].strip() == VERSION.encode("ascii"), "new rootfs image-version mismatch")
    for name in old_files:
        if name not in allowed_changes:
            check(old_files[name] == new_files[name], f"rootfs payload changed outside image-version and updater: {name}")
    check(digest_bytes(old_files[updater_path]) == EXPECTED_OLD_UPDATER, "previous rootfs updater is not the pinned restorefix2 updater")
    check(digest_bytes(new_files[updater_path]) == EXPECTED_UPDATER, "restorefix3 rootfs updater hash mismatch")
    for name in old_metadata:
        if name not in allowed_changes:
            check(old_metadata[name] == new_metadata[name], f"rootfs metadata changed outside image-version and updater: {name}")
    check(old_metadata[version_path] == new_metadata[version_path], "image-version member metadata changed")
    check(old_metadata[updater_path] == new_metadata[updater_path], "rootfs updater mode/metadata changed")
    return old_files, new_files


def parse_device_inventory(data: bytes) -> dict[str, str]:
    lines = data.decode("ascii").splitlines()
    check(lines and lines[0] == f"KREL={KREL}", "device module inventory KREL mismatch")
    check(len(lines) >= 2 and lines[1] == "REGULAR_FILES=31", "device module inventory file count mismatch")
    result: dict[str, str] = {}
    symlink_marker = lines.index("SYMLINKS:") if "SYMLINKS:" in lines else -1
    directory_marker = lines.index("DIRECTORIES:") if "DIRECTORIES:" in lines else -1
    check(symlink_marker >= 2 and directory_marker == symlink_marker + 1, "device module inventory sections are malformed or list a symlink")
    for line in lines[2:symlink_marker]:
        m = re.fullmatch(r"([0-9a-f]{64})  (\./[A-Za-z0-9_./+-]+)", line)
        check(m is not None, f"malformed device module inventory line: {line}")
        digest, name = m.groups()
        check(name not in result, f"duplicate module inventory path: {name}")
        result[name] = digest
    check(all(line.startswith(".") for line in lines[directory_marker + 1:]), "malformed module inventory directory section")
    check(len(result) == 31, f"device module inventory has {len(result)} entries, expected 31")
    return result


def verify_module_payload(package: dict[str, bytes], files: dict[str, bytes]) -> None:
    with tarfile.open(fileobj=io.BytesIO(package["mu300-kernel-7.2.8-u30air-native14.tar.gz"]), mode="r:gz") as kernel:
        names = [m.name for m in kernel.getmembers()]
        check(kernel.extractfile("./kernel.release").read().strip() == KREL.encode(), "kernel.release mismatch")
        check(kernel.extractfile("./devices").read().strip() == b"u30air", "kernel bundle device mismatch")
        check(names.count("./modules.builtin") == 1, "kernel bundle must contain exactly one modules.builtin")
        check(names.count("./modules.builtin.modinfo") == 1, "kernel bundle must contain exactly one modules.builtin.modinfo")
        builtin = kernel.extractfile("./modules.builtin").read()
        builtin_modinfo = kernel.extractfile("./modules.builtin.modinfo").read()
        check(digest_bytes(builtin) == EXPECTED_BUILTIN, "kernel modules.builtin hash mismatch")
        check(digest_bytes(builtin_modinfo) == EXPECTED_BUILTIN_MODINFO, "kernel modules.builtin.modinfo hash mismatch")
        kernel_modules: dict[str, bytes] = {}
        for member in kernel.getmembers():
            if member.name.endswith(".ko"):
                payload = kernel.extractfile(member).read()
                check(b"vermagic=" + KREL.encode() + b" " in payload, f"wrong module vermagic: {member.name}")
                kernel_modules[PurePosixPath(member.name).name] = payload
        check(len(kernel_modules) == 31, f"kernel bundle has {len(kernel_modules)} .ko files, expected 31")
    root_files = files
    prefix = f"./lib/modules/{KREL}/"
    root_modules = {name[len(prefix):]: payload for name, payload in root_files.items() if name.startswith(prefix)}
    check(set(root_modules) == set(kernel_modules) | {"modules.builtin", "modules.builtin.modinfo"}, "rootfs module paths are not the exact 31 modules plus the allowlisted index pair")
    for name, data in kernel_modules.items():
        check(root_modules[name] == data, f"rootfs/kernel module byte mismatch: {name}")
    check(digest_bytes(root_modules["modules.builtin"]) == EXPECTED_BUILTIN, "rootfs modules.builtin differs from kernel bundle")
    check(digest_bytes(root_modules["modules.builtin.modinfo"]) == EXPECTED_BUILTIN_MODINFO, "rootfs modules.builtin.modinfo differs from kernel bundle")

    inventory_path = RELEASE / "device-validation/native14-independent-charge-endpoint-20261007T1954HKT/device-module-tree-sha256.txt"
    check(digest_file(inventory_path) == EXPECTED_DEVICE_MODULE_INVENTORY, "copied device module inventory hash mismatch")
    inventory = parse_device_inventory(inventory_path.read_bytes())
    for relative, expected in inventory.items():
        name = relative.removeprefix("./")
        check(name in root_modules and digest_bytes(root_modules[name]) == expected, f"device/candidate module mismatch: {relative}")


def verify_privacy_and_resource_guards(package: dict[str, bytes]) -> None:
    with tarfile.open(fileobj=io.BytesIO(package["mu300-openwrt-rootfs.tar.gz"]), mode="r:gz") as root:
        for member in root.getmembers():
            if member.isdir():
                continue
            name = member.name.lstrip("./")
            check(not name.startswith("opt/mu300/android/") or (member.isreg() and name == "opt/mu300/android/system/bin/cltest"), f"per-device Android resource leaked into public rootfs: {name}")
            check(not name.startswith("etc/dropbear/dropbear_") and "ssh_host_" not in name and "__properties__" not in name, f"private identity path leaked: {name}")
            check(Path(name).name not in ("wcnmodem.bin", "gnssmodem.bin") and not Path(name).name.startswith("wifi_board_config"), f"per-device radio asset leaked: {name}")
            if member.issym() or member.islnk():
                target = member.linkname.lower()
                for private_token in ("dropbear_ed25519_host_key", "ssh_host_", "gnssmodem.bin", "wcnmodem.bin", "wifi_board_config", "device_known_hosts"):
                    check(private_token not in target, f"symlink points at private device data: {name} -> {member.linkname}")
    installer = package["install-device.sh"].decode("utf-8")
    for token in (
        "apex", "dev-properties", "linkerconfig", "system", "vendor", "gnssmodem.bin", "wcnmodem.bin",
        "wifi_board_config.ini", "wifi_board_config_ab.ini", "bt_configure_pskey.ini", "bt_configure_rf.ini",
        'cp -a "$OLD/lib/firmware/." "$NEW/lib/firmware/"', "rtl_fw_saved",
    ):
        check(token in installer, f"installer no longer guards/preserves required device resources: {token}")
    check("jsonfilter" in installer, "installer must parse the checksummed candidate manifest on device")
    check("this exact firmware version is already installed" in installer, "installer exact-version rejection missing")
    check("same-KREL modules differ beyond the kernel-bundle-verified built-in index pair" in installer, "same-KREL strict module guard missing")

    source_archive = RELEASE / SOURCE_NAME
    with tarfile.open(source_archive, "r:gz") as source:
        for member in source.getmembers():
            name = member.name.lower()
            check("device_known_hosts" not in name and "known_hosts.reinstalled" not in name, f"private host-key artifact path found: {member.name}")
            check("dropbear_ed25519_host_key" not in name and "ssh_host_" not in name, f"host private-key filename found: {member.name}")
            if member.name == "native14/install-kernel-device.sh":
                legacy = source.extractfile(member).read().decode("utf-8", errors="replace")
                check("native12" in legacy and "native11" in legacy, "legacy kernel installer no longer identifies its native12 target")
            if member.name == "native14/README.md":
                docs = source.extractfile(member).read().decode("utf-8")
                check("native11→native12" in docs and "严禁" in docs, "source README does not warn about the native12 legacy kernel installer")


def verify_observer(package: dict[str, bytes]) -> None:
    observer = package["observe-charge-to-full.sh"]
    check(digest_bytes(observer) == EXPECTED_OBSERVER, "strict observer source hash mismatch")
    script = observer.decode("utf-8")
    required = (
        "boot=$(cat /proc/sys/kernel/random/boot_id)",
        '[ "$(cat /proc/sys/kernel/random/boot_id)" = "$boot" ] || exit 2',
        '[ "$cap" = 100 ]', '[ "$bat_status" = Full ]', '[ "$chg_status" = Full ]',
        '[ "$bat_health" = Good ]', '[ "$chg_health" = Good ]', '[ "$online" = 1 ]',
        '[ "$temp" -ge 150 ]', '[ "$temp" -le 450 ]', '[ "$voltage" -ge 4200000 ]',
        '[ "$ocv" -ge 4000000 ]', '[ "$cv" -ge 4268000 ]',
        '[ "$cur" -ge 0 ]', '[ "$cur" -le 20000 ]',
        '[ "$avg" -ge -20000 ]', '[ "$avg" -le 20000 ]',
        't-first >= 120', "sleep 10",
    )
    for token in required:
        check(token in script, f"strict observer lost required condition: {token}")


def verify_source_payload(package: dict[str, bytes]) -> None:
    source_path = RELEASE / SOURCE_NAME
    with tarfile.open(source_path, "r:gz") as source:
        source_members = source.getmembers()
        members = {member.name: member for member in source_members}
        check(len(members) == len(source_members), "duplicate source archive members")
        private_link_tokens = ("dropbear_ed25519_host_key", "ssh_host_", "gnssmodem.bin", "wcnmodem.bin", "wifi_board_config", "device_known_hosts")
        for member in source_members:
            if member.issym() or member.islnk():
                target = member.linkname.lower()
                for token in private_link_tokens:
                    check(token not in target, f"source symlink points at private device data: {member.name} -> {member.linkname}")
        expected_files = {
            "native14/install-device.sh": ("install-device.sh", EXPECTED_INSTALLER),
            "native14/rollback-device.sh": ("rollback-device.sh", EXPECTED_ROLLBACK),
            "native14/test-deployment-compat.py": ("test-deployment-compat.py", EXPECTED_TEST),
            "native14/test-mu300-update-idempotent.py": ("test-mu300-update-idempotent.py", EXPECTED_UPDATER_TEST),
            "native14/overlay/opt/mu300/bin/mu300-update": ("mu300-update", EXPECTED_UPDATER),
            "project/rootfs/overlay/opt/mu300/bin/mu300-update": ("mu300-update", EXPECTED_UPDATER),
            "native14/observe-charge-to-full.sh": ("observe-charge-to-full.sh", EXPECTED_OBSERVER),
            "native14/overlay/opt/mu300/lib/fcc-record.sh": (None, EXPECTED_FCC_LIBRARY),
            "native14/overlay/opt/mu300/bin/mu300-fcc-record": (None, EXPECTED_FCC_SERVICE),
        }
        for member_name, (package_name, expected) in expected_files.items():
            check(member_name in members, f"source archive missing {member_name}")
            payload = source.extractfile(members[member_name]).read()
            check(digest_bytes(payload) == expected, f"source file hash mismatch: {member_name}")
            if package_name:
                check(payload == package[package_name], f"package/source bytes differ: {member_name}")
            if member_name.endswith("/opt/mu300/bin/mu300-update"):
                check(members[member_name].mode & 0o111, f"source updater is not executable: {member_name}")

        release_mirrors = {
            "native14/assemble-restorefix3.py": "assemble-restorefix3.py",
            "native14/verify-restorefix3-delivery.py": "verify-restorefix3-delivery.py",
            "native14/README.md": "README.md",
            "native14/README-restorefix3.md": "README-restorefix3.md",
            "native14/RESTOREFIX3-REVIEW.md": "RESTOREFIX3-REVIEW.md",
            "native14/device-validation/updater-idempotency-audit-20261008.md": "updater-idempotency-audit-20261008.md",
            "native14/device-validation/test-deployment-compat-restorefix3-20261008.stdout.log": "test-deployment-compat-restorefix3-20261008.stdout.log",
            "native14/device-validation/test-mu300-update-idempotent-restorefix3-20261008.stdout.log": "test-mu300-update-idempotent-restorefix3-20261008.stdout.log",
            "native14/observe-native14-discharge.sh": "observe-native14-discharge.sh",
            "native14/observe-native14-await-discharge.sh": "observe-native14-await-discharge.sh",
            "native14/observe-native14-fcc-status.sh": "observe-native14-fcc-status.sh",
        }
        for member_name, rel in release_mirrors.items():
            check(member_name in members and members[member_name].isreg(), f"source archive missing regular file {member_name}")
            payload = source.extractfile(members[member_name]).read()
            check(payload == (RELEASE / rel).read_bytes(), f"source/release bytes differ: {member_name}")

        for member_name, expected in (
            ("native14/observe-native14-discharge.sh", EXPECTED_DISCHARGE),
            ("native14/observe-native14-await-discharge.sh", EXPECTED_AWAIT_DISCHARGE),
            ("native14/observe-native14-fcc-status.sh", EXPECTED_FCC_STATUS),
            ("native14/device-validation/test-deployment-compat-restorefix3-20261008.stdout.log", EXPECTED_TEST_LOG),
            ("native14/device-validation/test-mu300-update-idempotent-restorefix3-20261008.stdout.log", EXPECTED_UPDATER_TEST_LOG),
        ):
            check(member_name in members, f"source archive missing test/observer material: {member_name}")
            check(digest_bytes(source.extractfile(members[member_name]).read()) == expected, f"source evidence hash mismatch: {member_name}")

        old_tool_path = "native14/install-kernel-device.sh"
        check(old_tool_path in members and members[old_tool_path].isreg(), "native12 legacy kernel tool is missing from source archive")
        legacy = source.extractfile(members[old_tool_path]).read().decode("utf-8", errors="replace")
        check("native12" in legacy and "native11" in legacy, "legacy kernel installer no longer identifies its native12 target")
        readme = source.extractfile(members["native14/README.md"]).read().decode("utf-8")
        check("native11→native12" in readme and "严禁" in readme, "source README does not warn about the native12 legacy kernel installer")
        awaiter = source.extractfile(members["native14/observe-native14-await-discharge.sh"]).read().decode("utf-8")
        check("/tmp/observe-native14-fcc.sh" in awaiter, "discharge observer alias contract changed")


def verify_old_immutable_inputs() -> None:
    check(digest_file(OLD_RELEASE / ARCHIVE_NAME) == EXPECTED_OLD_ARCHIVE, "prior immutable firmware archive changed")
    check(digest_file(OLD_RELEASE / SOURCE_NAME) == EXPECTED_OLD_PROJECT_SOURCE, "prior immutable project source archive changed")
    check(digest_file(OLD_RELEASE / LINUX_SOURCE_NAME) == EXPECTED_OLD_LINUX_SOURCE, "prior immutable Linux source archive changed")
    old_zip = FIRMWARE / "u30air-native14-restorefix2-2026.10.07-test.zip"
    if old_zip.is_file():
        check(digest_file(old_zip) == EXPECTED_OLD_ZIP, "prior immutable restorefix ZIP changed")


def verify_zip(sums: dict[str, str]) -> tuple[Path, str]:
    zip_path = FIRMWARE / ZIP_NAME
    sidecar = Path(str(zip_path) + ".sha256")
    check(zip_path.is_file() and sidecar.is_file(), "new restorefix3 ZIP or its SHA-256 sidecar is missing")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    actual = digest_file(zip_path)
    check(expected == actual, "outer ZIP SHA-256 sidecar mismatch")
    with zipfile.ZipFile(zip_path, "r") as archive:
        check(archive.testzip() is None, "ZIP CRC test failed")
        names = archive.namelist()
        check(len(names) == len(set(names)), "duplicate ZIP member")
        expected_names = {f"{ZIP_PREFIX}/{name}" for name in sums} | {f"{ZIP_PREFIX}/SHA256SUMS"}
        actual_names = set(names)
        check(actual_names == expected_names, f"ZIP/release file set mismatch: missing={expected_names-actual_names}; extra={actual_names-expected_names}")
        check(archive.read(f"{ZIP_PREFIX}/SHA256SUMS") == (RELEASE / "SHA256SUMS").read_bytes(), "ZIP SHA256SUMS differs from release")
        for name, expected_hash in sums.items():
            with archive.open(f"{ZIP_PREFIX}/{name}") as stream:
                check(digest_stream(stream) == expected_hash, f"ZIP payload differs from release file: {name}")
        for name in names:
            low = name.lower()
            check("device_known_hosts" not in low and "known_hosts.reinstalled" not in low, f"private host-key artifact in ZIP: {name}")
            check("dropbear_ed25519_host_key" not in low and "ssh_host_" not in low, f"host private-key artifact in ZIP: {name}")
    return zip_path, actual


def main() -> None:
    check(RELEASE.is_dir(), f"restorefix3 release directory is missing: {RELEASE}")
    verify_old_immutable_inputs()
    sums = verify_release_sums()
    release_manifest = json.loads((RELEASE / "BUILD-MANIFEST.json").read_text(encoding="utf-8"))
    delivery = json.loads((RELEASE / "delivery.json").read_text(encoding="utf-8"))
    check(delivery.get("archive") == ARCHIVE_NAME, "delivery.json archive name mismatch")
    archive_path = RELEASE / ARCHIVE_NAME
    check(digest_file(archive_path) == delivery.get("sha256"), "delivery.json inner archive hash mismatch")
    check(release_manifest["version"] == VERSION and release_manifest["kernel_release"] == KREL, "release manifest version/KREL mismatch")
    check(release_manifest["release_status"] == "offline candidate; restorefix3 has not been flashed", "release status must state package is unflashed")
    check(release_manifest["hardware_boot_tested"] is False and release_manifest["hardware_charging_tested"] is False, "candidate hardware status is overstated")
    check(release_manifest["independent_capacity_accuracy_verified"] is False, "independent capacity accuracy must remain unverified")
    check(release_manifest["deployment_compatibility_tests"]["cases_passed"] == 40, "offline compatibility test scenario count mismatch")
    check(release_manifest["deployment_compatibility_tests"]["script_sha256"] == EXPECTED_TEST, "offline compatibility test script hash mismatch")
    check(release_manifest["deployment_compatibility_tests"]["restorefix3_stdout_sha256"] == EXPECTED_TEST_LOG, "restorefix3 compatibility log hash mismatch")
    check(release_manifest["deployment_compatibility_tests"]["hardware_validation"] is False, "offline tests must not be labelled hardware validation")
    check(release_manifest["updater_idempotency_tests"]["cases_passed"] == 3, "updater test scenario count mismatch")
    check(release_manifest["updater_idempotency_tests"]["script_sha256"] == EXPECTED_UPDATER_TEST, "updater test script hash mismatch")
    check(release_manifest["updater_idempotency_tests"]["restorefix3_stdout_sha256"] == EXPECTED_UPDATER_TEST_LOG, "updater test log hash mismatch")
    check(release_manifest["updater_idempotency_tests"]["hardware_validation"] is False, "updater fixture must not be labelled hardware validation")

    package, package_sums = package_members(archive_path)
    check(package["BUILD-MANIFEST.json"] == (RELEASE / "BUILD-MANIFEST.json").read_bytes(), "top-level/package manifest copies differ")
    manifest = json.loads(package["BUILD-MANIFEST.json"])
    check(manifest == release_manifest, "inner and outer manifests differ")
    check(package["install-device.sh"] and digest_bytes(package["install-device.sh"]) == EXPECTED_INSTALLER, "installer hash mismatch")
    check(digest_bytes(package["rollback-device.sh"]) == EXPECTED_ROLLBACK, "rollback hash mismatch")
    check(digest_bytes(package["test-deployment-compat.py"]) == EXPECTED_TEST, "offline test script hash mismatch")
    check(digest_bytes(package["test-mu300-update-idempotent.py"]) == EXPECTED_UPDATER_TEST, "updater test script hash mismatch")
    check(digest_bytes(package["mu300-update"]) == EXPECTED_UPDATER, "package mu300-update hash mismatch")
    with tarfile.open(archive_path, "r:gz") as outer:
        updater_members = [m for m in outer.getmembers() if m.name == "package/mu300-update"]
        check(len(updater_members) == 1 and updater_members[0].isreg() and updater_members[0].mode & 0o111,
              "package updater must be exactly one executable regular file")
    check(manifest["runtime_restore_sha256"] == {"library": EXPECTED_FCC_LIBRARY, "service": EXPECTED_FCC_SERVICE}, "FCC manifest hashes mismatch")
    check(manifest["charge_endpoint_observer_sha256"] == EXPECTED_OBSERVER, "observer manifest hash mismatch")
    check(manifest["kernel_bundle_sha256"] == digest_bytes(package["mu300-kernel-7.2.8-u30air-native14.tar.gz"]), "manifest kernel bundle hash mismatch")

    root_tar = package["mu300-openwrt-rootfs.tar.gz"]
    with tarfile.open(fileobj=io.BytesIO(root_tar), mode="r:gz") as new_root:
        root_entries = new_root.getmembers()
        root_members = {member.name: member for member in root_entries}
        check(len(root_members) == len(root_entries), "duplicate rootfs member path")
        root_payloads = {member.name: new_root.extractfile(member).read() for member in root_members.values() if member.isreg()}
    check(root_payloads["./etc/mu300/image-version"].strip() == VERSION.encode(), "rootfs image-version mismatch")
    updater_candidates = [m for m in root_entries if m.name == UPDATER_PATH]
    check(len(updater_candidates) == 1 and updater_candidates[0].isreg() and updater_candidates[0].mode & 0o111,
          "rootfs updater must be exactly one executable regular member")
    check(digest_bytes(root_payloads[UPDATER_PATH]) == EXPECTED_UPDATER, "rootfs updater hash mismatch")
    check(root_payloads[UPDATER_PATH] == package["mu300-update"], "package updater and rootfs updater bytes differ")
    check(digest_bytes(root_payloads["./opt/mu300/lib/fcc-record.sh"]) == EXPECTED_FCC_LIBRARY, "rootfs FCC restore library bytes mismatch")
    check(digest_bytes(root_payloads["./opt/mu300/bin/mu300-fcc-record"]) == EXPECTED_FCC_SERVICE, "rootfs FCC service bytes mismatch")
    check(root_payloads["./opt/mu300/bin/mu300-fcc-record"].find(b"fcc_restore_write") >= 0, "FCC restore service lacks restore transaction entry point")
    check(root_members["./opt/mu300/bin/mu300-usb"].mode & 0o111, "mu300-usb is not executable in rootfs")
    check(len(root_payloads["./lib/firmware/rtl_nic/rtl8153b-2.fw"]) == 1880, "RTL8153B firmware payload size mismatch")
    for path in ("./etc/hotplug.d/net/90-u30air-lan", "./etc/hotplug.d/iface/90-u30air-lan", "./opt/mu300/bin/mu300-lan-usb"):
        check(root_members[path].mode & 0o111, f"network helper is not executable: {path}")
    check(b"ip link set dev \"$iface\" master br-lan" in root_payloads["./opt/mu300/bin/mu300-lan-usb"], "RTL8153B helper no longer attaches to br-lan")

    old_archive = OLD_RELEASE / ARCHIVE_NAME
    with tarfile.open(old_archive, "r:gz") as old:
        old_root_payload = old.extractfile("package/mu300-openwrt-rootfs.tar.gz").read()
        old_kernel_payload = old.extractfile("package/mu300-kernel-7.2.8-u30air-native14.tar.gz").read()
    check(package["mu300-kernel-7.2.8-u30air-native14.tar.gz"] == old_kernel_payload, "compiled kernel bundle was rebuilt or modified")
    old_files, new_files = compare_rootfs_members(old_root_payload, root_tar)
    mutable_rootfs_files = {"./etc/mu300/image-version", UPDATER_PATH}
    check(set(old_files) - mutable_rootfs_files == set(new_files) - mutable_rootfs_files, "rootfs regular file paths changed unexpectedly")
    check(release_manifest["kernel_bundle_sha256"] == digest_bytes(old_kernel_payload), "manifest kernel hash differs from frozen restorefix2 kernel bundle")

    verify_module_payload(package, root_payloads)
    verify_privacy_and_resource_guards(package)
    verify_observer(package)
    verify_source_payload(package)
    check(package["README.md"].decode("utf-8").find("尚未刷入") >= 0, "package README omits unflashed status")
    check("/tmp/observe-native14-fcc.sh" in package["README.md"].decode("utf-8"), "package README omits the planned discharge observer alias")
    review = package["RESTOREFIX3-REVIEW.md"].decode("utf-8").lower()
    check("low-soc deviation" in review and "physical measurement" in review, "package review omits low-SOC validation limitation")
    check("no firmware was flashed" in review, "package review omits unflashed status")
    zip_path, zip_hash = verify_zip(sums)
    print(json.dumps({
        "result": "PASS",
        "version": VERSION,
        "release": str(RELEASE),
        "zip": str(zip_path),
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": zip_hash,
        "kernel_bundle_sha256": digest_bytes(package["mu300-kernel-7.2.8-u30air-native14.tar.gz"]),
        "rootfs_members": len(root_members),
        "device_modules_matched": 31,
        "candidate_modules": 33,
        "FCC_library_sha256": EXPECTED_FCC_LIBRARY,
        "FCC_service_sha256": EXPECTED_FCC_SERVICE,
        "offline_compatibility_cases": 40,
        "updater_idempotency_cases": 3,
        "hardware_boot_tested": False,
        "hardware_charging_tested": False,
        "independent_capacity_accuracy_verified": False,
    }, indent=2))


if __name__ == "__main__":
    main()
