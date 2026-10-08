#!/bin/sh
# Read-only preflight; no raw I2C access and no configuration/role changes.
PATH="$PATH:/opt/mu300/bin:/usr/sbin:/sbin"
printf '\n--- System ---\n'
uname -a
cat /etc/openwrt_release 2>/dev/null
mu300-device 2>/dev/null
printf '\n--- USB role and gadget state ---\n'
for f in /sys/class/usb_role/*/role /sys/class/udc/*/state; do
 [ -r "$f" ] && printf '%s: %s\n' "$f" "$(cat "$f")"
done
printf '\n--- Charger binding ---\n'
for f in /sys/bus/i2c/devices/*-006b/driver; do
 [ -L "$f" ] && readlink -f "$f"
done
printf '\n--- Power ---\n'
for d in /sys/class/power_supply/*; do
 [ -d "$d" ] || continue
 for n in status online current_now voltage_now capacity temp model_name; do
  [ -r "$d/$n" ] && printf '%s/%s: %s\n' "${d##*/}" "$n" "$(cat "$d/$n")"
 done
done
printf '\n--- USB devices ---\n'
for d in /sys/bus/usb/devices/*; do
 [ -r "$d/idVendor" ] || continue
 printf '%s %s:%s %s\n' "${d##*/}" "$(cat "$d/idVendor")" "$(cat "$d/idProduct")" "$(cat "$d/product" 2>/dev/null)"
done
printf '\n--- Interfaces ---\n'
ip -br link
printf '\n--- LAN bridge ---\n'
uci -q get network.lan.device
printf '\n--- Prototype services ---\n'
ls /etc/init.d/*usb* /usr/local/sbin/mu300-usb-auto 2>/dev/null || :
