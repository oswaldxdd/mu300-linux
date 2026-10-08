#!/bin/sh
# Settings-preserving native11 -> native12 kernel update, with an independent snapshot.
set -eu
export PATH="$PATH:/opt/mu300/bin:/opt/mu300/busybox-bin:/usr/sbin:/sbin"
PKG=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DISK=/mnt/mu300-disk
KREL=7.2.8-u30air-native12
BACKUP=$DISK/.mu300-native12-kernel-rollback/$(date -u +%Y%m%dT%H%M%SZ)-$$
die() { echo "$*" >&2; exit 1; }
sha() { sha256sum "$1" | cut -d' ' -f1; }
[ "$(id -u)" = 0 ] || die 'root required'
[ "$(mu300-device)" = u30air ] || die 'U30 Air required'
[ "$(uname -r)" = 7.2.8-u30air-native11 ] || die 'verified native11 baseline required'
[ / -ef "$DISK/openwrt" ] || die 'unexpected root filesystem'
[ ! -e "$DISK/openwrt/lib/modules/$KREL" ] || die 'native12 modules already exist'
[ "$(cat "$DISK/boot/kernel")" = 7.2 ] || die 'unexpected kernel selection'
(cd "$PKG" && sha256sum -c KERNEL-SHA256SUMS) || die 'payload checksum mismatch'
[ "$(df -Pk "$DISK" | awk 'END {print $4}')" -ge 60000 ] || die 'need 60000 KiB free for persistent rollback and modules'
[ "$(df -Pk /tmp | awk 'END {print $4}')" -ge 180000 ] || die 'need 180000 KiB free temporary RAM space'
STAGE=/tmp/u30air-native12-update-$$
mkdir -m 700 "$STAGE"
boot_dev=
dtbo_dev=
for u in /sys/class/block/*/uevent; do
    grep -qx PARTNAME=boot_b "$u" && boot_dev=/dev/$(basename "${u%/uevent}")
    grep -qx PARTNAME=dtbo_b "$u" && dtbo_dev=/dev/$(basename "${u%/uevent}")
done
[ -n "$boot_dev" ] && [ "$(cat "/sys/class/block/${boot_dev##*/}/size")" = 131072 ] || die 'unexpected boot_b'
[ -n "$dtbo_dev" ] && [ "$(sha "$dtbo_dev")" = 581020c762acbce7dcc52a540860410f7c93752bec990e2b6b5e1cbb6b418d39 ] || die 'stock DTBO mismatch'
mkdir -p "$BACKUP"
dd if="$boot_dev" bs=1048576 2>/dev/null | gzip -c > "$BACKUP/boot_b.img.gz"
gzip -t "$BACKUP/boot_b.img.gz"
[ "$(gzip -dc "$BACKUP/boot_b.img.gz" | wc -c)" = 67108864 ] || die 'incomplete compressed boot snapshot'
gzip -dc "$BACKUP/boot_b.img.gz" | sha256sum | cut -d' ' -f1 > "$BACKUP/boot_b.sha256"
[ "$(sha "$boot_dev")" = "$(cat "$BACKUP/boot_b.sha256")" ] || die 'backup readback failed'
tar -czf "$BACKUP/boot-metadata.tar.gz" -C "$DISK" boot
cp -p /etc/mu300/image-version "$BACKUP/image-version"
cat > "$BACKUP/rollback.sh" <<'ROLLBACK'
#!/bin/sh
set -eu
B=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$B" in /mnt/mu300-disk/.mu300-native12-kernel-rollback/*) ;; *) exit 1 ;; esac
[ "$(gzip -dc "$B/boot_b.img.gz" | sha256sum | cut -d' ' -f1)" = "$(cat "$B/boot_b.sha256")" ]
dev=
for u in /sys/class/block/*/uevent; do
    grep -qx PARTNAME=boot_b "$u" && dev=/dev/$(basename "${u%/uevent}")
done
[ -n "$dev" ] && [ "$(cat "/sys/class/block/${dev##*/}/size")" = 131072 ]
gzip -dc "$B/boot_b.img.gz" | dd of="$dev" bs=1048576 conv=notrunc 2>/dev/null
sync
[ "$(sha256sum "$dev" | cut -d' ' -f1)" = "$(cat "$B/boot_b.sha256")" ]
tar -xzpf "$B/boot-metadata.tar.gz" -C /mnt/mu300-disk
# Native11 remains installed; ignore any additional native12 modules during rollback.
rm -f /mnt/mu300-disk/boot/installed.id
cp -p "$B/image-version" /etc/mu300/image-version
sync
echo 'Native11 boot restored and verified; reboot to use it.'
ROLLBACK
chmod 700 "$BACKUP/rollback.sh"
echo prepared > "$BACKUP/state"
sync
armed=1
on_exit() {
    rc=$?
    trap - EXIT
    if [ "$armed" = 1 ]; then sh "$BACKUP/rollback.sh" || echo 'RESTORE FAILED: do not reboot.' >&2; fi
    exit "$rc"
}
trap on_exit EXIT
printf 'Rollback snapshot: %s\n' "$BACKUP"
MU300_STAGE="$STAGE" MU300_DISK="$DISK" MU300_KERNEL_BUNDLE="$PKG/mu300-kernel-7.2.8-u30air-native12.tar.gz" \
    MU300_RELEASE=local-u30air-native12-soc-reconcile sh "$PKG/mu300-update" boot
[ "$(find "$DISK/openwrt/lib/modules/$KREL" -name '*.ko' | wc -l)" = 31 ] || die 'matching modules missing'
printf '%s\n' u30air-native12-android-charge-2026.10.05-test > /etc/mu300/image-version
echo installed > "$BACKUP/state"
sync
armed=0
trap - EXIT
printf 'Native12 kernel and matching modules installed; existing userspace and settings retained.\n'
printf 'Rollback: sh %s/rollback.sh\n' "$BACKUP"
