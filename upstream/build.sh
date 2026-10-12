#!/bin/bash
# Build mainline for the MU300 inside mu300-mainline-build: Image + DTB. /work = this directory, /src = kernel tree volume
set -eo pipefail
KV=${KV:-7.2.9}
# /work and /src remain the container defaults; WORK/SRC let the same flow run
# directly in an isolated Linux build directory for integration verification.
WORK=${WORK:-/work}
SRC=${SRC:-/src}
# where Image, DTB and modules.builtin go: /work/out for 6.18, OUTDIR=out-7.2 keeps another kernel next to it
OUT=${OUT:-$WORK/${OUTDIR:-out-7.2}}
# the kernel source, fetched once into the volume and checked against kernel.org's checksum list
if [ ! -d "$SRC/linux-$KV" ]; then
    base=https://cdn.kernel.org/pub/linux/kernel/v${KV%%.*}.x
    mkdir -p "$SRC"
    cd "$SRC"
    curl -fsSLO "$base/linux-$KV.tar.xz"
    curl -fsSL "$base/sha256sums.asc" | grep " linux-$KV.tar.xz\$" | sha256sum -c - || { rm -f linux-$KV.tar.xz; exit 1; }
    tar -xJf linux-$KV.tar.xz && rm linux-$KV.tar.xz
fi
cd "$SRC/linux-$KV"
# MU300 patches (idempotent); MU300_PROBE_STAGE=N adds the boot-stage reset probe (debug)
python3 "$WORK/port/install.py" .
[ -n "${MU300_PROBE_STAGE:-}" ] && python3 "$WORK/debug/install-probe.py" . "$MU300_PROBE_STAGE"
# a patch either applies or is applied already; anything else stops the build (it used to be skipped silently).
# patches/<major.minor>/ holds what only one kernel needs (what a newer one has upstream already)
for p in "$WORK"/patches/*.patch "$WORK"/patches/"$(echo "$KV" | cut -d. -f1,2)"/*.patch; do
    [ -e "$p" ] || continue
    # The U30 Air 7.2 integration replaces the legacy fuel-gauge implementation
    # with its native FCC/SOC policy. Reapplying those patches would either
    # conflict with the replacement or leave a second, obsolete path behind.
    if [[ "$KV" == 7.2.* && "$(basename "$p")" =~ ^(0006|0010|0011)- ]]; then
        echo "U30 Air 7.2: using native FCC fuel gauge instead of $(basename "$p")"
        continue
    fi
    if patch -p1 -N -s --dry-run < "$p" >/dev/null 2>&1; then patch -p1 -N -s < "$p"
    elif patch -p1 -R -s -f --dry-run < "$p" >/dev/null 2>&1; then :
    else echo "patch does not apply to $KV: $p" >&2; exit 1; fi
done
U30_CONFIG=
case "$KV" in
  7.2.*)
    # Replace the legacy U30 FGU/BQ256xx path only on 7.2. The F50 stays
    # without a charger, and 6.18 retains its existing compatibility path.
    python3 "$WORK/u30air/install.py" "$SRC/linux-$KV"
    U30_CONFIG=$WORK/u30air/config
    ;;
esac
O=$SRC/out-$KV
mkdir -p "$O"
make O=$O ARCH=arm64 allnoconfig >/dev/null
./scripts/kconfig/merge_config.sh -m -O $O $O/.config "$WORK/mu300-mainline.config" >/dev/null
if [ -n "$U30_CONFIG" ]; then
  ./scripts/kconfig/merge_config.sh -m -O $O $O/.config "$U30_CONFIG" >/dev/null
fi
make O=$O ARCH=arm64 olddefconfig >/dev/null
# options Kconfig did not take; ones known not to exist in this kernel are listed in config-ignored.txt, any
# other one stops the build (a silently dropped option has cost a working feature before)
bad=
while IFS= read -r l; do
  case "$l" in CONFIG_*=*) k=${l%%=*}; v=${l#*=}; g=$(grep -E "^$k=" $O/.config | cut -d= -f2- || true);
    # Validate the merged configuration, not values superseded by the U30
    # fragment. In particular, base BQ256XX=y is intentionally overridden.
    if [ -n "$U30_CONFIG" ] && grep -q "^$k=" "$U30_CONFIG"; then continue; fi
    if [ "$v" = n ]; then
      case "$g" in y|m) bad="$bad\nNOT DISABLED: $k (got $g)";; esac
      continue
    fi
    [ "$g" = "$v" ] || grep -qx "$k" "$WORK/config-ignored.txt" 2>/dev/null || bad="$bad\nNOT SET: $k want $v got ${g:-unset}";; esac
done < "$WORK/mu300-mainline.config"
if [ -n "$U30_CONFIG" ]; then
  while IFS= read -r l; do
    case "$l" in CONFIG_*=*) k=${l%%=*}; v=${l#*=}; g=$(grep -E "^$k=" $O/.config | cut -d= -f2- || true);
      if [ "$v" = n ]; then
        case "$g" in y|m) bad="$bad\nNOT DISABLED: $k (got $g)";; esac
        continue
      fi
      [ "$g" = "$v" ] || bad="$bad\nNOT SET: $k want $v got ${g:-unset}";; esac
  done < "$U30_CONFIG"
fi
[ -z "$bad" ] || { printf "config options not taken:$bad\n" >&2; exit 1; }
# on failure show the compiler's own messages: a plain grep for "error" also matches object names like
# fdt_strerror.o and used to fill the report with those
make O=$O ARCH=arm64 -j"$(nproc)" Image > $O/build.log 2>&1 || {
    grep -n -E ": (fatal )?error: |-Werror|treated as errors|undefined reference|No such file|Killed|internal compiler error|\*\*\*" -A3 $O/build.log | head -80 || true
    echo "--- end of build.log:"; tail -25 $O/build.log || true
    exit 1
}
cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp -I include -I scripts/dtc/include-prefixes \
  "$WORK/dts/ums9620-mu300.dts" | dtc -I dts -O dtb -o "$O/ums9620-mu300.dtb" -
mkdir -p $OUT
cp $O/arch/arm64/boot/Image $O/ums9620-mu300.dtb "$OUT/"
# for depmod on the device: modprobe of a built-in driver fails without them
cp $O/modules.builtin $O/modules.builtin.modinfo "$OUT/"
ls -la "$OUT"
