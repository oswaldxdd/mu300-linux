# Recovery boundaries

Before installation writes, the installer preserves and verifies both boot slots, the full misc partition and its head in the printed work/backups directory. Verify the backup hashes before recovery. These backups do not contain the existing Linux root filesystem.

`mu300-next-boot android` followed by reboot selects Android for recovery; it does not restore the previous Linux rootfs. Do not restore an old boot partition against incompatible modules. A complete firmware rollback needs the corresponding rootfs/modules and verified boot/misc data.

For boot-only updates performed by `mu300-update boot`, the update tool preserves its previous boot image for `mu300-update rollback-boot`. This is separate from restoring an installation after wipe. Retain a known-good complete backup before choosing wipe.

The charge2 kernel has booted successfully on a device. Recovery after power loss and rollback of this newly assembled complete archive have not been tested.
