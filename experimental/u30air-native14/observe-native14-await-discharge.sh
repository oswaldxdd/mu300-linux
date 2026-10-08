#!/bin/sh
# Observe actual supply removal; never changes charge/USB/power policy.
set -eu
PATH=/opt/mu300/bin:/opt/mu300/busybox-bin:/usr/sbin:/usr/bin:/sbin:/bin
out=$1
boot=$(cat /proc/sys/kernel/random/boot_id)
[ ! -e "$out.csv" ] || exit 1
i=0
while [ "$i" -lt 4320 ]; do
    [ "$(cat /proc/sys/kernel/random/boot_id)" = "$boot" ] || exit 2
    online=$(cat /sys/class/power_supply/sgm41511-charger/online)
    read -r up idle < /proc/uptime
    printf '%s,%s\n' "$up" "$online"
    case "$online" in
        0)
            [ ! -e "$out.csv" ] || exit 1
            sh -c 'trap "" HUP; exec sh /tmp/observe-native14-discharge.sh' > "$out.csv" 2> "$out.err" < /dev/null &
            pid=$!
            echo "$pid" > "$out.pid"
            sh -c 'trap "" HUP; exec sh /tmp/observe-native14-fcc.sh "$1"' sh "$pid" > "$out.fcc.log" 2> "$out.fcc.err" < /dev/null &
            echo $! > "$out.fcc.pid"
            echo "Actual external input offline; discharge observer PID=$pid" >&2
            exit 0;;
        1) ;;
        *) exit 3;;
    esac
    i=$((i+1))
    sleep 10
done
echo 'No supply removal detected in 12 hours; no discharge observer started.' >&2
