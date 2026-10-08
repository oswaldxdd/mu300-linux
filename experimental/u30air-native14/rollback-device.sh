#!/bin/sh
set -eu
export PATH="$PATH:/opt/mu300/bin:/opt/mu300/busybox-bin:/usr/sbin:/sbin"
DISK=/mnt/mu300-disk
KREL=7.2.8-u30air-native14
BACKUP=${1:?pass the rollback snapshot path printed during installation}
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
is_sha256() {
    case "$1" in ''|*[!0-9a-f]*) return 1 ;; esac
    [ "${#1}" = 64 ]
}
file_sha() {
    hash_line=$(sha256sum "$1") || return 1
    hash=${hash_line%% *}
    is_sha256 "$hash" || return 1
    printf '%s\n' "$hash"
}
module_tree_manifest() {
    root=$1
    manifest_output=$2
    [ -d "$root" ] && [ ! -L "$root" ] || return 1
    module_hash_tmp=$(mktemp -d /tmp/mu300-module-hash.XXXXXX) || return 1
    if ! (CDPATH= cd -- "$root" && find . ! -type f ! -type d -print > "$module_hash_tmp/nonregular"); then
        rm -rf "$module_hash_tmp"; return 1
    fi
    if [ -s "$module_hash_tmp/nonregular" ] || ! (CDPATH= cd -- "$root" && find . -type f -print > "$module_hash_tmp/files"); then
        rm -rf "$module_hash_tmp"; return 1
    fi
    if [ ! -s "$module_hash_tmp/files" ] || ! (CDPATH= cd -- "$root" && while IFS= read -r file; do
        case "$file" in ./*) ;; *) exit 1 ;; esac
        case "$file" in *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./+-]*) exit 1 ;; esac
        file_hash_line=$(sha256sum "$file") || exit 1
        file_hash=${file_hash_line%% *}
        is_sha256 "$file_hash" || exit 1
        [ "$file_hash_line" = "$file_hash  $file" ] || exit 1
        printf '%s\t%s\n' "$file_hash" "$file"
    done < "$module_hash_tmp/files") > "$module_hash_tmp/hashes"; then
        rm -rf "$module_hash_tmp"; return 1
    fi
    if ! LC_ALL=C sort -t "$(printf '\t')" -k2,2 "$module_hash_tmp/hashes" > "$module_hash_tmp/hashes.sorted"; then
        rm -rf "$module_hash_tmp"; return 1
    fi
    [ -z "$manifest_output" ] || cp "$module_hash_tmp/hashes.sorted" "$manifest_output" || { rm -rf "$module_hash_tmp"; return 1; }
    module_hash=$(file_sha "$module_hash_tmp/hashes.sorted") || { rm -rf "$module_hash_tmp"; return 1; }
    rm -rf "$module_hash_tmp"
    printf '%s\n' "$module_hash"
}
module_tree_sha() { module_tree_manifest "$1" ""; }
case "$BACKUP" in "$DISK"/.mu300-native14-rollback/*) ;; *) die 'snapshot is outside the native firmware backup directory' ;; esac
[ -d "$BACKUP" ] && [ ! -L "$BACKUP" ] || die 'snapshot directory missing or is a symlink'
[ "$(readlink -f "$BACKUP")" = "$BACKUP" ] || die 'snapshot path is not canonical'
[ "$(id -u)" = 0 ] || die 'run as root'
[ -s "$BACKUP/boot_b.img" ] && [ -f "$BACKUP/transaction.state" ] || die 'incomplete rollback snapshot'
expected_boot_sha=$(cat "$BACKUP/boot_b.sha256")
is_sha256 "$expected_boot_sha" || die 'boot backup checksum is malformed'
actual_boot_sha=$(file_sha "$BACKUP/boot_b.img") || die 'cannot read boot backup checksum'
[ "$actual_boot_sha" = "$expected_boot_sha" ] || die 'boot backup checksum failed'
modules_state=absent
if [ -f "$BACKUP/original-root-native-modules.state" ]; then
    modules_state=$(cat "$BACKUP/original-root-native-modules.state")
fi
case "$modules_state" in
    present)
        [ -d "$BACKUP/original-root-native-modules.snapshot" ] && [ ! -L "$BACKUP/original-root-native-modules.snapshot" ] || die 'original native module snapshot is missing or unsafe'
        [ -s "$BACKUP/original-root-native-modules.sha256" ] || die 'original native module snapshot checksum is missing'
        expected_modules_sha=$(cat "$BACKUP/original-root-native-modules.sha256")
        case "$expected_modules_sha" in *[!0-9a-f]*|'') die 'original native module snapshot checksum is malformed' ;; esac
        [ "${#expected_modules_sha}" = 64 ] || die 'original native module snapshot checksum has the wrong length'
        actual_modules_sha=$(module_tree_sha "$BACKUP/original-root-native-modules.snapshot") || die 'original native module snapshot contains unsupported file types'
        [ "$actual_modules_sha" = "$expected_modules_sha" ] || die 'original native module snapshot checksum failed'
        ;;
    absent) ;;
    *) die 'unknown original native module snapshot state' ;;
esac
boot_dev=
for u in /sys/class/block/*/uevent; do
    [ -r "$u" ] && grep -qx 'PARTNAME=boot_b' "$u" || continue
    d=${u%/uevent}; boot_dev=/dev/${d##*/}
    [ -b "$boot_dev" ] || boot_dev=/dev/block/${d##*/}
    break
done
[ -n "$boot_dev" ] || die 'boot_b partition missing'
[ "$(cat "/sys/class/block/${boot_dev##*/}/size")" = 131072 ] || die 'unexpected boot_b partition size'
module_restore_stage=
if [ "$modules_state" = present ]; then
    module_restore_stage=$BACKUP/module-restore-staging-$$
    [ ! -e "$module_restore_stage" ] || die 'module restore staging path already exists'
    mkdir "$module_restore_stage"
    cp -a "$BACKUP/original-root-native-modules.snapshot" "$module_restore_stage/$KREL"
    staged_modules_sha=$(module_tree_sha "$module_restore_stage/$KREL") || die 'cannot stage original native module snapshot'
    [ "$staged_modules_sha" = "$expected_modules_sha" ] || die 'staged original native module snapshot checksum failed'
fi
state=$(cat "$BACKUP/transaction.state")
if [ -d "$BACKUP/openwrt-before-update" ]; then
    failed=$DISK/openwrt.native14-failed-${BACKUP##*/}
    [ ! -e "$failed" ] || die 'a failed-system recovery directory already exists'
    [ ! -e "$DISK/openwrt" ] || mv "$DISK/openwrt" "$failed"
    mv "$BACKUP/openwrt-before-update" "$DISK/openwrt"
elif [ "$state" = installed ] || [ "$state" = old-root-moved ]; then
    die 'the saved original OpenWrt system is missing'
fi
if [ -d "$DISK/openwrt.native14-new" ]; then
    [ ! -e "$BACKUP/openwrt-new-unused" ] || die 'an unused staging backup already exists'
    mv "$DISK/openwrt.native14-new" "$BACKUP/openwrt-new-unused"
fi
if [ -d "$DISK/openwrt/lib/modules/$KREL" ]; then
    [ ! -e "$BACKUP/original-root-native-modules" ] || die 'native module backup already exists'
    mv "$DISK/openwrt/lib/modules/$KREL" "$BACKUP/original-root-native-modules"
fi
if [ "$modules_state" = present ]; then
    module_root=$DISK/openwrt/lib/modules
    module_target=$module_root/$KREL
    [ -d "$DISK/openwrt" ] && [ ! -e "$module_target" ] || die 'restored system has an unexpected native module directory'
    mkdir -p "$module_root"
    mv "$module_restore_stage/$KREL" "$module_target"
    restored_modules_sha=$(module_tree_sha "$module_target") || die 'restored native module directory is invalid'
    [ "$restored_modules_sha" = "$expected_modules_sha" ] || die 'restored native module directory checksum failed'
    rmdir "$module_restore_stage"
fi
dd if="$BACKUP/boot_b.img" of="$boot_dev" bs=1048576 conv=notrunc 2>/dev/null
sync
restored_boot_sha=$(file_sha "$boot_dev") || die 'cannot read restored boot_b checksum'
[ "$restored_boot_sha" = "$expected_boot_sha" ] || die 'boot_b restore readback failed'
if [ "$(cat "$BACKUP/boot-metadata.state")" = present ]; then
    [ ! -d "$DISK/boot" ] || mv "$DISK/boot" "$BACKUP/boot-metadata-after-test-$(date +%s)"
    tar -xzpf "$BACKUP/boot-metadata.tar.gz" -C "$DISK"
elif [ -d "$DISK/boot" ]; then
    mv "$DISK/boot" "$BACKUP/boot-metadata-after-test-$(date +%s)"
fi
if [ -d "$DISK/ubuntu" ]; then
    if [ "$(cat "$BACKUP/ubuntu-modules.state")" = present ]; then
        tar -xzpf "$BACKUP/ubuntu-modules.tar.gz" -C "$DISK/ubuntu/lib/modules"
    elif [ -d "$DISK/ubuntu/lib/modules/$KREL" ]; then
        mv "$DISK/ubuntu/lib/modules/$KREL" "$BACKUP/ubuntu-native-modules-after-test"
    fi
fi
echo rolled-back > "$BACKUP/transaction.state"
sync
printf '\nOriginal OpenWrt system and boot_b restored. Reboot to use them.\n'
