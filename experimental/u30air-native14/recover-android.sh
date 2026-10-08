#!/system/bin/sh
# Run under rooted Android A, using its saved Linux-disk offset and snapshot.
set -eu
STAMP=${1:?provide the snapshot directory basename}
OFF=${2:?provide the Linux offset printed by the installer}
T=/data/local/tmp/mu300-native-recovery
MP=/mnt/mu300-native-recovery
die() { echo "ERROR: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die 'Magisk root is required'
case "$(getprop ro.product.device)" in U30Air|U30air|u30air) ;; *) die 'device is not U30 Air' ;; esac
[ "$(getprop ro.boot.slot_suffix)" = _a ] || die 'boot rooted Android slot A first'
case "$STAMP" in ''|*[!0-9TZ-]*) die 'invalid snapshot basename' ;; esac
case "$OFF" in ''|*[!0-9]*) die 'invalid Linux offset' ;; esac
[ "$OFF" != 0 ] || die 'zero offset is not allowed'
[ -x "$T/busybox" ] || die 'recovery busybox not uploaded'
BB=$T/busybox
PATH=$T/bin:/system/bin:/system/xbin
mkdir -p "$T/bin"
for c in awk cat cp cut date dd dirname grep head id losetup mkdir mknod mount mv od readlink sed sha256sum sh sync tar tr umount wc blockdev; do
    ln -sf "$BB" "$T/bin/$c"
done
MU300_OFF=$OFF sh "$T/android-mount-mu300root.sh" "$MP"
cleanup() { sync; sh "$T/android-mount-mu300root.sh" -u "$MP" || true; }
trap cleanup EXIT
BACKUP=$MP/.mu300-native12-rollback/$STAMP
[ -d "$BACKUP" ] && [ "$(cat "$BACKUP/linux-offset")" = "$OFF" ] || die 'snapshot/offset do not match'
# Translate only the fixed mountpoint in the known rollback script, using the uploaded audited copy.
$BB sed "s|DISK=/mnt/mu300-disk|DISK=$MP|" "$T/rollback-device.sh" > "$T/rollback-mounted.sh"
$BB sh "$T/rollback-mounted.sh" "$BACKUP"
echo 'Android recovery completed. Restart Linux using your existing mu300-linux entry.'
