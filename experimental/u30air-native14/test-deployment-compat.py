#!/usr/bin/env python3
"""Run the real native14 installer and rollback scripts against temporary files in WSL."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
KREL = "7.2.8-u30air-native14"
BASE_VERSION = "u30air-native14-android-charge-2026.10.05-test"
NEXT_VERSION = "u30air-native14-restorefix3-2026.10.08-test"
STOCK_DTBO_SHA = "581020c762acbce7dcc52a540860410f7c93752bec990e2b6b5e1cbb6b418d39"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path: Path, data: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    path.chmod(mode)


def module_tree_sha(root: Path) -> str:
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AssertionError(f"module symlink is not supported: {path}")
        if path.is_file():
            records.append(f"{sha(path)}  ./{path.relative_to(root).as_posix()}\n")
    if not records:
        raise AssertionError(f"empty module tree: {root}")
    return hashlib.sha256("".join(sorted(records)).encode()).hexdigest()


def make_modules(root: Path, marker: str, metadata: bool = False,
                 missing_module: str = "", changed_metadata: bool = False,
                 extra_file: str = "", symlink_name: str = "") -> None:
    moddir = root / "lib/modules" / KREL
    moddir.mkdir(parents=True, exist_ok=True)
    for name, content in {
        "driver-a.ko": f"module-a:{marker}\n",
        "driver-b.ko": f"module-b:{marker}\n",
    }.items():
        if name != missing_module:
            write(moddir / name, content)
    if metadata:
        builtin = f"built-in:{marker}\n"
        modinfo = f"modinfo:{marker}\n"
        if changed_metadata:
            builtin = "changed-builtin\n"
        write(moddir / "modules.builtin", builtin)
        write(moddir / "modules.builtin.modinfo", modinfo)
    if extra_file:
        write(moddir / extra_file, "unexpected module metadata\n")
    if symlink_name:
        os.symlink("driver-a.ko", moddir / symlink_name)


def make_module_payload(root: Path, marker: str) -> None:
    moddir = root / "modules"
    moddir.mkdir(parents=True, exist_ok=True)
    for name, content in {
        "driver-a.ko": f"module-a:{marker}\n",
        "driver-b.ko": f"module-b:{marker}\n",
    }.items():
        write(moddir / name, content)
    write(root / "modules.builtin", f"built-in:{marker}\n")
    write(root / "modules.builtin.modinfo", f"modinfo:{marker}\n")


def make_rootfs(path: Path, version: str, modules_marker: str,
                metadata_mode: str = "pair", missing_module: str = "",
                extra_module_file: str = "", candidate_symlink: str = "") -> None:
    root = path.parent / "rootfs-tree"
    root.mkdir()
    write(root / "etc/mu300/image-version", version + "\n")
    write(root / "sbin/init", "#!/bin/sh\n", 0o755)
    write(root / "opt/mu300/bin/mu300-usb", "#!/bin/sh\n", 0o755)
    write(root / "lib/firmware/rtl_nic/rtl8153b-2.fw", "rtl-fw\n")
    write(root / "etc/passwd", "root:x:0:0:root:/root:/bin/sh\n")
    write(root / "etc/shadow", "root:*:0:0:99999:7:::\n")
    write(root / "etc/group", "root:x:0:\n")
    make_modules(root, modules_marker, metadata=(metadata_mode != "none"),
                 missing_module=missing_module, extra_file=extra_module_file,
                 symlink_name=candidate_symlink)
    if metadata_mode == "one":
        (root / "lib/modules" / KREL / "modules.builtin.modinfo").unlink()
    elif metadata_mode == "mismatch":
        write(root / "lib/modules" / KREL / "modules.builtin", "candidate-tampered\n")
    elif metadata_mode == "mismatch-modinfo":
        write(root / "lib/modules" / KREL / "modules.builtin.modinfo", "candidate-tampered\n")
    with tarfile.open(path, "w:gz") as archive:
        archive.add(root, arcname=".")


def make_kernel(path: Path, modules_marker: str) -> None:
    root = path.parent / "kernel-tree"
    root.mkdir()
    write(root / "kernel.release", KREL + "\n")
    write(root / "devices", "u30air\n")
    write(root / "Image", "test-image\n")
    write(root / "ramdisk-generic.lz4", "test-ramdisk\n")
    make_module_payload(root, modules_marker)
    with tarfile.open(path, "w:gz") as archive:
        archive.add(root, arcname=".")


def uci_stub(path: Path) -> None:
    write(path, """#!/bin/sh
case "$*" in
  '-q changes network') exit 0 ;;
  '-q get network.lan.device') echo br-lan ;;
  '-q show network') echo "network.lan.name='br-lan'" ;;
  '-q get network.lan.type') echo bridge ;;
  *) exit 1 ;;
esac
""", 0o755)


def jsonfilter_stub(path: Path) -> None:
    write(path, """#!/usr/bin/env python3
import json, sys
args=sys.argv[1:]
try:
    source=args[args.index('-i')+1]
    expr=args[args.index('-e')+1]
    if not expr.startswith('@.'):
        raise ValueError('unsupported expression')
    value=json.load(open(source, encoding='utf-8'))[expr[2:]]
    if not isinstance(value, str):
        raise TypeError('field is not a string')
    print(value)
except Exception:
    sys.exit(1)
""", 0o755)


def fake_updater(path: Path) -> None:
    write(path, """#!/bin/sh
set -eu
[ "${1:-}" = boot ] || exit 2
work=$TEST_ROOT/updater-work
rm -rf "$work"; mkdir -p "$work"
printf '%s\n' "${MU300_RELEASE:-}" > "$TEST_ROOT/updater-release"
    tar -xzf "$MU300_KERNEL_BUNDLE" -C "$work" ./kernel.release ./modules ./modules.builtin ./modules.builtin.modinfo
    krel=$(cat "$work/kernel.release")
    mkdir -p "$MU300_DISK/openwrt/lib/modules/$krel"
    for module in "$work"/modules/*.ko; do cp -p "$module" "$MU300_DISK/openwrt/lib/modules/$krel/"; done
    if [ "${UPDATE_FAIL:-0}" = 1 ]; then
        printf 'injected interrupted module write\n' > "$MU300_DISK/openwrt/lib/modules/$krel/driver-a.ko"
    fi
    printf 'changed-test-boot\n' > "$TEST_ROOT/dev/boot_b"
[ "${UPDATE_FAIL:-0}" = 1 ] && exit 42
exit 0
""", 0o755)


def failure_stubs(directory: Path) -> None:
    write(directory / "find", """#!/bin/sh
if [ "${FIND_FAIL:-}" = module-files ] && [ "$*" = '. -type f -print' ]; then exit 81; fi
exec /usr/bin/find "$@"
""", 0o755)
    write(directory / "sort", """#!/bin/sh
[ "${SORT_FAIL:-0}" = 1 ] && exit 82
exec /usr/bin/sort "$@"
""", 0o755)
    write(directory / "awk", """#!/bin/sh
if [ "${AWK_FAIL_INODE:-0}" = 1 ]; then
  for arg do case "$arg" in expected=*) exit 86 ;; esac; done
fi
exec /usr/bin/awk "$@"
""", 0o755)
    write(directory / "ls", """#!/bin/sh
count=0
if [ -n "${LS_CALLS_FILE:-}" ]; then
  [ ! -f "$LS_CALLS_FILE" ] || count=$(cat "$LS_CALLS_FILE")
  count=$((count + 1))
  printf '%s\n' "$count" > "$LS_CALLS_FILE"
  [ "${LS_FAIL_CALL:-}" != "$count" ] || exit 84
  if [ "${LS_MALFORMED_CALL:-}" = "$count" ]; then
    printf 'not-an-inode %s\n' "${2:-}"
    exit 0
  fi
fi
exec /bin/ls "$@"
""", 0o755)
    write(directory / "tar", """#!/bin/sh
if [ "${TAR_FAIL_NEW_EXTRACT:-0}" = 1 ]; then
  want_dest=0
  destination=
  for arg do
    if [ "$want_dest" = 1 ]; then destination=$arg; want_dest=0; continue; fi
    [ "$arg" != -C ] || want_dest=1
  done
  case "$destination" in
    */openwrt.native14-new)
      mkdir -p "$destination/.partial-test" || exit 85
      exit 85
      ;;
  esac
fi
exec /usr/bin/tar "$@"
""", 0o755)
    write(directory / "sha256sum", """#!/bin/sh
for arg do
  case "$arg" in *boot_b*)
    case "$arg" in *"${SHA_FAIL_TARGET:-__no_match__}"*) status=FAIL ;; *) status=OK ;; esac
    [ -z "${SHA_LOG:-}" ] || printf '%s %s\n' "$status" "$arg" >> "$SHA_LOG"
    ;;
  esac
  if [ -n "${SHA_FAIL_TARGET:-}" ]; then
    case "$arg" in *"$SHA_FAIL_TARGET"*) exit 83 ;; esac
  fi
  if [ -n "${SHA_MALFORMED_TARGET:-}" ]; then
    case "$arg" in *"$SHA_MALFORMED_TARGET"*) printf 'malformed  %s\\n' "$arg"; exit 0 ;; esac
  fi
done
exec /usr/bin/sha256sum "$@"
""", 0o755)


def transformed_scripts(test_root: Path, pkg: Path) -> None:
    installer = (HERE / "install-device.sh").read_text()
    installer = installer.replace("DISK=/mnt/mu300-disk", f"DISK={test_root}/mnt/mu300-disk")
    installer = installer.replace("BIN=/opt/mu300/bin", f"BIN={test_root}/opt/mu300/bin")
    installer = installer.replace(
        '[ -d "$OLD" ] && [ / -ef "$OLD" ]',
        '[ -d "$OLD" ] && [ "$TEST_ROOT/current-root" -ef "$OLD" ]',
    )
    installer = installer.replace("/etc/openwrt_release", f"{test_root}/etc/openwrt_release")
    installer = installer.replace('for u in /sys/class/block/*/uevent; do', 'for u in "$TEST_ROOT"/sys/class/block/*/uevent; do')
    installer = installer.replace("printf '/dev/%s\\n' \"${d##*/}\"", 'printf \'%s/dev/%s\\n\' "$TEST_ROOT" "${d##*/}"')
    installer = installer.replace('cat "/sys/class/block/${1##*/}/size"', 'cat "$TEST_ROOT/sys/class/block/${1##*/}/size"')
    installer = installer.replace('/sys/class/block/${disk_source##*/}/loop/offset', '$TEST_ROOT/sys/class/block/${disk_source##*/}/loop/offset')
    installer = installer.replace('/proc/mounts', f'{test_root}/proc/mounts')

    rollback = (HERE / "rollback-device.sh").read_text()
    rollback = rollback.replace("DISK=/mnt/mu300-disk", f"DISK={test_root}/mnt/mu300-disk")
    rollback = rollback.replace('for u in /sys/class/block/*/uevent; do', 'for u in "$TEST_ROOT"/sys/class/block/*/uevent; do')
    rollback = rollback.replace('boot_dev=/dev/${d##*/}', 'boot_dev=$TEST_ROOT/dev/${d##*/}')
    rollback = rollback.replace('boot_dev=/dev/block/${d##*/}', 'boot_dev=$TEST_ROOT/dev/block/${d##*/}')
    rollback = rollback.replace('cat "/sys/class/block/${boot_dev##*/}/size"', 'cat "$TEST_ROOT/sys/class/block/${boot_dev##*/}/size"')
    write(pkg / "install-device.sh", installer, 0o755)
    write(pkg / "rollback-device.sh", rollback, 0o755)


def make_environment(root: Path, pkg: Path, current_version: str, existing_modules: bool,
                     candidate_version: str, current_module_marker: str = "same",
                     candidate_module_marker: str = "same", fail_update: bool = False,
                     manifest_mode: str = "valid", candidate_metadata_mode: str = "pair",
                     candidate_missing_module: str = "", candidate_extra_module_file: str = "",
                     candidate_symlink: str = "",
                     current_module_metadata: bool = False,
                     current_metadata_changed: bool = False) -> tuple[Path, dict[str, str]]:
    test_root = root / "fake-device"
    disk = test_root / "mnt/mu300-disk"
    old = disk / "openwrt"
    old.mkdir(parents=True)
    write(test_root / "etc/openwrt_release", "DISTRIB_ID='OpenWrt'\n")
    (test_root / "dev/block").mkdir(parents=True)
    (test_root / "dev/boot_b").write_bytes(b"O" * (64 * 1024 * 1024))
    (test_root / "dev/dtbo_b").write_bytes(b"D" * (8 * 1024 * 1024))
    os.symlink("../boot_b", test_root / "dev/block/boot_b")
    os.symlink("../dtbo_b", test_root / "dev/block/dtbo_b")
    (test_root / "sys/class/block/boot_b").mkdir(parents=True)
    (test_root / "sys/class/block/dtbo_b").mkdir(parents=True)
    (test_root / "sys/class/block/fake-loop/loop").mkdir(parents=True)
    write(test_root / "sys/class/block/boot_b/uevent", "PARTNAME=boot_b\n")
    write(test_root / "sys/class/block/boot_b/size", "131072\n")
    write(test_root / "sys/class/block/dtbo_b/uevent", "PARTNAME=dtbo_b\n")
    write(test_root / "sys/class/block/dtbo_b/size", "16384\n")
    write(test_root / "sys/class/block/fake-loop/loop/offset", "4194304\n")
    write(test_root / "proc/mounts", f"/dev/fake-loop {disk} ext4 rw 0 0\n")
    (test_root / "opt/mu300/bin").mkdir(parents=True)
    write(test_root / "opt/mu300/bin/mu300-device", "#!/bin/sh\necho u30air\n", 0o755)
    old_version = old / "etc/mu300/image-version"
    write(old_version, current_version + "\n")
    write(old / "etc/config/network", "config device\n")
    write(old / "etc/mu300/settings", "preserved\n")
    write(old / "etc/dropbear/config", "preserved\n")
    write(old / "etc/passwd", "root:x:0:0:root:/root:/bin/sh\n")
    write(old / "etc/shadow", "root:*:0:0:99999:7:::\n")
    write(old / "etc/group", "root:x:0:\n")
    write(old / "etc/rc.local", "exit 0\n")
    for folder in ("apex", "dev-properties", "linkerconfig", "system", "vendor"):
        (old / "opt/mu300/android" / folder).mkdir(parents=True, exist_ok=True)
    write(old / "opt/mu300/android/apex/com.android.runtime/bin/linker64", "linker\n", 0o755)
    write(old / "opt/mu300/android/vendor/bin/modem_control", "modem\n", 0o755)
    write(old / "opt/mu300/android/linkerconfig/ld.config.txt", "config\n")
    write(old / "opt/mu300/android/dev-properties/build.prop", "prop\n")
    for name in ("gnssmodem.bin", "wcnmodem.bin", "wifi_board_config.ini",
                 "wifi_board_config_ab.ini", "bt_configure_pskey.ini", "bt_configure_rf.ini"):
        write(old / "lib/firmware" / name, name + "\n")
    write(old / "lib/firmware/rtl_nic/rtl8153b-2.fw", "rtl-fw\n")
    if existing_modules:
        make_modules(old, current_module_marker, metadata=current_module_metadata,
                     changed_metadata=current_metadata_changed)

    rootfs = pkg / "mu300-openwrt-rootfs.tar.gz"
    kernel = pkg / f"mu300-kernel-{KREL}.tar.gz"
    make_rootfs(rootfs, candidate_version, candidate_module_marker,
                 metadata_mode=candidate_metadata_mode,
                 missing_module=candidate_missing_module,
                 extra_module_file=candidate_extra_module_file,
                 candidate_symlink=candidate_symlink)
    make_kernel(kernel, candidate_module_marker)
    manifest = {"version": candidate_version, "kernel_release": KREL, "device": "u30air"}
    manifest_text = json.dumps(manifest, indent=2) + "\n"
    if manifest_mode == "missing":
        pass
    elif manifest_mode == "corrupt-after-sums":
        write(pkg / "BUILD-MANIFEST.json", manifest_text)
    elif manifest_mode == "mismatch-krel":
        manifest["kernel_release"] = "7.2.8-u30air-native13"
        write(pkg / "BUILD-MANIFEST.json", json.dumps(manifest, indent=2) + "\n")
    elif manifest_mode == "mismatch-version":
        manifest["version"] = "u30air-native14-other-test"
        write(pkg / "BUILD-MANIFEST.json", json.dumps(manifest, indent=2) + "\n")
    elif manifest_mode == "duplicate-version":
        write(pkg / "BUILD-MANIFEST.json", manifest_text.replace('  "kernel_release"', '  "version": "u30air-native14-duplicate-test",\n  "kernel_release"'))
    elif manifest_mode == "nonstring-version":
        write(pkg / "BUILD-MANIFEST.json", manifest_text.replace(f'"version": "{candidate_version}"', '"version": 14'))
    else:
        write(pkg / "BUILD-MANIFEST.json", manifest_text)
    fake_updater(pkg / "mu300-update")

    # The fake DTBO only exercises the code path; bind the temporary installer to its digest.
    test_dtbo_sha = sha(test_root / "dev/dtbo_b")
    write(test_root / "current-root-link-placeholder", "unused\n")
    os.symlink(old, test_root / "current-root")
    return test_root, {"disk": str(disk), "old": str(old), "dtbo_sha": test_dtbo_sha,
                       "fail_update": "1" if fail_update else "0",
                       "missing_manifest": "1" if manifest_mode == "missing" else "0",
                       "candidate_version": candidate_version}


def run_case(label: str, *, current_version: str = BASE_VERSION, existing_modules: bool = True,
             current_module_marker: str = "same", candidate_module_marker: str = "same",
             fail_update: bool = False, manifest_mode: str = "valid",
             candidate_version: str = NEXT_VERSION, action: str = "install",
             helper_fail: str = "", candidate_metadata_mode: str = "pair",
             candidate_missing_module: str = "", candidate_extra_module_file: str = "",
             candidate_symlink: str = "",
             current_module_metadata: bool = False,
             current_metadata_changed: bool = False,
             actual_updater: bool = False) -> tuple[int, str, Path, Path, dict[str, str]]:
    root = Path(tempfile.mkdtemp(prefix=f"u30-{label}-"))
    pkg = root / "package"
    pkg.mkdir()
    test_root, data = make_environment(root, pkg, current_version, existing_modules,
                                      candidate_version, current_module_marker,
                                      candidate_module_marker, fail_update, manifest_mode,
                                      candidate_metadata_mode, candidate_missing_module,
                                      candidate_extra_module_file, candidate_symlink,
                                      current_module_metadata,
                                      current_metadata_changed)
    transformed_scripts(test_root, pkg)
    if actual_updater:
        # Exercise the shipped updater itself, with only device paths redirected to
        # temporary regular files. The real bundle installs its 31 modules before
        # the deliberately invalid boot header makes the update fail.
        runtime = HERE.parent / "runtime-update"
        updater = (runtime / "mu300-update").read_text()
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
            if old not in updater:
                raise AssertionError(f"actual updater fixture could not bind path: {old}")
            updater = updater.replace(old, new)
        write(pkg / "mu300-update", updater, 0o755)
        shutil.copyfile(runtime / f"mu300-kernel-{KREL}.tar.gz",
                        pkg / f"mu300-kernel-{KREL}.tar.gz")
    # Bind only this temporary fixture to its synthetic DTBO hash.
    installer = (pkg / "install-device.sh").read_text()
    installer = installer.replace(STOCK_DTBO_SHA, data["dtbo_sha"])
    write(pkg / "install-device.sh", installer, 0o755)
    if manifest_mode != "missing":
        with (pkg / "SHA256SUMS").open("w") as output:
            for name in sorted(p.name for p in pkg.iterdir() if p.is_file() and p.name != "SHA256SUMS"):
                output.write(f"{sha(pkg / name)}  {name}\n")
    elif manifest_mode == "missing":
        with (pkg / "SHA256SUMS").open("w") as output:
            for name in sorted(p.name for p in pkg.iterdir() if p.is_file() and p.name != "SHA256SUMS"):
                if name != "BUILD-MANIFEST.json":
                    output.write(f"{sha(pkg / name)}  {name}\n")
    if manifest_mode == "corrupt-after-sums":
        with (pkg / "BUILD-MANIFEST.json").open("a") as stream:
            stream.write("corruption\n")
    # A missing manifest is deliberately not included in the checksum list or package directory.
    if manifest_mode == "missing":
        (pkg / "BUILD-MANIFEST.json").unlink(missing_ok=True)

    stubs = root / "stubs"
    stubs.mkdir()
    write(stubs / "id", "#!/bin/sh\necho 0\n", 0o755)
    write(stubs / "uname", "#!/bin/sh\n[ \"${1:-}\" = -r ] && echo '" + KREL + "'\n", 0o755)
    write(stubs / "date", "#!/bin/sh\necho 20000101000000Z\n", 0o755)
    write(stubs / "ping", "#!/bin/sh\nexit 1\n", 0o755)
    write(stubs / "df", "#!/bin/sh\necho 'Filesystem 1024-blocks Used Available Capacity Mounted on'\necho fake 10000000 0 9000000 0% \"$2\"\n", 0o755)
    uci_stub(stubs / "uci")
    jsonfilter_stub(stubs / "jsonfilter")
    failure_stubs(stubs)
    env = os.environ.copy()
    env.update({"PATH": str(stubs) + ":" + env.get("PATH", ""),
                "TEST_ROOT": str(test_root), "UPDATE_FAIL": data["fail_update"],
                "LS_CALLS_FILE": str(root / "ls-calls")})
    sha_log = root / "boot-sha-calls.log"
    env["SHA_LOG"] = str(sha_log)
    if helper_fail == "find":
        env["FIND_FAIL"] = "module-files"
    elif helper_fail == "sort":
        env["SORT_FAIL"] = "1"
    elif helper_fail == "module-sha":
        env["SHA_FAIL_TARGET"] = "driver-a.ko"
    elif helper_fail == "module-sha-malformed":
        env["SHA_MALFORMED_TARGET"] = "driver-a.ko"
    elif helper_fail == "boot-hashes-both":
        env["SHA_FAIL_TARGET"] = "boot_b"
    elif helper_fail == "boot-hash-backup":
        env["SHA_FAIL_TARGET"] = "boot_b.img"
    elif helper_fail == "staging-ls-first":
        env["LS_FAIL_CALL"] = "1"
    elif helper_fail == "staging-ls-cleanup":
        env["LS_FAIL_CALL"] = "2"
        env["TAR_FAIL_NEW_EXTRACT"] = "1"
    elif helper_fail == "staging-ls-malformed":
        env["LS_MALFORMED_CALL"] = "1"
    elif helper_fail == "staging-awk-failure":
        env["AWK_FAIL_INODE"] = "1"
    result = subprocess.run(["sh", str(pkg / "install-device.sh")], env=env,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if action.startswith("rollback-") and result.returncode == 0:
        backups = sorted((Path(data["disk"]) / ".mu300-native14-rollback").iterdir())
        assert len(backups) == 1, backups
        backup = backups[0]
        if action == "rollback-corrupt-module-snapshot":
            (backup / "original-root-native-modules.snapshot/driver-a.ko").write_text("corrupted snapshot\n")
        elif action == "rollback-corrupt-module-digest":
            (backup / "original-root-native-modules.sha256").write_text("0" * 64 + "\n")
        elif action == "rollback-corrupt-boot-snapshot":
            with (backup / "boot_b.img").open("r+b") as stream:
                stream.write(b"X")
        rollback_env = env.copy()
        if action == "rollback-boot-hash-command-failure":
            rollback_env["SHA_FAIL_TARGET"] = "boot_b.img"
        elif action == "rollback-boot-hash-malformed":
            rollback_env["SHA_MALFORMED_TARGET"] = "boot_b.img"
        elif action == "rollback-restored-boot-hash-command-failure":
            rollback_env["SHA_FAIL_TARGET"] = "dev/block/boot_b"
        elif action == "rollback-module-find-failure":
            rollback_env["FIND_FAIL"] = "module-files"
        elif action == "rollback-module-sort-failure":
            rollback_env["SORT_FAIL"] = "1"
        elif action == "rollback-module-file-sha-failure":
            rollback_env["SHA_FAIL_TARGET"] = "driver-a.ko"
        elif action == "rollback-module-file-sha-malformed":
            rollback_env["SHA_MALFORMED_TARGET"] = "driver-a.ko"
        rollback = subprocess.run(["sh", str(backup / "rollback.sh"), str(backup)],
                                  env=rollback_env, text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        result = subprocess.CompletedProcess(rollback.args, rollback.returncode,
                                             result.stdout + "\n" + rollback.stdout)
    return result.returncode, result.stdout, root, test_root, data


def assert_rejected(label: str, **kwargs) -> None:
    code, output, root, test_root, data = run_case(label, **kwargs)
    try:
        assert code != 0, f"{label}: installer unexpectedly succeeded\n{output}"
        assert not Path(data["disk"], "openwrt.native14-new").exists(), f"{label}: staged root left behind"
        assert not Path(data["disk"], ".mu300-native14-rollback").exists(), f"{label}: rollback backup created before preflight"
        print(f"PASS {label}: rejected before device/kernel writes; staged root cleaned ({output.strip().splitlines()[-1]})")
    finally:
        shutil.rmtree(root)


def assert_staging_identity_fail_closed(label: str, helper_fail: str, partial: bool = False) -> None:
    code, output, root, test_root, data = run_case(label, helper_fail=helper_fail)
    try:
        disk = Path(data["disk"])
        old = Path(data["old"])
        staging = disk / "openwrt.native14-new"
        assert code != 0, f"{label}: installer unexpectedly continued after staging identity failure\n{output}"
        assert old.joinpath("etc/mu300/image-version").read_text() == BASE_VERSION + "\n"
        assert module_tree_sha(old / "lib/modules" / KREL) == module_tree_sha_for_marker(root, "same")
        assert (test_root / "dev/boot_b").read_bytes()[:64] == b"O" * 64
        assert not (disk / ".mu300-native14-rollback").exists(), "rollback transaction started before staging identity was secured"
        assert staging.is_dir() and not staging.is_symlink(), "unverifiable staging path should be preserved for inspection"
        if partial:
            assert (staging / ".partial-test").is_dir(), "failed extraction fixture did not leave its sentinel"
        else:
            assert list(staging.iterdir()) == [], "no candidate rootfs writes should precede a valid staging identity"
        assert "Preserving the staging path because its directory identity could not be verified." in output or "cannot record staging directory identity" in output
        print(f"PASS {label}: no boot/root/module transaction; unverified staging path preserved fail-closed")
    finally:
        shutil.rmtree(root)


def assert_same_krel_success_and_rollback(current_module_metadata: bool = False) -> None:
    label = "same-krel-existing-metadata" if current_module_metadata else "same-krel-upgrade"
    code, output, root, test_root, data = run_case(label, action="rollback-after-success",
                                                    current_module_metadata=current_module_metadata)
    try:
        assert code == 0, output
        assert (test_root / "updater-release").read_text().strip() == \
            "local-" + data["candidate_version"], "mu300-update must receive the validated candidate release tag"
        disk = Path(data["disk"])
        old = Path(data["old"])
        assert (old / "etc/mu300/image-version").read_text() == BASE_VERSION + "\n"
        assert module_tree_sha(old / "lib/modules" / KREL) == module_tree_sha_for_marker(
            root, "same", metadata=current_module_metadata)
        assert (test_root / "dev/boot_b").read_bytes()[:64] == b"O" * 64
        backup = next((disk / ".mu300-native14-rollback").iterdir())
        assert (backup / "original-root-native-modules.state").read_text().strip() == "present"
        assert (backup / "transaction.state").read_text().strip() == "rolled-back"
        print("PASS same-KREL new-version install followed by real rollback script: old root, exact original module tree and boot bytes restored")
        print(output.strip().splitlines()[-1])
    finally:
        shutil.rmtree(root)


def module_tree_sha_for_marker(root: Path, marker: str, metadata: bool = False) -> str:
    fixture = root / "expected-modules"
    make_modules(fixture, marker, metadata=metadata)
    return module_tree_sha(fixture / "lib/modules" / KREL)


def assert_same_krel_failed_update_rolls_back() -> None:
    code, output, root, test_root, data = run_case("same-krel-update-failure", fail_update=True)
    try:
        assert code != 0, output
        old = Path(data["old"])
        backup = next((Path(data["disk"]) / ".mu300-native14-rollback").iterdir())
        assert (old / "etc/mu300/image-version").read_text() == BASE_VERSION + "\n"
        assert module_tree_sha(old / "lib/modules" / KREL) == module_tree_sha_for_marker(root, "same")
        assert (test_root / "dev/boot_b").read_bytes()[:64] == b"O" * 64
        assert (backup / "transaction.state").read_text().strip() == "rolled-back"
        print("PASS same-KREL injected updater failure: trap restored original root modules and boot bytes")
    finally:
        shutil.rmtree(root)


def assert_actual_updater_failed_update_rolls_back() -> None:
    code, output, root, test_root, data = run_case(
        "actual-updater-boot-failure-rollback", current_version="u30air-native13-test",
        existing_modules=False, action="actual-updater-failure", actual_updater=True)
    try:
        assert code != 0, f"real updater boot failure was not propagated by installer:\n{output}"
        assert "boot: 31 modules for " + KREL + " installed into: openwrt" in output, output
        assert "boot: boot_b does not hold a boot image header v4, not touching it" in output, output
        assert "ERROR: kernel/boot update failed" in output, output
        old = Path(data["old"])
        disk = Path(data["disk"])
        backup = next((disk / ".mu300-native14-rollback").iterdir())
        assert (old / "etc/mu300/image-version").read_text() == "u30air-native13-test\n"
        assert not (old / "lib/modules" / KREL).exists(), "trap left newly copied native modules in the old root"
        assert (backup / "original-root-native-modules.state").read_text().strip() == "absent"
        saved_modules = backup / "original-root-native-modules"
        assert saved_modules.is_dir() and len(list(saved_modules.glob("*.ko"))) == 31, \
            "trap did not preserve the real updater's copied module tree"
        assert (test_root / "dev/boot_b").read_bytes() == b"O" * (64 * 1024 * 1024)
        assert (backup / "transaction.state").read_text().strip() == "rolled-back"
        assert not (disk / "openwrt.native14-new").exists()
        print("PASS actual packaged updater failure: installer propagated nonzero and trap restored old root, removed the newly added 31-module tree, and restored regular-file boot bytes")
    finally:
        shutil.rmtree(root)


def assert_first_install_rollback() -> None:
    code, output, root, test_root, data = run_case("first-install-rollback", current_version="u30air-native13-test",
                                                    existing_modules=False, action="rollback-after-success")
    try:
        assert code == 0, output
        old = Path(data["old"])
        assert (old / "etc/mu300/image-version").read_text() == "u30air-native13-test\n"
        assert not (old / "lib/modules" / KREL).exists()
        assert (test_root / "dev/boot_b").read_bytes()[:64] == b"O" * 64
        backup = next((Path(data["disk"]) / ".mu300-native14-rollback").iterdir())
        assert (backup / "original-root-native-modules.state").read_text().strip() == "absent"
        print("PASS first-install rollback: preexisting kernel modules remain absent after rollback")
    finally:
        shutil.rmtree(root)


def assert_rollback_preflight_rejects(label: str, action: str) -> None:
    code, output, root, test_root, data = run_case(label, action=action)
    try:
        assert code != 0, f"{label}: rollback unexpectedly succeeded\n{output}"
        disk = Path(data["disk"])
        old = Path(data["old"])
        backup = next((disk / ".mu300-native14-rollback").iterdir())
        assert (old / "etc/mu300/image-version").read_text() == NEXT_VERSION + "\n"
        assert (test_root / "dev/boot_b").read_bytes() == b"changed-test-boot\n"
        assert (backup / "openwrt-before-update").is_dir()
        print(f"PASS {label}: rollback rejected before root or boot writes ({output.strip().splitlines()[-1]})")
    finally:
        shutil.rmtree(root)


def assert_rollback_restored_boot_hash_failure() -> None:
    code, output, root, test_root, data = run_case(
        "rollback-restored-boot-hash-command-failure",
        action="rollback-restored-boot-hash-command-failure")
    try:
        assert code != 0, output
        old = Path(data["old"])
        disk = Path(data["disk"])
        backup = next((disk / ".mu300-native14-rollback").iterdir())
        assert (old / "etc/mu300/image-version").read_text() == BASE_VERSION + "\n"
        assert module_tree_sha(old / "lib/modules" / KREL) == module_tree_sha_for_marker(root, "same")
        assert (test_root / "dev/boot_b").read_bytes()[:64] == b"O" * 64
        assert (backup / "transaction.state").read_text().strip() == "installed"
        calls = (root / "boot-sha-calls.log").read_text().splitlines()
        assert any(line.startswith("FAIL ") and "dev/block/boot_b" in line for line in calls), calls
        print("PASS rollback restored-boot hash-command failure: command failure is detected after restoration; no false success")
    finally:
        shutil.rmtree(root)


def assert_installer_boot_hash_failure(label: str, helper_fail: str, expected_failures: int) -> None:
    code, output, root, test_root, data = run_case(label, helper_fail=helper_fail)
    try:
        assert code != 0, f"{label}: installer unexpectedly succeeded\n{output}"
        old = Path(data["old"])
        disk = Path(data["disk"])
        assert (old / "etc/mu300/image-version").read_text() == BASE_VERSION + "\n"
        assert module_tree_sha(old / "lib/modules" / KREL) == module_tree_sha_for_marker(root, "same")
        assert not (disk / "openwrt.native14-new").exists()
        assert (test_root / "dev/boot_b").read_bytes()[:64] == b"O" * 64
        backup = next((disk / ".mu300-native14-rollback").iterdir())
        assert not (backup / "transaction.state").exists(), "updater ran before boot readback passed"
        calls = (root / "boot-sha-calls.log").read_text().splitlines()
        assert len(calls) == 2, calls
        assert sum(line.startswith("FAIL ") for line in calls) == expected_failures, calls
        print(f"PASS {label}: both boot source and backup hashes attempted; rejected before updater/root swap and staging cleaned")
    finally:
        shutil.rmtree(root)


def main() -> None:
    assert_rejected("same-version", candidate_version=BASE_VERSION)
    assert_rejected("missing-manifest", manifest_mode="missing")
    assert_rejected("corrupt-manifest", manifest_mode="corrupt-after-sums")
    assert_rejected("mismatched-krel-manifest", manifest_mode="mismatch-krel")
    assert_rejected("mismatched-rootfs-version", manifest_mode="mismatch-version")
    assert_rejected("duplicate-version-field", manifest_mode="duplicate-version")
    assert_rejected("nonstring-version-field", manifest_mode="nonstring-version")
    assert_rejected("same-krel-different-modules", candidate_module_marker="changed")
    assert_rejected("same-krel-missing-current-module", candidate_missing_module="driver-b.ko")
    assert_rejected("same-krel-extra-module-file", candidate_extra_module_file="modules.order")
    assert_rejected("same-krel-incomplete-builtin-index-pair", candidate_metadata_mode="one")
    assert_rejected("same-krel-builtin-index-does-not-match-kernel-bundle",
                    candidate_metadata_mode="mismatch")
    assert_rejected("same-krel-builtin-modinfo-does-not-match-kernel-bundle",
                    candidate_metadata_mode="mismatch-modinfo")
    assert_rejected("same-krel-module-symlink", candidate_symlink="unsafe-link")
    assert_rejected("same-krel-existing-builtin-index-changed",
                    current_module_metadata=True, current_metadata_changed=True)
    assert_rejected("module-hash-find-failure", helper_fail="find")
    assert_rejected("module-hash-sort-failure", helper_fail="sort")
    assert_rejected("module-hash-file-sha-failure", helper_fail="module-sha")
    assert_rejected("module-hash-malformed-file-digest", helper_fail="module-sha-malformed")
    assert_staging_identity_fail_closed("staging-inode-ls-failure", "staging-ls-first")
    assert_staging_identity_fail_closed("staging-inode-ls-malformed", "staging-ls-malformed")
    assert_staging_identity_fail_closed("staging-inode-awk-failure", "staging-awk-failure")
    assert_staging_identity_fail_closed("staging-cleanup-inode-ls-failure", "staging-ls-cleanup", partial=True)
    assert_same_krel_success_and_rollback()
    assert_same_krel_success_and_rollback(current_module_metadata=True)
    assert_same_krel_failed_update_rolls_back()
    assert_actual_updater_failed_update_rolls_back()
    assert_first_install_rollback()
    assert_installer_boot_hash_failure("installer-both-boot-hashes-fail", "boot-hashes-both", 2)
    assert_installer_boot_hash_failure("installer-backup-boot-hash-fails", "boot-hash-backup", 1)
    assert_rollback_preflight_rejects("rollback-corrupt-module-snapshot", "rollback-corrupt-module-snapshot")
    assert_rollback_preflight_rejects("rollback-corrupt-module-digest", "rollback-corrupt-module-digest")
    assert_rollback_preflight_rejects("rollback-module-find-failure", "rollback-module-find-failure")
    assert_rollback_preflight_rejects("rollback-module-sort-failure", "rollback-module-sort-failure")
    assert_rollback_preflight_rejects("rollback-module-file-sha-failure", "rollback-module-file-sha-failure")
    assert_rollback_preflight_rejects("rollback-module-file-sha-malformed", "rollback-module-file-sha-malformed")
    assert_rollback_preflight_rejects("rollback-corrupt-boot-snapshot", "rollback-corrupt-boot-snapshot")
    assert_rollback_preflight_rejects("rollback-boot-hash-command-failure", "rollback-boot-hash-command-failure")
    assert_rollback_preflight_rejects("rollback-boot-hash-malformed", "rollback-boot-hash-malformed")
    assert_rollback_restored_boot_hash_failure()
    print("PASS: all installer compatibility scenarios used only temporary regular files; no real boot device was opened")


if __name__ == "__main__":
    main()
