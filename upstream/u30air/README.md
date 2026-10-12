# U30 Air charging, Host, SOC and FCC learning

This source integration is scoped to the U30 Air on Linux 7.2.x and is integrated into release tag
`v2026.10.15` at `52093b28c9c3e0280b1a1be6f0d4b5437a6053a1`. The functional implementation was carried forward
from the independently reviewed integration commit `4f93959ca0596abc2bdcd301f96a6f620cc8100c`. The selected source came from the local
`usb-host-fcc-learning/native14-development/firmware/restorefix3-release` candidate, its matching
`usb-host-fcc-learning/native14-development/firmware/fcc-learning/source` sources, and
`github-sync/mu300-linux-panel-base/src/experimental/u30air-native14`.
The candidate README contains older stage notes; the integration follows the source and tests.

`build.sh` first applies the common mainline port and its patches. For 7.2.x it then installs the U30 sources and
configuration from this directory, replacing the legacy FGU implementation and its 0006/0010/0011 patches. The
U30 fragment disables `CONFIG_CHARGER_BQ256XX` and enables the native SGM41511 charger, USB role switch, and
RTL8152 driver. F50 and the 6.18 path do not use these replacements. Build the 7.2.9 image and matching modules
with the normal mainline build flow:

```sh
KV=7.2.9 OUTDIR=out-7.2 WORK=/work SRC=/src bash /work/build.sh
KV=7.2.9 OUTDIR=out-7.2 WORK=/work SRC=/src bash /work/build-modules.sh
```

## Power and USB behavior

The native SGM41511 driver is the only charger and role-policy owner on this U30 7.2 path; the BQ256xx driver is
disabled in the device fragment. The default policy is manual Device mode. A Host request requires a live external
input indication, turns no OTG boost on, and keeps charging controlled by the kernel charger policy. Automatic Host
policy is opt-in and asynchronous. `mu300-usb` refuses Host and role writes unless the native driver is bound and
reports external input present with boost off. The boot path returns to Device mode. Older kernels without the
native driver, including the release's 6.18 path, safely refuse these Host operations.

The RTL8152 kernel driver makes a compatible USB Ethernet adapter available as a network interface. The OpenWrt
panel lists discovered adapters and adds only the selected adapter to the configured LAN bridge. A saved port is
reattached on network hotplug and LAN ifup; merely switching to Host does not alter bridge membership.

## SOC and Full reporting

The FGU keeps Linux `capacity` as an integer percentage. `capacity_tenth` is read from the same charge-counter
sample and exported on the power-supply class device as an integer from 0 through 1000. It provides one-decimal
display resolution; it does not establish 0.1% measurement accuracy. The kernel reports 1000 only when its
protected Full policy succeeds. The dashboard uses the actual `status=Full` state and never infers Full from a
rounded 100% value.

## FCC persistence

The FGU accepts a restored model only during its initial restore window, before a learned cycle is committed, and
after validating design-capacity, gain, and model bounds. The runtime learns only from qualified cycles with
continuous input, charger quiet readback, stable OCV, sufficient counter span, and no source, thermal, charger, or
fuel-gauge fault. Quiet mode has a bounded lease and a separate charger watchdog.

The OpenWrt procd service and Ubuntu systemd unit run on every U30 Air boot and exit quietly when the current kernel
does not expose the native 7.2 attributes. This allows a device first booted on its vendor kernel to acquire FCC
support after a later kernel upgrade. The recorder discovers the eMMC by MMC type and non-removable status, uses
the boot-published Linux slot and matching `dtbo_a`/`dtbo_b`, and requires the persistent filesystem source to match
the boot-published root markers. Records bind a SHA-256 hash of the eMMC CID and active DTBO to the design
capacity and ADC gain, include a SHA-256 corruption check, and are range-checked before restoring. A missing or
invalid record leaves the kernel default intact.

The stock boot path uses its vendor device tree; this integration does not add a device-tree overlay. The FGU uses
the standard battery design capacity and treats `charge-full-microamp-hours` as optional: if absent it warns and
uses design capacity; a present but invalid value fails probe. The actual stock DT property set and successful FGU
probe still require device-side confirmation.

## Validation

The targeted U30 runner is:

```sh
python3 upstream/u30air/run-tests.py
```

It exercises the C policy functions and extracted production driver helpers, charger-lease teardown, FGU learning,
restore payloads, active-slot/eMMC/DTBO resolution, persistent mount validation, and the shipped FCC record codec.
The repository regression commands passed static integration (37 tests), fake-root device scripts (106 tests; 3
skip cases require a non-root user), USB role management and NIC LAN hotplug (16 tests), and dashboard SOC rendering
(20 tests, including JavaScript). The FCC/Host runner also passes its 13 compiled or production-source fixtures;
the public FGU status case is compiled and exercised across five status/error/device variants. These are source and
simulation checks; they do not establish charger behavior, battery accuracy, Host enumeration, Ethernet traffic, or
rollback on hardware.
