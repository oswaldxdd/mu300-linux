#!/bin/sh
# One-time installer only. USB/charging operation after boot is native code.
set -eu
export PATH="$PATH:/opt/mu300/bin:/opt/mu300/busybox-bin:/usr/sbin:/sbin"
PKG=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DISK=/mnt/mu300-disk
BIN=/opt/mu300/bin
KREL=7.2.8-u30air-native14
STAMP=$(date -u +%Y%m%dT%H%M%SZ)-$$
BACKUP=$DISK/.mu300-native14-rollback/$STAMP
NEW=$DISK/openwrt.native14-new
OLD=$DISK/openwrt
ARMED=0
STAGING=0
STAGING_INODE=

die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
is_sha256() {
    case "$1" in ''|*[!0-9a-f]*) return 1 ;; esac
    [ "${#1}" = 64 ]
}
sha_file() {
    hash_line=$(sha256sum "$1") || return 1
    hash=${hash_line%% *}
    is_sha256 "$hash" || return 1
    printf '%s\n' "$hash"
}
sha_equal_files() {
    sha_equal_left_rc=0
    sha_equal_right_rc=0
    sha_equal_left=$(sha_file "$1") || sha_equal_left_rc=$?
    sha_equal_right=$(sha_file "$2") || sha_equal_right_rc=$?
    [ "$sha_equal_left_rc" = 0 ] && [ "$sha_equal_right_rc" = 0 ] &&
        [ "$sha_equal_left" = "$sha_equal_right" ]
}
directory_inode() {
    [ "$#" = 1 ] || return 1
    inode_listing=$(LC_ALL=C ls -id "$1") || return 1
    inode_value=$(printf '%s\n' "$inode_listing" | awk -v expected="$1" '
        NR != 1 { bad=1; next }
        {
            if (NF != 2 || $1 !~ /^[1-9][0-9]*$/ || $2 != expected) bad=1
            else value=$1
        }
        END {
            if (NR != 1 || bad || value == "") exit 1
            print value
        }
    ') || return 1
    case "$inode_value" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$inode_value"
}
cleanup_staging() {
    [ "$STAGING" = 1 ] && [ -n "$STAGING_INODE" ] || return 1
    [ -d "$NEW" ] && [ ! -L "$NEW" ] || return 1
    cleanup_inode=$(directory_inode "$NEW") || return 1
    [ "$cleanup_inode" = "$STAGING_INODE" ] || return 1
    rm -rf "$NEW"
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
    module_hash=$(sha_file "$module_hash_tmp/hashes.sorted") || { rm -rf "$module_hash_tmp"; return 1; }
    rm -rf "$module_hash_tmp"
    printf '%s\n' "$module_hash"
}
module_tree_sha() { module_tree_manifest "$1" ""; }
same_krel_modules_compatible() {
    current_root=$1
    candidate_root=$2
    kernel_bundle=$3
    compare_tmp=$(mktemp -d /tmp/mu300-module-compare.XXXXXX) || return 1
    if ! module_tree_manifest "$current_root" "$compare_tmp/current.manifest" > /dev/null ||
       ! module_tree_manifest "$candidate_root" "$compare_tmp/candidate.manifest" > /dev/null; then
        rm -rf "$compare_tmp"; return 1
    fi
    if ! awk -F '\t' '
        NR == FNR {
            if (NF != 2 || $1 !~ /^[0-9a-f]+$/ || length($1) != 64 || ($2 in old)) bad=1
            old[$2]=$1
            next
        }
        {
            if (NF != 2 || $1 !~ /^[0-9a-f]+$/ || length($1) != 64 || ($2 in candidate)) bad=1
            candidate[$2]=$1
        }
        END {
            if (bad) exit 2
            for (p in old) {
                if (!(p in candidate) || old[p] != candidate[p]) bad=1
            }
            for (p in candidate) {
                if (p in old) continue
                if (p != "./modules.builtin" && p != "./modules.builtin.modinfo") bad=1
                else extra++
            }
            if (extra != 0 && extra != 2) bad=1
            exit bad
        }
    ' "$compare_tmp/current.manifest" "$compare_tmp/candidate.manifest"; then
        rm -rf "$compare_tmp"; return 1
    fi
    bundle_listing=$compare_tmp/kernel-bundle.list
    if ! tar -tzf "$kernel_bundle" > "$bundle_listing"; then
        rm -rf "$compare_tmp"; return 1
    fi
    [ "$(grep -Fxc './modules.builtin' "$bundle_listing")" = 1 ] &&
    [ "$(grep -Fxc './modules.builtin.modinfo' "$bundle_listing")" = 1 ] || {
        rm -rf "$compare_tmp"; return 1;
    }
    if ! tar -xzf "$kernel_bundle" -C "$compare_tmp" ./modules.builtin ./modules.builtin.modinfo; then
        rm -rf "$compare_tmp"; return 1
    fi
    for metadata in modules.builtin modules.builtin.modinfo; do
        [ -f "$compare_tmp/$metadata" ] && [ ! -L "$compare_tmp/$metadata" ] &&
        [ -f "$candidate_root/$metadata" ] && [ ! -L "$candidate_root/$metadata" ] &&
        sha_equal_files "$compare_tmp/$metadata" "$candidate_root/$metadata" || {
            rm -rf "$compare_tmp"; return 1;
        }
    done
    rm -rf "$compare_tmp"
    return 0
}
manifest_field() {
    key=$1
    count=$(awk -v key="$key" '
        {
            line=$0
            sub(/^[[:space:]]*/, "", line)
            prefix="\"" key "\""
            if (index(line, prefix) != 1) next
            rest=substr(line, length(prefix)+1)
            if (rest ~ /^[[:space:]]*:/) n++
        }
        END { print n+0 }
    ' "$PKG/BUILD-MANIFEST.json")
    [ "$count" = 1 ] || die "candidate manifest must contain exactly one top-level $key field"
    jsonfilter -i "$PKG/BUILD-MANIFEST.json" -e "@.$key" 2>/dev/null || die "cannot parse candidate manifest $key"
}
part_dev() {
    for u in /sys/class/block/*/uevent; do
        [ -r "$u" ] || continue
        grep -qx "PARTNAME=$1" "$u" || continue
        d=${u%/uevent}; printf '/dev/%s\n' "${d##*/}"; return 0
    done
    return 1
}
part_bytes() { sectors=$(cat "/sys/class/block/${1##*/}/size"); echo $((sectors * 512)); }
on_exit() {
    rc=$1
    trap - EXIT INT TERM HUP
    if [ "$ARMED" = 1 ]; then
        printf '\nInstallation stopped; restoring the saved system and boot image...\n' >&2
        sh "$BACKUP/rollback.sh" "$BACKUP" || printf 'RESTORE FAILED: keep the backup and use README recovery instructions.\n' >&2
    elif [ "$STAGING" = 1 ]; then
        cleanup_staging || printf 'Preserving the staging path because its directory identity could not be verified.\n' >&2
    fi
    exit "$rc"
}
trap 'on_exit $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

[ "$(id -u)" = 0 ] || die 'run as root'
[ -x "$BIN/mu300-device" ] && [ "$("$BIN/mu300-device")" = u30air ] || die 'this package is for U30 Air only'
case "$(uname -r)" in 7.2.*|7.2) ;; *) die 'requires an existing 7.2-series baseline' ;; esac
[ -f /etc/openwrt_release ] || die 'boot the existing OpenWrt system before installing'
[ -d "$OLD" ] && [ / -ef "$OLD" ] || die 'the running root is not /mnt/mu300-disk/openwrt; stop and reboot to the current OpenWrt'
[ ! -L "$OLD" ] && [ "$(readlink -f "$DISK")" = "$DISK" ] || die 'noncanonical Linux root path'
[ ! -e "$NEW" ] || die 'an earlier staging directory exists; inspect it before retrying'
for c in awk cat cp cut date dd df dirname du find grep head id jsonfilter ls mkdir mktemp mv readlink rm sed sha256sum sh sort sync tar tr uci; do
    command -v "$c" >/dev/null 2>&1 || die "required command missing: $c"
done
(cd "$PKG" && sha256sum -c SHA256SUMS) || die 'package checksum verification failed'
for f in BUILD-MANIFEST.json mu300-openwrt-rootfs.tar.gz mu300-kernel-7.2.8-u30air-native14.tar.gz; do
    [ -s "$PKG/$f" ] || die "required candidate package file missing: $f"
done
candidate_version=$(manifest_field version)
manifest_krel=$(manifest_field kernel_release)
manifest_device=$(manifest_field device)
case "$candidate_version" in u30air-native14-*) ;; *) die 'candidate manifest version is not a native14 firmware version' ;; esac
case "$candidate_version" in ''|*[!A-Za-z0-9._-]*) die 'candidate manifest version contains unsupported characters' ;; esac
[ "$manifest_krel" = "$KREL" ] || die 'candidate manifest kernel_release does not match this installer'
[ "$manifest_device" = u30air ] || die 'candidate manifest device is not U30 Air'
rootfs_version=$(tar -xOzf "$PKG/mu300-openwrt-rootfs.tar.gz" ./etc/mu300/image-version 2>/dev/null | tr -d '\r\n') || die 'cannot read candidate rootfs image-version'
[ "$rootfs_version" = "$candidate_version" ] || die 'candidate manifest version does not match the checksummed rootfs image-version'
current_version=unknown
if [ -e "$OLD/etc/mu300/image-version" ] || [ -L "$OLD/etc/mu300/image-version" ]; then
    [ -f "$OLD/etc/mu300/image-version" ] && [ ! -L "$OLD/etc/mu300/image-version" ] || die 'current image-version is not a regular file'
    current_version=$(cat "$OLD/etc/mu300/image-version")
    [ -n "$current_version" ] || die 'current image-version is empty'
fi
[ "$current_version" != "$candidate_version" ] || die 'this exact firmware version is already installed; use rollback before reinstalling'
same_krel=0
if [ -e "$OLD/lib/modules/$KREL" ] || [ -L "$OLD/lib/modules/$KREL" ]; then
    [ "$(uname -r)" = "$KREL" ] || die 'native14 module directory exists but is not the running kernel; resolve the stale same-KREL state first'
    [ -d "$OLD/lib/modules/$KREL" ] && [ ! -L "$OLD/lib/modules/$KREL" ] || die 'existing native14 module path is not a regular directory'
    same_krel=1
fi
[ -z "$(uci -q changes network)" ] || die 'network has uncommitted changes; save them first'
[ "$(uci -q get network.lan.device)" = br-lan ] || die 'LAN must use br-lan for this tested NIC configuration'
bridge=$(uci -q show network | sed -n "s/^network\.\([^=]*\)\.name='br-lan'$/\1/p" | head -1)
[ -n "$bridge" ] && [ "$(uci -q get "network.$bridge.type")" = bridge ] || die 'br-lan bridge definition not found'
# These U30 Air Android/WCN resources are intentionally excluded from the public image.
# Check them before staging anything: without them the modem and WCN cannot start.
for k in apex dev-properties linkerconfig system vendor; do
    [ -d "$OLD/opt/mu300/android/$k" ] || die "required U30 Air Android resource missing: opt/mu300/android/$k"
done
[ -x "$OLD/opt/mu300/android/apex/com.android.runtime/bin/linker64" ] || die 'Android runtime linker is missing or not executable'
[ -x "$OLD/opt/mu300/android/vendor/bin/modem_control" ] || die 'U30 Air modem_control is missing or not executable'
[ -e "$OLD/opt/mu300/android/linkerconfig/ld.config.txt" ] || die 'Android linker configuration is missing'
for f in gnssmodem.bin wcnmodem.bin wifi_board_config.ini wifi_board_config_ab.ini bt_configure_pskey.ini bt_configure_rf.ini; do
    [ -s "$OLD/lib/firmware/$f" ] || die "required U30 Air firmware is missing or empty: lib/firmware/$f"
done
boot_dev=$(part_dev boot_b) || die 'boot_b GPT partition not found'
dtbo_dev=$(part_dev dtbo_b) || die 'dtbo_b GPT partition not found'
[ "$(part_bytes "$boot_dev")" = 67108864 ] || die 'unexpected boot_b partition size'
[ "$(part_bytes "$dtbo_dev")" = 8388608 ] || die 'unexpected dtbo_b partition size'
# Retain the stock vendor DTB/DTBO. A prior fixed-Host experiment must be rolled back first.
[ "$(sha_file "$dtbo_dev")" = 581020c762acbce7dcc52a540860410f7c93752bec990e2b6b5e1cbb6b418d39 ] || die 'DTBO differs from the captured stock U30 Air baseline; restore the prior DT experiment before installing'
avail_kb=$(df -Pk "$DISK" | awk 'END {print $4}')
old_kb=0
# Count only static Android paths; runtime dev/proc/sys mounts must not be copied.
for k in opt/mu300/android/apex opt/mu300/android/dev-properties opt/mu300/android/linkerconfig \
         opt/mu300/android/odm opt/mu300/android/product opt/mu300/android/system \
         opt/mu300/android/system_ext opt/mu300/android/vendor opt/mu300/android/vendor_dlkm \
         opt/mu300/android/data lib/firmware lib/modules root etc usr/local; do
    [ -d "$OLD/$k" ] || continue
    amount=$(du -sk "$OLD/$k" | awk '{print $1}')
    old_kb=$((old_kb + amount))
done
case "$avail_kb:$old_kb" in *[!0-9:]*|:*) die 'cannot determine system size/free space' ;; esac
need_kb=$((old_kb + 524288))
[ "$avail_kb" -ge "$need_kb" ] || die "need ${need_kb} KiB free for staging and recovery; have ${avail_kb} KiB"
disk_source=$(awk -v mp="$DISK" '$2 == mp {print $1; exit}' /proc/mounts)
offset_path=/sys/class/block/${disk_source##*/}/loop/offset
[ -r "$offset_path" ] || die 'cannot identify the Linux loop-device offset for Android recovery'
linux_offset=$(cat "$offset_path")
case "$linux_offset" in ''|*[!0-9]*) die 'invalid Linux loop-device offset' ;; esac
[ "$linux_offset" != 0 ] || die 'Linux disk offset is zero; refusing an unrecognized layout'

printf '\nPreparing the complete OpenWrt system without changing the running system...\n'
mkdir "$NEW"
STAGING=1
STAGING_INODE=$(directory_inode "$NEW") || die 'cannot record staging directory identity'
tar -xzpf "$PKG/mu300-openwrt-rootfs.tar.gz" -C "$NEW"
[ -x "$NEW/sbin/init" ] && [ -x "$NEW/opt/mu300/bin/mu300-usb" ] || die 'new root filesystem is incomplete'
[ "$(cat "$NEW/etc/mu300/image-version")" = "$candidate_version" ] || die 'extracted rootfs image-version changed after package validation'
if [ "$same_krel" = 1 ]; then
    same_krel_modules_compatible "$OLD/lib/modules/$KREL" "$NEW/lib/modules/$KREL" "$PKG/mu300-kernel-7.2.8-u30air-native14.tar.gz" ||
        die 'same-KREL modules differ beyond the kernel-bundle-verified built-in index pair'
fi
for k in etc/config etc/mu300 etc/dropbear etc/rc.local root usr/local; do
    [ -e "$OLD/$k" ] || continue
    mkdir -p "$NEW/$(dirname "$k")"
    rm -rf "$NEW/$k"
    cp -a "$OLD/$k" "$NEW/$k"
done
for k in etc/passwd etc/shadow etc/group; do cp -p "$OLD/$k" "$NEW/$k"; done
printf '%s\n' "$candidate_version" > "$NEW/etc/mu300/image-version"
# Copy only static Android trees. The public rootfs contains placeholders; overwrite
# them with the device's known-good runtime instead of silently keeping those stubs.
for k in apex dev-properties linkerconfig odm product system system_ext vendor vendor_dlkm data; do
    [ -d "$OLD/opt/mu300/android/$k" ] || continue
    mkdir -p "$NEW/opt/mu300/android/$k"
    cp -a "$OLD/opt/mu300/android/$k/." "$NEW/opt/mu300/android/$k/"
done
# Retain any device-specific firmware while keeping this build's tested RTL8153B blob.
rtl_fw=$NEW/lib/firmware/rtl_nic/rtl8153b-2.fw
[ -s "$rtl_fw" ] || die 'packaged RTL8153B firmware is missing'
rtl_fw_saved=$NEW/.u30-native14-rtl8153b-2.fw
cp -p "$rtl_fw" "$rtl_fw_saved"
cp -a "$OLD/lib/firmware/." "$NEW/lib/firmware/"
cp -p "$rtl_fw_saved" "$rtl_fw"
sha_equal_files "$rtl_fw_saved" "$rtl_fw" || die 'RTL8153B firmware preservation verification failed'
rm -f "$rtl_fw_saved"
for f in gnssmodem.bin wcnmodem.bin wifi_board_config.ini wifi_board_config_ab.ini bt_configure_pskey.ini bt_configure_rf.ini; do
    cp -p "$OLD/lib/firmware/$f" "$NEW/lib/firmware/$f"
    sha_equal_files "$OLD/lib/firmware/$f" "$NEW/lib/firmware/$f" || die "U30 Air firmware copy verification failed: $f"
done
for p in \
    opt/mu300/android/apex/com.android.runtime/bin/linker64 \
    opt/mu300/android/vendor/bin/modem_control \
    opt/mu300/android/linkerconfig/ld.config.txt \
    opt/mu300/android/dev-properties; do
    [ -e "$NEW/$p" ] || die "U30 Air Android resource copy verification failed: $p"
done
[ -x "$NEW/opt/mu300/android/apex/com.android.runtime/bin/linker64" ] || die 'copied Android runtime linker is not executable'
[ -x "$NEW/opt/mu300/android/vendor/bin/modem_control" ] || die 'copied U30 Air modem_control is not executable'
for k in lib/modules; do
    [ -d "$OLD/$k" ] || continue
    mkdir -p "$NEW/$k"
    cp -an "$OLD/$k/." "$NEW/$k/"
done
# Keep optional service selections without carrying the obsolete automatic Host daemon over.
for l in "$OLD"/etc/rc.d/*; do
    [ -L "$l" ] || continue
    case ${l##*/} in *mu300-usb-auto*) continue ;; esac
    [ -L "$NEW/etc/rc.d/${l##*/}" ] || cp -a "$l" "$NEW/etc/rc.d/"
done
rm -f "$NEW/etc/rc.d/"*mu300-usb-auto* "$NEW/etc/init.d/mu300-usb-auto" "$NEW/usr/local/sbin/mu300-usb-auto"
# RTL8153 interfaces are attached by hotplug and netifd, using USB IDs.
# First-install defaults would otherwise reset the copied LAN/APN and remove eth0.
rm -f "$NEW/etc/uci-defaults/90-mu300"
# Older installations detect the board directly and have no cached device file.
if [ -f "$OLD/etc/mu300-device" ]; then
    cp -p "$OLD/etc/mu300-device" "$NEW/etc/mu300-device"
fi
sync

mkdir -p "$BACKUP"
printf '%s\n' "$linux_offset" > "$BACKUP/linux-offset"
if [ "$same_krel" = 1 ]; then
    cp -a "$OLD/lib/modules/$KREL" "$BACKUP/original-root-native-modules.snapshot"
    original_modules_sha=$(module_tree_sha "$OLD/lib/modules/$KREL") || die 'cannot verify the current native14 module tree'
    snapshot_modules_sha=$(module_tree_sha "$BACKUP/original-root-native-modules.snapshot") || die 'cannot verify the rollback native14 module snapshot'
    [ "$snapshot_modules_sha" = "$original_modules_sha" ] || die 'native14 module snapshot differs from the installed tree'
    printf '%s\n' "$original_modules_sha" > "$BACKUP/original-root-native-modules.sha256"
    printf '%s\n' present > "$BACKUP/original-root-native-modules.state"
else
    printf '%s\n' absent > "$BACKUP/original-root-native-modules.state"
fi
dd if="$boot_dev" of="$BACKUP/boot_b.img" bs=1048576 2>/dev/null
sha_equal_files "$boot_dev" "$BACKUP/boot_b.img" || die 'boot_b backup readback verification failed'
boot_backup_sha=$(sha_file "$BACKUP/boot_b.img") || die 'cannot calculate boot_b backup checksum'
printf '%s\n' "$boot_backup_sha" > "$BACKUP/boot_b.sha256"
if [ -d "$DISK/boot" ]; then
    tar -czf "$BACKUP/boot-metadata.tar.gz" -C "$DISK" boot
    echo present > "$BACKUP/boot-metadata.state"
else
    echo absent > "$BACKUP/boot-metadata.state"
fi
if [ -d "$DISK/ubuntu/lib/modules/$KREL" ]; then
    tar -czf "$BACKUP/ubuntu-modules.tar.gz" -C "$DISK/ubuntu/lib/modules" "$KREL"
    echo present > "$BACKUP/ubuntu-modules.state"
else
    echo absent > "$BACKUP/ubuntu-modules.state"
fi
cp "$PKG/rollback-device.sh" "$BACKUP/rollback.sh"
echo prepared > "$BACKUP/transaction.state"
sync
ARMED=1
printf '\nRollback snapshot: %s\n' "$BACKUP"
printf 'Android recovery Linux offset: %s\n' "$linux_offset"
mkdir -p "$DISK/boot"
printf '%s\n' 7.2 > "$DISK/boot/kernel"
MU300_DISK="$DISK" MU300_BIN="$BIN" \
MU300_KERNEL_BUNDLE="$PKG/mu300-kernel-7.2.8-u30air-native14.tar.gz" \
MU300_RELEASE="local-$candidate_version" \
    sh "$PKG/mu300-update" boot || die 'kernel/boot update failed'
echo kernel-updated > "$BACKUP/transaction.state"
sync
mv "$OLD" "$BACKUP/openwrt-before-update"
echo old-root-moved > "$BACKUP/transaction.state"
mv "$NEW" "$OLD"
STAGING=0
echo installed > "$BACKUP/transaction.state"
sync
ARMED=0
trap - EXIT INT TERM HUP
printf '\nComplete firmware installed. No reboot or boot-slot change was performed.\n'
printf 'Rollback: sh %s/rollback.sh %s\n' "$BACKUP" "$BACKUP"
printf 'Reboot once, verify native14, then connect charger + splitter + NIC. Auto policy waits 60 seconds after boot.\n'
