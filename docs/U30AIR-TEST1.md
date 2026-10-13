# U30 Air charge2 test image 1

This is a test build based on the stable charge2 source tree. It ports selected fixes and features from the pinned
upstream snapshot; it is not an upstream merge and is not a replacement for the published release.

> Historical release boundary: `charge2-test1-20261012` is frozen at source commit `b6e323125f8231c605dbe2c90bceb4407dcb18a7`.
> Its updater release lookup retains the generic upstream repository behavior. Repository routing to `oswaldxdd/mu300-linux`
> applies only to the current source branch and follow-up `r2`; it was not part of the frozen test1 package.

## Source and scope

- Charge2 source baseline: `75b1b2d`.
- Upstream review source: `dikeckaan/mu300-linux` at `d7a1b2b12dc2f93a7ca5325b246fc563153f042f`.
- Scope: the six reviewed areas—AT/LuCI resilience, Wi-Fi/WCN recovery, country-aware DFS avoidance, cellular IPv6
  recovery and old-prefix deprecation, updater concurrency/config merge, and installed-package/config recovery.
- The test tree also carries the panel additions implemented for this build: local SMS persistence, traffic totals,
  CPU profile/status, and thermal status. See the UI and service changes in the source for exact controls.

The change is a selective port onto charge2. It preserves the charge2 source and its device-specific configuration;
it does not replace the tree with upstream `main`.

## Expected behavior

- Wi-Fi recovery drains/stops the receive path before teardown and makes one WCN reset attempt after a firmware
  assert (after a 10-second watchdog). If Wi-Fi remains stuck for about 70 seconds, `recovery_reboot` defaults to allowing a whole-device reboot
  as the final recovery step. Set the module parameter `recovery_reboot=0` to disable that reboot while collecting
  diagnostics; Wi-Fi then remains down until manually recovered. This is a recovery fallback, not proof that a
  hardware fault is fixed.
- The SC2355 does not provide radar detection. The OpenWrt hostapd generator is patched to avoid DFS channels while
  retaining channels appropriate to the configured country.
- Cellular IPv6 is re-enabled when the bearer is brought up in extend-prefix mode. In relay mode the RA helper marks
  the old delegated prefix deprecated when the carrier changes it. Existing interface and DNS settings are retained.
- In the frozen test1 package, `mu300-update check` remains read-only and retains the generic upstream release lookup.
  The U30 Air plus `uname -r` = `7.2.9-u30air-charge2` routing to `oswaldxdd/mu300-linux` applies only to the current
  source branch and follow-up `r2`. Generic online `apply`, `boot`, and kernel-changing operations remain rejected
  because upstream release assets are not charge2-compatible. Use a matching offline charge2 package.
- The toolkit update page is read-only for version checks and offers only rollback or cleanup of a retained prior
  system. It does not offer online apply or claim that this test package has already upgraded the device.
- Local state-changing updater operations share a protected runtime lock. The host installer mutex covers the host
  installer workflow; the Android-side installer checks that `/data` is not Android-shell-writable and that `/data/adb`
  is root-owned, non-group/world-writable, and not a symlink. It creates `/data/adb/.mu300-install-lock-root`
  (root-owned mode 0700) and atomically creates its `active` child lock while the script runs.
  This device lock does not claim to cover a separate later host-side boot-slot-arm phase.
- Package recovery records packages installed on the previous system, retries restoration after boot when network
  becomes available, and retains removed packages' settings/service state for restoration. DNS cleanup acts only on
  an attributable, unused orphaned local forwarder; uncertain or VPN-owned settings are left for the user.

## Boundaries

- The native charge2 SGM41511, PD/Type-C, FCC and SOC kernel stack is retained. No generic upstream charging policy
  or charger driver replaces it.
- No Android/Linux startup-chain synchronization, Android B-slot expansion, `ttyd`, `adb`, or VPN management-entry
  changes are included.
- The updater refuses generic online apply. This repository lookup only checks release metadata; it does not add an
  online firmware update path.

## Validation status

Source tests and the cross-build checks are not device validation. Before calling this build stable, test on the U30
Air itself: repeated Wi-Fi off/on and AP/station reconnection, sustained Wi-Fi traffic and WCN recovery, country-based
channel selection, cellular IPv6 reconnect/prefix change and client reachability, updater lock contention and
rollback, package restoration with both working and unavailable feeds, DNS/interface preservation, and a complete
Android installer run with rollback confirmation. Charging behavior also needs a separate physical-device test under
the user's actual power/USB setup. No hardware-success claim is made by this test note.
