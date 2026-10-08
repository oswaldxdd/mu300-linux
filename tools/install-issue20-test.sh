#!/bin/sh
# Install a supplied local test bundle on an existing 6.18 OpenWrt system.
set -eu
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo 'Run as root.' >&2; exit 1; }
[ -f /etc/openwrt_release ] || { echo 'This package requires OpenWrt.' >&2; exit 1; }
case $(uname -r) in
    6.18.*) ;;
    *) echo 'Requires a device already running Linux 6.18.x; record your current versions first.' >&2; exit 1 ;;
esac
case $(/opt/mu300/bin/mu300-device) in
    f50|u30air) ;;
    *) echo 'Unsupported device.' >&2; exit 1 ;;
esac
sha256sum -c SHA256SUMS
sh -n ./mu300-update
MU300_KERNEL_BUNDLE="$PWD/mu300-kernel-6.18-issue20-pm1.tar.gz" \
    MU300_RELEASE=issue20-pm1 sh ./mu300-update boot
echo 'Update completed. Reboot, then check uname -r for 6.18.54-issue20-pm1.'
