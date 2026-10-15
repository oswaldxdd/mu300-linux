"""Positive and negative checks for the r3 kernel archive reuse boundary."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "tools/build-charge2-sae-offload-kernel.py"
SPEC = importlib.util.spec_from_file_location("r3_kernel_builder_test", BUILDER)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load kernel builder: {BUILDER}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class KernelBundleGuard(unittest.TestCase):
    def make_archive(self, path: Path, *, image: bytes, module: bytes,
                     extra: bool = False, image_mode: int = 0o644,
                     image_mtime: int = 1_700_000_000) -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name, payload, mode in (
                ("./Image", image, image_mode),
                ("./modules/sprd_wlan_combo.ko", module, 0o644),
                ("./modules/other.ko", b"same-module", 0o644),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = mode
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                info.mtime = image_mtime if name == "./Image" else 1_700_000_000
                archive.addfile(info, io.BytesIO(payload))
            if extra:
                payload = b"unexpected"
                info = tarfile.TarInfo("./unexpected")
                info.size = len(payload)
                info.mode = 0o644
                info.uid = info.gid = 0
                info.mtime = 1_700_000_000
                archive.addfile(info, io.BytesIO(payload))

    def test_accepts_only_wifi_module_content_change(self):
        with tempfile.TemporaryDirectory() as temp:
            base, candidate = Path(temp) / "base.tar.gz", Path(temp) / "candidate.tar.gz"
            self.make_archive(base, image=b"image", module=b"old-wifi")
            self.make_archive(candidate, image=b"image", module=b"new-wifi")
            comparison = MODULE.compare_bundles(base, candidate)
            self.assertEqual(comparison["changed_members"], ["./modules/sprd_wlan_combo.ko"])
            self.assertEqual(len(comparison["members"]), 3)

    def test_rejects_changed_image_bytes(self):
        self.assert_rejected(image=b"different", module=b"new-wifi")

    def test_rejects_changed_image_metadata(self):
        self.assert_rejected(image=b"image", module=b"new-wifi", image_mode=0o600)

    def test_rejects_changed_image_mtime(self):
        self.assert_rejected(image=b"image", module=b"new-wifi",
                             image_mtime=1_700_000_001)

    def test_rejects_added_archive_member(self):
        self.assert_rejected(image=b"image", module=b"new-wifi", extra=True)

    def assert_rejected(self, *, image: bytes, module: bytes,
                        extra: bool = False, image_mode: int = 0o644,
                     image_mtime: int = 1_700_000_000) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base, candidate = Path(temp) / "base.tar.gz", Path(temp) / "candidate.tar.gz"
            self.make_archive(base, image=b"image", module=b"old-wifi")
            self.make_archive(candidate, image=image, module=module,
                              extra=extra, image_mode=image_mode, image_mtime=image_mtime)
            with self.assertRaises(RuntimeError):
                MODULE.compare_bundles(base, candidate)


if __name__ == "__main__":
    unittest.main()
