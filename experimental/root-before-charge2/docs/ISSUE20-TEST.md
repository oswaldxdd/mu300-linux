# Issue #20 runtime PM test build

Experimental kernel update for an **already installed OpenWrt system running
Linux 6.18.x** on a ZTE U30 Air / F50. It is not a whole-disk OpenWrt image.
The package installer refuses other running kernel series and devices.

## Change

`cp_dele_on_commad()` treated the positive success return from
`pm_runtime_get_sync()` as failure, then looped indefinitely with `mdelay(1)`.
The fix accepts every nonnegative result, balances the reference on negative
results with `pm_runtime_put_noidle()`, and tries transient `-EAGAIN`/`-EBUSY`
errors at most five times, sleeping 20 ms between attempts. Permanent failures
return immediately. Failure sends `SMSG_VAL_DELE_REQ_FAIL` and does not enable
SIPA. Repeated ENABLE/DISABLE commands do not add or release a second owned
reference. Runtime PM is enabled before starting the connection thread.

This branch changes the mainline module only; it does not fix the vendor 5.4
source tree. The existing sysfs reset/message concurrency is outside this
patch's scope. Hardware validation remains required.

## Install the delivered package

Unzip on the computer. Connect the U30 Air (default USB address 192.168.78.1).
Save an OpenWrt configuration backup and confirm you can return to Android.
Before proceeding, inspect these read-only outputs:

```sh
ssh root@192.168.78.1 'uname -r; cat /etc/mu300/image-version; mu300-device'
```

If the device is not already running 6.18.x, record those outputs and plan a
separate kernel migration instead of bypassing the installer's check.

From the unpacked package directory on the computer:

```sh
ssh root@192.168.78.1 'mkdir -p /root/issue20-pm1'
scp -O mu300-kernel-6.18-issue20-pm1.tar.gz mu300-update install-test.sh SHA256SUMS root@192.168.78.1:/root/issue20-pm1/
ssh root@192.168.78.1
```

Then on OpenWrt:

```sh
cd /root/issue20-pm1
sh install-test.sh
# Only when the update reports success:
reboot
```

The installer checks hashes and uses the supplied updater with a local bundle
and explicit test tag; it does not fetch latest. Updating writes boot_b and
installs the matching modules. Do not cut power while it is writing. The
updater retains the previous boot image for rollback. Do not run another boot
update before finishing this test, as that replaces the single previous-image
backup. Keep stable power for installation; battery/USB tests come afterwards.

After reboot, expected `uname -r`: **6.18.54-issue20-pm1**. Check:

```sh
uname -r
modinfo sipa-dele | grep vermagic
logread | tail -n 80
dmesg | grep -E 'sipa_dele|soft lockup|Oops|panic'
```

Run 24–72 hours including sustained upload/download, hours of idle time followed
by resumed traffic, and cellular reconnects. Record uptime, unexpected reboots,
WAN recovery and the first PM error if one occurs. Absence of a reboot in one
run does not prove the fault is eliminated. Save `/sys/fs/pstore/*` after a crash,
before another reboot, and redact credentials/identifiers from shared logs.

## Rollback

While OpenWrt is still reachable, use the included updater:

```sh
cd /root/issue20-pm1
sh ./mu300-update rollback-boot
# Only when restoration succeeds:
reboot
```

If the test kernel cannot boot, use the project's Android fallback and reinstall
the known-good release with the computer installer, choosing update to retain
Linux data. A shell command cannot recover a system you cannot reach. The test
package does not include a device-specific stock boot image or proprietary files.

## Build and tests

Base: `oswaldxdd/mu300-linux` develop at `2d9cf7e`; underlying upstream at
`1a69a41ea9d20fadd575e9b082364d61ce5fcf9a`. Mainline source: kernel.org Linux
6.18.54 with the repository's port and patches, unchanged mainline config,
plus `LOCALVERSION=-issue20-pm1`. Cross compiler: Ubuntu's
`aarch64-linux-gnu-gcc`. All vendor modules are rebuilt together.
The package manifest supplies exact source hashes and build details.

Existing build scripts expect `/work` to point at `upstream/` and `/src` to be a
dedicated kernel build directory. In that isolated Linux build environment:

```sh
export CROSS_COMPILE=aarch64-linux-gnu-
export LOCALVERSION=-issue20-pm1
bash /work/build.sh
bash /work/build-modules.sh
upstream/make-bundle.sh mu300-kernel-6.18-issue20-pm1.tar.gz /path/to/reference/mu300-kernel.tar.gz
```

The reference helper bundle is upstream `v2026.10.08`, checked against that
release's SHA256SUMS. It supplies static busybox/logdw only. The kernel and
modules in this test package are newly built, not relabeled upstream binaries.

`tests/test_sipa_pm.py` compiles and executes the actual helper/command-handler
code against stub PM APIs, covering 0/1 success, transient and permanent errors,
bounded retry, negative-return reference accounting, failure reply, repeated
commands and recovery after a transient error. Existing repository tests cover
ramdisk construction, module order, device scripts and update helpers.
