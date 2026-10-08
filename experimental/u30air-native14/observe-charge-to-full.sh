#!/bin/sh
# Read-only observation; exiting does not change charging or power state.
set -eu
PATH=/opt/mu300/bin:/opt/mu300/busybox-bin:/usr/sbin:/usr/bin:/sbin:/bin
boot=$(cat /proc/sys/kernel/random/boot_id)
echo 'uptime_s,capacity,charge_now_uah,voltage_now_uv,voltage_ocv_uv,current_now_ua,current_avg_ua,temp_decic,charger_online,battery_status,charger_status,charger_health,charger_cv_max_uv,battery_health'
i=0
seen_online=0
full_since=
while [ "$i" -lt 4321 ]; do
    [ "$(cat /proc/sys/kernel/random/boot_id)" = "$boot" ] || exit 2
    read -r task_up task_idle < /proc/uptime
    sample=$(awk -F= -v t="$task_up" '
      FILENAME ~ /sc27xx-fgu/ {b[$1]=$2}
      FILENAME ~ /sgm41511-charger/ {c[$1]=$2}
      END {
        split("CAPACITY CHARGE_NOW VOLTAGE_NOW VOLTAGE_OCV CURRENT_NOW CURRENT_AVG TEMP", fields, " ");
        for (j=1;j<=7;j++) if (b["POWER_SUPPLY_" fields[j]] !~ /^-?[0-9]+$/) exit 2;
        if (c["POWER_SUPPLY_ONLINE"] !~ /^[01]$/ ||
            c["POWER_SUPPLY_CONSTANT_CHARGE_VOLTAGE_MAX"] !~ /^[0-9]+$/ ||
            !("POWER_SUPPLY_STATUS" in b) || !("POWER_SUPPLY_HEALTH" in b) ||
            !("POWER_SUPPLY_STATUS" in c) || !("POWER_SUPPLY_HEALTH" in c)) exit 2;
        printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n",t,
          b["POWER_SUPPLY_CAPACITY"],b["POWER_SUPPLY_CHARGE_NOW"],
          b["POWER_SUPPLY_VOLTAGE_NOW"],b["POWER_SUPPLY_VOLTAGE_OCV"],
          b["POWER_SUPPLY_CURRENT_NOW"],b["POWER_SUPPLY_CURRENT_AVG"],
          b["POWER_SUPPLY_TEMP"],c["POWER_SUPPLY_ONLINE"],
          b["POWER_SUPPLY_STATUS"],c["POWER_SUPPLY_STATUS"],
          c["POWER_SUPPLY_HEALTH"],c["POWER_SUPPLY_CONSTANT_CHARGE_VOLTAGE_MAX"],
          b["POWER_SUPPLY_HEALTH"];
      }' /sys/class/power_supply/sc27xx-fgu/uevent /sys/class/power_supply/sgm41511-charger/uevent)
    printf '%s\n' "$sample"
    IFS=, read -r up cap charge voltage ocv cur avg temp online bat_status chg_status chg_health cv bat_health <<ROW
$sample
ROW
    [ "$online" = 1 ] && seen_online=1
    if [ "$seen_online" = 1 ] && [ "$online" = 0 ]; then
        echo 'External supply disconnected; separate this charge cycle.' >&2
        break
    fi
    if [ "$temp" -ge 450 ] || [ "$voltage" -le 3400000 ]; then
        echo "Recorded observation boundary: temp=$temp voltage=$voltage; charging is not controlled by this logger." >&2
        break
    fi
    # Stock FGU fullbatt-voltage=4.2V; native13 requires CV >=4.268V.
    # Use the exact recorded sample and elapsed uptime, not a second read.
    if [ "$cap" = 100 ] && [ "$bat_status" = Full ] &&
       [ "$chg_status" = Full ] && [ "$online" = 1 ] &&
       [ "$bat_health" = Good ] && [ "$chg_health" = Good ] &&
       [ "$temp" -ge 150 ] && [ "$temp" -le 450 ] &&
       [ "$voltage" -ge 4200000 ] && [ "$ocv" -ge 4000000 ] &&
       [ "$cv" -ge 4268000 ] && [ "$cur" -ge 0 ] && [ "$cur" -le 20000 ] &&
       [ "$avg" -ge -20000 ] && [ "$avg" -le 20000 ]; then
        [ -n "$full_since" ] || full_since="$up"
        if awk -v t="$up" -v first="$full_since" 'BEGIN {exit !(t-first >= 120)}'; then
            echo 'Recorded qualified 100% and hardware Full stable for >=120 seconds.' >&2
            break
        fi
    else
        full_since=
    fi
    if [ "$seen_online" = 0 ] && [ "$i" -ge 60 ]; then
        echo 'No external supply detected during the ten-minute observation window.' >&2
        break
    fi
    i=$((i+1))
    [ "$i" -lt 4321 ] && sleep 10 || true
done
