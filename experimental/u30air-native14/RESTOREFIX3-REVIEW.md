# Restorefix3 offline review

## Problem and change

The restorefix2 installer correctly rolled back when its updater returned nonzero. The updater's confirmed-current boot branch had printed `boot: already up to date` and returned success from `boot_update`, but the case ended on a false `[ -n "$BOOT_CHANGED" ]` test and returned 1. Restorefix3 keeps updater failure propagation intact and makes the optional reboot notice conditional without changing boot bytes or historical boot metadata when they already match.

The same patched updater bytes are present in the candidate install package and exactly one executable regular rootfs member at `./opt/mu300/bin/mu300-update`. The corresponding source archive updates both `project/rootfs/overlay/opt/mu300/bin/mu300-update` and `native14/overlay/opt/mu300/bin/mu300-update`. FCC runtime assets and strict charge-to-full observer are retained. The previously compiled native14 kernel bundle is reused unchanged.

## Offline evidence

- Frozen updater `fc64d98d…` reproduced the false exit 1 on the real already-current boot path.
- Patched updater `9a6f94fa…` passed actual changed-boot, same-Image/ramdisk and written-SHA no-op, and missing-boot failure branches against temporary regular files.
- The Ubuntu-24.04 installer suite passed 40 scenarios: 39 simulated temporary-file cases and one actual updater/installer rollback integration. The latter copied all 31 real bundle modules to a temporary old root, failed on a deliberately invalid regular-file boot image, propagated nonzero, then verified rollback restored the original root/modules state and 64 MiB boot-file contents.
- Raw stdout is retained in `test-deployment-compat-restorefix3-20261008.stdout.log`; the separate test source and manifest record its exact SHA. The original 40-case transcript from the earlier fixture label remains preserved as `test-deployment-compat-20261008.stdout.log`.
- The standalone updater test source covers successful boot update, no-op success without writes, and genuine boot lookup failure. Its final package run is recorded alongside the ZIP verification outputs.

These are offline file-fixture results, not device validation. No actual boot block device was opened, and no firmware was flashed. The candidate's boot, modem, charging, Host/RTL8153B hotplug, WAN sharing, and full battery accuracy remain unverified. The independently recorded charging endpoint was from a different native14 image. The low-SOC deviation still needs physical measurement.

## Follow-up SOC measurement

Use the included `observe-native14-discharge.sh`, `observe-native14-await-discharge.sh`, and `observe-native14-fcc-status.sh` for the planned discharge endpoint. The Stage 1 procedure copies the FCC status script to `/tmp/observe-native14-fcc.sh`, the alias expected by the await script. The discharge logger is bounded at 10% / 3.40 V, thermal limit, or restored external supply; it never powers off the device. The older `firmware/observe-full-discharge.sh` samples repeatedly and stops at 20% / 3.45 V; it cannot support a claimed 0–100% independent accuracy result.

The source-only `install-kernel-device.sh` remains a native11-to-native12 legacy tool and must not be run on native14.
