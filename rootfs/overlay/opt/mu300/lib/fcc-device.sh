#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Resolve the active U30 Air eMMC and DTBO from the live system, fail-closed.

fcc_find_emmc() {
    root=$1
    count=0
    found=
    for disk in "$root"/sys/class/block/mmcblk[0-9]*; do
        [ -r "$disk/device/type" ] || continue
        [ ! -e "$disk/partition" ] || continue
        [ "$(cat "$disk/device/type" 2>/dev/null)" = MMC ] || continue
        [ "$(cat "$disk/removable" 2>/dev/null)" = 0 ] || continue
        [ -r "$disk/device/cid" ] || continue
        count=$((count + 1))
        found=$disk
    done
    [ "$count" -eq 1 ] || return 1
    printf '%s\n' "$found"
}

fcc_active_slot() {
    root=$1
    published=
    from_args=
    if [ -r "$root/run/mu300/linux-slot" ]; then
        published=$(cat "$root/run/mu300/linux-slot")
        case $published in a|b) ;; *) return 1 ;; esac
    fi
    if [ -r "$root/proc/cmdline" ]; then
        for arg in $(cat "$root/proc/cmdline"); do
            case $arg in
                androidboot.slot_suffix=_a|androidboot.slot=a|androidboot.slot_suffix=a) slot=a ;;
                androidboot.slot_suffix=_b|androidboot.slot=b|androidboot.slot_suffix=b) slot=b ;;
                *) continue ;;
            esac
            if [ -n "$from_args" ] && [ "$from_args" != "$slot" ]; then
                return 1
            fi
            from_args=$slot
        done
    fi
    [ -z "$published" ] || [ -z "$from_args" ] || [ "$published" = "$from_args" ] || return 1
    slot=${published:-$from_args}
    case $slot in a|b) printf '%s\n' "$slot" ;; *) return 1 ;; esac
}

fcc_find_dtbo() {
    root=$1
    disk=$2
    slot=$3
    case $slot in a|b) ;; *) return 1 ;; esac
    count=0
    found=
    for event in "$disk"p*/uevent; do
        [ -r "$event" ] || continue
        grep -qx "PARTNAME=dtbo_$slot" "$event" || continue
        part=${event%/uevent}
        count=$((count + 1))
        found=/dev/${part##*/}
    done
    [ "$count" -eq 1 ] || return 1
    printf '%s\n' "$found"
}

fcc_validate_store_mount() {
    root=$1
    mountpoint=$2
    emmc=${3##*/}
    mounts=$root/proc/mounts
    [ -r "$mounts" ] || return 1
    set -- $(awk -v m="$mountpoint" '$2 == m { print $1 }' "$mounts")
    [ "$#" -eq 1 ] || return 1
    source=$1
    published_disk=$(cat "$root/run/mu300/disk-dev" 2>/dev/null)
    # boot/init publishes the backing root device at /run/mu300-root-dev;
    # the selected root mount source is separately recorded under /run/mu300.
    root_device=$(cat "$root/run/mu300-root-dev" 2>/dev/null)
    [ -n "$published_disk" ] && [ "$source" = "$published_disk" ] || return 1
    case $root_device in
        mmcblk[0-9]*@*)
            # The boot initramfs reports the backing eMMC and offset for its loop mount.
            root_disk=${root_device%@*}
            [ "$root_disk" = "$emmc" ] && case $source in /dev/loop[0-9]*) return 0 ;; esac
            return 1
            ;;
        /dev/mmcblk[0-9]*p[0-9]*)
            root_part=${root_device##*/}
            root_disk=${root_part%p*}
            [ -e "$root/sys/class/block/$root_part" ] || return 1
            [ "$source" = "$root_device" ] || return 1
            ;;
        *) return 1 ;;
    esac
}
