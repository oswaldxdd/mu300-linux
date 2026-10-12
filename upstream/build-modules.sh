#!/bin/bash
# Build the out-of-tree vendor modules (WCN, modem, Mali) against the mainline tree built by build.sh, and collect
# the kernel's own modules next to them (upstream/out/modules, flat).
# Run inside the mu300-mainline-build container, or set WORK/SRC/OUTPUT_DIR for an isolated Linux build.
set -eo pipefail
KV=${KV:-7.2.9}
WORK=${WORK:-/work}
SRC=${SRC:-/src}
KERNEL_TREE=${KERNEL_TREE:-$SRC/linux-$KV}
KERNEL_OUT=${KERNEL_OUT:-$SRC/out-$KV}
OUTPUT_DIR=${OUTPUT_DIR:-$WORK/${OUTDIR:-out-7.2}}   # as in build.sh
MODULE_BUILD=${MODULE_BUILD:-$SRC/mod-build}
# every out-of-tree module, in dependency order (wlan/bt use wcn_bsp, the PMIC watchdog and Mali the modem's headers)
mods=${*:-wcn_bsp sprd_wlan_combo sprdbt_tty sprd_modem sprd_pmic_wdt mali}
mkdir -p "$OUTPUT_DIR/modules" "$MODULE_BUILD"
# a full build starts clean, so no module of another kernel release ends up next to the new ones
[ $# -gt 0 ] || rm -f "$OUTPUT_DIR"/modules/*.ko "$OUTPUT_DIR"/modules/*.log
# Module.symvers for the built-in exports (pcie-sprd etc.)
make -C "$KERNEL_TREE" O="$KERNEL_OUT" ARCH=arm64 -j"$(nproc)" modules > "$KERNEL_OUT/modules.log" 2>&1 || { tail -20 "$KERNEL_OUT/modules.log"; exit 1; }
# the kernel's own modules (the =m options of mu300-mainline.config), stripped, flat next to the vendor ones: the
# bundle and mu300-update keep one flat directory, which depmod (Ubuntu) and kmodloader (OpenWrt) both index
rm -rf "$KERNEL_OUT/mod-install"
make -C "$KERNEL_TREE" O="$KERNEL_OUT" ARCH=arm64 INSTALL_MOD_PATH="$KERNEL_OUT/mod-install" INSTALL_MOD_STRIP=1 DEPMOD=true modules_install >> "$KERNEL_OUT/modules.log" 2>&1 ||
    { tail -20 "$KERNEL_OUT/modules.log"; exit 1; }
# the set of the previous run goes first: an option no longer =m leaves no module behind on a partial run
if [ -f "$KERNEL_OUT/modules.in-tree" ]; then while read -r ko; do rm -f "$OUTPUT_DIR/modules/$ko"; done < "$KERNEL_OUT/modules.in-tree"; fi
find "$KERNEL_OUT/mod-install/lib/modules" -name '*.ko' -exec basename {} \; | sort > "$KERNEL_OUT/modules.in-tree"
# one flat directory holds them all, and modprobe takes - and _ for the same: two modules of one name would shadow
# each other
tr - _ < "$KERNEL_OUT/modules.in-tree" | sort > "$KERNEL_OUT/modules.in-tree.names"
[ -z "$(uniq -d "$KERNEL_OUT/modules.in-tree.names")" ] || { echo "in-tree modules share a name: $(uniq -d "$KERNEL_OUT/modules.in-tree.names")" >&2; exit 1; }
find "$KERNEL_OUT/mod-install/lib/modules" -name '*.ko' -exec cp {} "$OUTPUT_DIR/modules/" \;
extra=
for m in $mods; do
    rm -rf "$MODULE_BUILD/$m" && mkdir -p "$MODULE_BUILD" && cp -r "$WORK/modules/$m" "$MODULE_BUILD/$m"
    # wlan/bt use wcn_bsp's exports and its vendor headers (../wcn_bsp/kinclude)
    [ -d "$MODULE_BUILD/wcn_bsp" ] || cp -r "$WORK/modules/wcn_bsp" "$MODULE_BUILD/wcn_bsp"
    # the PMIC watchdog talks to pm_sys over SIPC, so it needs the modem stack's headers next to it
    [ -d "$MODULE_BUILD/sprd_modem" ] || cp -r "$WORK/modules/sprd_modem" "$MODULE_BUILD/sprd_modem"
    # the Mali DDK needs its own configuration switches (same ones the 5.4 build uses)
    margs=
    kcflags=
    [ "$m" = mali ] && kcflags="-I$MODULE_BUILD/mali/kinclude"
    # the Mali driver calls Trusty for protected mode, so it needs the vendor trusty headers that ship with
    # the modem modules
    [ "$m" = mali ] && [ ! -d "$MODULE_BUILD/mali/kinclude" ] && cp -r "$WORK/modules/sprd_modem/kinclude" "$MODULE_BUILD/mali/kinclude"
    [ "$m" = mali ] && margs="src=$MODULE_BUILD/mali CONFIG_MALI_MIDGARD=m CONFIG_MALI_PLATFORM_NAME=qogirn6pro CONFIG_MALI_DEVFREQ=y CONFIG_DEVFREQ_THERMAL=y CONFIG_MALI_DEBUG=n CONFIG_MALI_FENCE_DEBUG=n BUILD=no"
    make -C "$KERNEL_OUT" ARCH=arm64 M="$MODULE_BUILD/$m" KBUILD_EXTRA_SYMBOLS="$extra" KCFLAGS="$kcflags" $margs -j"$(nproc)" modules 2>&1 | tee "$OUTPUT_DIR/modules/$m.log"
    [ -f "$MODULE_BUILD/$m/Module.symvers" ] && extra="$extra $MODULE_BUILD/$m/Module.symvers"
    for ko in $(find "$MODULE_BUILD/$m" -name '*.ko'); do
        ! echo "${ko##*/}" | tr - _ | grep -Fqx -f - "$KERNEL_OUT/modules.in-tree.names" || { echo "$m: ${ko##*/} has the name of an in-tree module" >&2; exit 1; }
        cp "$ko" "$OUTPUT_DIR/modules/"
    done
done
ls -la "$OUTPUT_DIR"/modules/*.ko
