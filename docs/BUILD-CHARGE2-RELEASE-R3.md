# Build `v2026.10.15-charge2-r3`

This is the current complete U30 Air charge2 release builder. It binds the ZIP to the exact clean source commit recorded in `release/BUILD-MANIFEST.json`. The kernel bundle is rebuilt by compiling the changed `sprd_wlan_combo.ko` from committed source and replacing that member in the audited r2 kernel bundle. The builder verifies the original source evidence, the actual Kbuild `.config`, WCN `Module.symvers`, the complete upstream input set, and all archive members. The kernel `Image`, generic ramdisk, and the other 378 modules are reused byte-for-byte.

The original `tools/build-charge2-release.py` remains the strict R2 assembler and rejects changed upstream inputs. Use `tools/build-charge2-release-r3.py` for this release.

## Rebuild the Wi-Fi packages

Use the official OpenWrt SDK archive pinned in `openwrt/sae-offload/inputs.json`, with the base package feed checked out at commit `f0a60eee2fe051741c643ea6118718aae1ef17fb`. The SDK `.config`, all 63 original hostapd patches, hostapd recipe, and `wpad.init` are checked before the build. The recipe temporarily increments the package release to `r6`, adds the reviewed SAE result patch, and scopes the service environment to the hostapd procd instance. The script restores those feed files when the build finishes.

```sh
python3 tools/build-charge2-sae-offload-wifi.py \
  --sdk-root /path/to/openwrt-sdk-25.12.5-armsr-armv8_gcc-14.3.0_musl.Linux-x86_64 \
  --sdk-archive /path/to/openwrt-sdk-25.12.5-armsr-armv8_gcc-14.3.0_musl.Linux-x86_64.tar.zst \
  --out /path/to/r3-build/wifi
```

The output includes the two r6 APKs, SDK build log, feed patch manifest, SDK config, applied hostapd patch and build provenance. Its hashes must match the pins; a different compiler result stops the build.

## Rebuild the Wi-Fi kernel module

The base kernel package and original build evidence are fixed inputs. On WSL/Linux with Python 3.12+, GNU `tar`/`gzip`, AArch64 GCC/binutils and the original kernel build tree, run:

```sh
python3 tools/build-charge2-sae-offload-kernel.py \
  --repo . \
  --base-kernel /path/to/mu300-kernel-7.2.tar.gz \
  --base-provenance /path/to/kernel-evidence/BUILD-PROVENANCE.json \
  --base-evidence /path/to/kernel-evidence \
  --kernel-tree /path/to/linux-7.2.9 \
  --kernel-output /path/to/out-7.2.9 \
  --wcn-symbols /path/to/wcn_bsp/Module.symvers \
  --output-kernel /path/to/r3-build/mu300-kernel-7.2.tar.gz \
  --evidence-out /path/to/r3-build/module-evidence
```

The output kernel bundle changes only `./modules/sprd_wlan_combo.ko`. The package builder rejects a dirty checkout, an unreviewed upstream file change, a different module hash, or any change to another archive member.

## Assemble the single ZIP

Provide the pinned OpenWrt rootfs, rollback kernel, `packages.adb`, `ucode-mod-socket` APK, the audited base kernel and its original evidence, plus the two outputs above:

```sh
python3 tools/build-charge2-release-r3.py \
  --repo . --source-commit <final-40-character-commit> \
  --official-rootfs /path/to/mu300-openwrt-luci-rootfs.tar.gz \
  --rollback-kernel /path/to/mu300-kernel.tar.gz \
  --kernel /path/to/r3-build/mu300-kernel-7.2.tar.gz \
  --base-kernel /path/to/mu300-kernel-7.2.tar.gz \
  --module-provenance /path/to/r3-build/module-evidence/MODULE-BUILD-PROVENANCE.json \
  --module-evidence /path/to/r3-build/module-evidence \
  --wpad-apk /path/to/r3-build/wifi/wpad-basic-mbedtls-2025.08.26~ca266cc2-r6.apk \
  --hostapd-common-apk /path/to/r3-build/wifi/hostapd-common-2025.08.26~ca266cc2-r6.apk \
  --wifi-build-evidence /path/to/r3-build/wifi \
  --apk-index /path/to/packages.adb \
  --apk /path/to/ucode-mod-socket-2026.01.16~85922056-r2.apk \
  --kernel-provenance /path/to/original-kernel-evidence/BUILD-PROVENANCE.json \
  --kernel-evidence /path/to/original-kernel-evidence \
  --out /path/to/release/v2026.10.15-charge2-r3
```

The sibling artifact is the single complete `U30-Air-Openwrt-r3.zip`. It carries the source snapshot, the full build evidence, installer, package manifests and SHA-256 checksums. Run `pwsh -NoProfile -File .\install-u30air-host.ps1 -VerifyOnly` before the installer. A successful build or `VerifyOnly` does not mean this exact ZIP has been installed or booted on hardware.

## Regression tests and validation boundary

Run `python3 -m unittest discover -s tests` and `python3 upstream/u30air/run-tests.py`. The SAE fixture compiles the actual patch helper and association bridge, checks malformed inputs and authentication state, and requires the old unconditional PWE guard negative control to fail.

The driver module, AP generator and service init hashes match the device-tested versions. The r3 `wpad` has an additional SAE-only PWE guard compared with the device-tested binary. That correction passes the regression fixtures, but its exact binary and the complete r3 ZIP have not been tested on-device. `BUILD-MANIFEST.json` keeps full-package hardware validation and install/boot validation false.
