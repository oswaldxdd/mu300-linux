# MU300 native kmod compatibility provider

This APK provider is built into release `v2026.10.15-charge2-r2` for the U30 Air firmware kernel `7.2.9-u30air-charge2` only. It advertises the four dependency capabilities `kmod-dummy`, `kmod-inet-diag`, `kmod-nft-socket`, and `kmod-nft-tproxy` using verified built-in kernel features and modules already shipped by this firmware.

The package contains no kernel modules and does not alter the kernel configuration. Its payload is documentation and a strict pre-install/pre-upgrade guard. The guard checks the running kernel release and architecture, required `/proc/config.gz` symbols, exact hashes and vermagic for `dummy.ko`, `udp_diag.ko`, and `raw_diag.ko`, plus the exact `modules.builtin` hash and required built-in entries. A kernel update invalidates this provider until the new kernel is checked and the package is rebuilt.

This is a project-local compatibility provider, not an official OpenWrt or Nikki package, kmod feed entry, or signed upstream package. The firmware build registers this unsigned package in the image with a per-command untrusted-package allowance; it does not change APK's global trust configuration. Do not install it on another kernel or distribute it separately from its matching firmware.
