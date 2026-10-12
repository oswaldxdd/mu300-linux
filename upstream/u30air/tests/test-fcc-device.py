"""Run the shipped FCC disk/slot resolver against fake procfs and sysfs."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "rootfs/overlay/opt/mu300/lib/fcc-device.sh"


def put(root: Path, rel: str, data: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    return path


def resolve(root: Path, expected_status: int = 0) -> str:
    command = r'''
. "$2"
emmc=$(fcc_find_emmc "$1") || exit 11
slot=$(fcc_active_slot "$1") || exit 12
dtbo=$(fcc_find_dtbo "$1" "$emmc" "$slot") || exit 13
fcc_validate_store_mount "$1" /mnt/mu300-disk "$emmc" || exit 14
printf '%s|%s|%s\n' "${emmc##*/}" "$slot" "$dtbo"
'''
    result = subprocess.run(
        ["sh", "-c", command, "fcc-device-test", str(root), str(LIB)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert result.returncode == expected_status, (result.returncode, result.stdout, result.stderr)
    return result.stdout.strip()


def make_root(root: Path, disk: str = "mmcblk0", slot: str = "a") -> None:
    base = f"sys/class/block/{disk}"
    put(root, f"{base}/device/type", "MMC\n")
    put(root, f"{base}/device/cid", "0123456789abcdef\n")
    put(root, f"{base}/removable", "0\n")
    put(root, f"{base}p12/uevent", f"PARTNAME=dtbo_{slot}\n")
    other = "b" if slot == "a" else "a"
    put(root, f"{base}p13/uevent", f"PARTNAME=dtbo_{other}\n")
    put(root, "run/mu300/linux-slot", f"{slot}\n")
    put(root, "proc/cmdline", f"console=ttyS1 androidboot.slot_suffix=_{slot}\n")
    put(root, "run/mu300/disk-dev", "/dev/loop0\n")
    put(root, "run/mu300-root-dev", f"{disk}@27762098176\n")
    put(root, "proc/mounts", "/dev/loop0 /mnt/mu300-disk ext4 rw,noatime 0 0\n")


with tempfile.TemporaryDirectory(prefix="mu300-fcc-device-") as tmp:
    root = Path(tmp)
    make_root(root)
    assert resolve(root) == "mmcblk0|a|/dev/mmcblk0p12"

    # The live slot is taken from the initramfs marker and must agree with LK.
    put(root, "run/mu300/linux-slot", "b\n")
    assert resolve(root, 12) == ""
    put(root, "run/mu300/linux-slot", "a\n")
    put(root, "proc/cmdline", "console=ttyS1\n")
    assert resolve(root) == "mmcblk0|a|/dev/mmcblk0p12"
    put(root, "run/mu300/linux-slot", "\n")
    assert resolve(root, 12) == ""

    # Ambiguous eMMC, missing/duplicate slot DTBO, and a detached mount fail closed.
    put(root, "run/mu300/linux-slot", "a\n")
    second = f"sys/class/block/mmcblk2"
    put(root, f"{second}/device/type", "MMC\n")
    put(root, f"{second}/device/cid", "fedcba9876543210\n")
    put(root, f"{second}/removable", "0\n")
    assert resolve(root, 11) == ""
    (root / second).rename(root / "sys/class/block/unused")
    (root / "sys/class/block/mmcblk0p12/uevent").unlink()
    assert resolve(root, 13) == ""
    put(root, "sys/class/block/mmcblk0p12/uevent", "PARTNAME=dtbo_a\n")
    put(root, "sys/class/block/mmcblk0p14/uevent", "PARTNAME=dtbo_a\n")
    assert resolve(root, 13) == ""
    (root / "sys/class/block/mmcblk0p14").rename(root / "sys/class/block/unused-part")
    put(root, "proc/mounts", "/dev/loop1 /mnt/mu300-disk ext4 rw 0 0\n")
    assert resolve(root, 14) == ""

    # A non-removable eMMC whose Linux numbering is not mmcblk0 is discovered by type.
    root2 = Path(tmp) / "second"
    make_root(root2, "mmcblk2", "b")
    assert resolve(root2) == "mmcblk2|b|/dev/mmcblk2p12"

    # Booting from an SD partition is accepted only when that exact root source
    # and the initramfs published mount source agree; FCC identity remains eMMC-bound.
    put(root2, "sys/class/block/mmcblk1p1/partition", "1\n")
    put(root2, "run/mu300/disk-dev", "/dev/mmcblk1p1\n")
    put(root2, "run/mu300-root-dev", "/dev/mmcblk1p1\n")
    put(root2, "proc/mounts", "/dev/mmcblk1p1 /mnt/mu300-disk ext4 rw 0 0\n")
    assert resolve(root2) == "mmcblk2|b|/dev/mmcblk2p12"

print("PASS: shipped FCC resolver follows the published Linux slot, discovers the eMMC by MMC type, selects matching DTBO, and verifies the boot-selected persistent mount")
