#!/usr/bin/env python3
"""Exercise the shipped FCC persistence codec against a temporary store."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[3]
library = root / "rootfs/overlay/opt/mu300/lib/fcc-record.sh"


def invoke(script, *args):
    return subprocess.run(
        ["sh", "-c", '. "$1"; shift; ' + script, "sh", str(library),
         *map(str, args)], text=True, capture_output=True
    )


with tempfile.TemporaryDirectory(prefix="mu300-fcc-record-") as tmp:
    directory = Path(tmp) / "store"
    directory.mkdir(mode=0o700)
    record = directory / "learned-v1"
    identity = "a" * 64
    dtbo = "b" * 64
    write = invoke("fcc_record_write \"$1\" \"$2\" \"$3\" 3500 2048 3400",
                   record, identity, dtbo)
    assert write.returncode == 0, write.stderr
    assert record.stat().st_mode & 0o777 == 0o600

    read = invoke("fcc_record_read \"$1\" \"$2\" \"$3\" 3500 2048",
                  record, identity, dtbo)
    assert read.returncode == 0 and read.stdout.strip() == "3400"

    for other_identity, other_dtbo, design, gain in (
        ("c" * 64, dtbo, "3500", "2048"),
        (identity, "d" * 64, "3500", "2048"),
        (identity, dtbo, "3600", "2048"),
        (identity, dtbo, "3500", "2049"),
    ):
        rejected = invoke("fcc_record_read \"$1\" \"$2\" \"$3\" \"$4\" \"$5\"",
                          record, other_identity, other_dtbo, design, gain)
        assert rejected.returncode != 0

    original = record.read_bytes()
    parts = original.decode().strip().split("|")
    parts[5] = "4000"  # outside the model/design ratio guard
    record.write_text("|".join(parts) + "\n")
    rejected = invoke("fcc_record_read \"$1\" \"$2\" \"$3\" 3500 2048",
                      record, identity, dtbo)
    assert rejected.returncode != 0
    rejected = invoke("fcc_record_write \"$1\" \"$2\" \"$3\" 3500 2048 4000",
                      record, identity, dtbo)
    assert rejected.returncode != 0

    record.write_bytes(original)
    tampered = record.read_text().replace("|3400|", "|3300|")
    record.write_text(tampered)
    rejected = invoke("fcc_record_read \"$1\" \"$2\" \"$3\" 3500 2048",
                      record, identity, dtbo)
    assert rejected.returncode != 0

    target = directory / "target"
    target.write_bytes(original)
    link = directory / "linked-record"
    link.symlink_to(target)
    rejected = invoke("fcc_record_read \"$1\" \"$2\" \"$3\" 3500 2048",
                      link, identity, dtbo)
    assert rejected.returncode != 0
    rejected = invoke("fcc_record_write \"$1\" \"$2\" \"$3\" 3500 2048 3400",
                      link, identity, dtbo)
    assert rejected.returncode != 0

    linked_store = Path(tmp) / "linked-store"
    linked_store.symlink_to(directory, target_is_directory=True)
    rejected = invoke("fcc_record_write \"$1\" \"$2\" \"$3\" 3500 2048 3400",
                      linked_store / "record", identity, dtbo)
    assert rejected.returncode != 0

print("PASS: shipped FCC record write/read, mode, identity binding, CRC and symlink/range guards")
