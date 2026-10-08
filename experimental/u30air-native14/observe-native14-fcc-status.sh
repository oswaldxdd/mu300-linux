#!/bin/sh
set -eu
PATH=/opt/mu300/bin:/opt/mu300/busybox-bin:/usr/sbin:/usr/bin:/sbin:/bin
pid=$1
boot=$(cat /proc/sys/kernel/random/boot_id)
while [ -r "/proc/$pid/stat" ]; do
    state=$(awk '{print $3}' "/proc/$pid/stat")
    [ "$state" != Z ] || break
    [ "$(cat /proc/sys/kernel/random/boot_id)" = "$boot" ] || exit 1
    read -r up idle < /proc/uptime
    printf '%s ' "$up"
    cat /sys/bus/platform/devices/64400000.spi:pmic@0:fgu@800/fcc_status
    sleep 60
done
