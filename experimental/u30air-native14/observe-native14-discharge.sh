#!/bin/sh
# Read-only measurement. Stopping this process does not stop discharge.
set -eu
PATH=/opt/mu300/bin:/opt/mu300/busybox-bin:/usr/sbin:/usr/bin:/sbin:/bin
echo 'uptime_s,capacity,charge_now_uah,voltage_now_uv,voltage_ocv_uv,current_now_ua,current_avg_ua,temp_decic,charger_online,battery_status'
boot=$(cat /proc/sys/kernel/random/boot_id)
i=0
seen_offline=0
while [ "$i" -lt 4321 ]; do
    [ "$(cat /proc/sys/kernel/random/boot_id)" = "$boot" ] || exit 2
    read -r task_up task_idle < /proc/uptime
    sample=$(awk -F= -v t="$task_up" '
      FILENAME ~ /sc27xx-fgu/ {b[$1]=$2}
      FILENAME ~ /sgm41511-charger/ {c[$1]=$2}
      END {
        split("CAPACITY CHARGE_NOW VOLTAGE_NOW VOLTAGE_OCV CURRENT_NOW CURRENT_AVG TEMP", fields, " ");
        for (j=1;j<=7;j++) if (b["POWER_SUPPLY_" fields[j]] !~ /^-?[0-9]+$/) exit 2;
        if (c["POWER_SUPPLY_ONLINE"] !~ /^[01]$/ || !("POWER_SUPPLY_STATUS" in b)) exit 2;
        printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n",t,b["POWER_SUPPLY_CAPACITY"],b["POWER_SUPPLY_CHARGE_NOW"],b["POWER_SUPPLY_VOLTAGE_NOW"],b["POWER_SUPPLY_VOLTAGE_OCV"],b["POWER_SUPPLY_CURRENT_NOW"],b["POWER_SUPPLY_CURRENT_AVG"],b["POWER_SUPPLY_TEMP"],c["POWER_SUPPLY_ONLINE"],b["POWER_SUPPLY_STATUS"];
      }' /sys/class/power_supply/sc27xx-fgu/uevent /sys/class/power_supply/sgm41511-charger/uevent)
    printf '%s\n' "$sample"
    IFS=, read -r sample_up cap sample_charge voltage sample_ocv sample_current sample_avg temp online sample_status <<ROW
$sample
ROW
    [ "$online" = 0 ] && seen_offline=1
    if [ "$seen_offline" = 1 ] && [ "$online" = 1 ]; then
        echo 'Supply restored; discharge trace completed.' >&2
        break
    fi
    if [ "$cap" -le 10 ] || [ "$temp" -ge 450 ] || [ "$voltage" -le 3400000 ]; then
        echo "Recorded sample boundary reached: cap=$cap temp=$temp voltage=$voltage; this logger does not stop discharge." >&2
        break
    fi
    i=$((i+1))
    [ "$i" -lt 4321 ] && sleep 10 || true
done