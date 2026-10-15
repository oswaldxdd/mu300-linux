# U30 Air charge2 r3 firmware

This is the complete ZTE U30 Air firmware package for OpenWrt 25.12.5 and Linux `7.2.9-u30air-charge2`. It carries the charge2 runtime, matching kernel modules, the native-kmod capability provider, and the WPA2-PSK/WPA3-SAE mixed-mode compatibility update.

## Download and verify

Download the single `U30-Air-Openwrt-r3.zip` asset from the GitHub release and extract it. The package includes its source snapshot, offline installer, `PACKAGE-FILES.sha256` and release asset checksums. Run `pwsh -NoProfile -File .\install-u30air-host.ps1 -VerifyOnly` before installation to verify the package without connecting to the device.

## Install

The package is for a rooted ZTE U30 Air running Android with USB debugging enabled. Back up device data and existing configuration before installing. After `-VerifyOnly` succeeds, run `pwsh -NoProfile -File .\install-u30air-host.ps1` and review the target and write summary before accepting the final `INSTALL` prompt. This is the U30 Air offline installation flow, not a general OpenWrt sysupgrade image.

## Wireless fix and validation

The firmware updates the SC2355 driver, hostapd/wpad and AP configuration generator for WPA2-PSK/WPA3-SAE mixed mode. The driver module, AP generator and hostapd service init in this package match their device-tested hashes. The r3 wpad adds an SAE-only PWE guard correction; that exact binary passed synthetic patch regression tests but has not been device-tested at its final hash. The complete r3 ZIP has not been installed or booted as a whole. Keep the manifest's full-package and device-install validation fields false until that exact artifact is tested on U30 Air.

## Build and rollback boundaries

`BUILD-CHARGE2-RELEASE.md` records the pinned SDK, feed, kernel and APK inputs and the offline build commands. The kernel package changes only `sprd_wlan_combo.ko`; its `Image`, generic ramdisk and remaining 378 modules are inherited byte-for-byte from the audited base bundle. Keep the included rollback kernel and a full device backup available during installation. The native-kmod provider contains a fail-closed package guard and does not ship `.ko` files.
