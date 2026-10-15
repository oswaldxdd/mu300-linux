import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


TOP = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TEST_BUILDER = load_module("charge2_test_builder_for_wrapper_test", TOP / "tools/build-charge2-test.py")
RELEASE_BUILDER = load_module("charge2_release_builder_for_wrapper_test", TOP / "tools/build-charge2-release.py")


class OfflineWrapperReleaseIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
        if not cls.pwsh:
            raise unittest.SkipTest("PowerShell 7 is required for offline-wrapper fixtures")

    def run_verify(self, wrapper_text, manifest, tamper=False):
        with tempfile.TemporaryDirectory(prefix="mu300-wrapper-fixture-", dir=TOP) as temp:
            package = Path(temp)
            release = package / "release"
            release.mkdir()
            names = {
                "mu300-kernel.tar.gz",
                "mu300-kernel-7.2.tar.gz",
                "mu300-openwrt-luci-rootfs.tar.gz",
                "mu300-update",
                "mu300-native-kmod-compat-7.2.9-r1.apk",
                "BUILD-MANIFEST.json",
            }
            if "mu300-native-kmod-compat-7.2.9-r1.apk" not in wrapper_text:
                names.remove("mu300-native-kmod-compat-7.2.9-r1.apk")
            for name in names - {"BUILD-MANIFEST.json"}:
                (release / name).write_bytes(("fixture:" + name).encode())
            (release / "BUILD-MANIFEST.json").write_text(
                json.dumps(manifest) + "\n", encoding="utf-8"
            )
            lines = []
            for name in sorted(names):
                digest = hashlib.sha256((release / name).read_bytes()).hexdigest()
                lines.append(f"{digest}  {name}")
            (release / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii")
            if tamper:
                with (release / "mu300-kernel-7.2.tar.gz").open("ab") as stream:
                    stream.write(b"tampered")
            wrapper = package / "install-u30air-host.ps1"
            wrapper.write_text(wrapper_text, encoding="utf-8-sig", newline="\r\n")
            return subprocess.run(
                [self.pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(wrapper), "-VerifyOnly"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )

    def test_test_wrapper_accepts_only_test_candidate_manifest(self):
        valid = self.run_verify(TEST_BUILDER.host_wrapper(), {
            "release_id": "charge2-test1-20261012", "test_candidate": True,
        })
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        wrong = self.run_verify(TEST_BUILDER.host_wrapper(), {
            "release_id": "v2026.10.15-charge2-r2", "test_candidate": False,
        })
        self.assertNotEqual(wrong.returncode, 0)

    def test_formal_wrapper_accepts_only_formal_release_and_checks_hashes(self):
        wrapper = RELEASE_BUILDER.host_wrapper()
        valid = self.run_verify(wrapper, {
            "release_id": "v2026.10.15-charge2-r2",
            "release_channel": "stable",
            "test_candidate": False,
            "kernel_release": "7.2.9-u30air-charge2",
        })
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        wrong = self.run_verify(wrapper, {
            "release_id": "charge2-test1-20261012",
            "release_channel": "test",
            "test_candidate": True,
            "kernel_release": "7.2.9-u30air-charge2",
        })
        self.assertNotEqual(wrong.returncode, 0)
        tampered = self.run_verify(wrapper, {
            "release_id": "v2026.10.15-charge2-r2",
            "release_channel": "stable",
            "test_candidate": False,
            "kernel_release": "7.2.9-u30air-charge2",
        }, tamper=True)
        self.assertNotEqual(tampered.returncode, 0)

    def run_source_installer_verify(self, manifest, tamper=False):
        with tempfile.TemporaryDirectory(prefix="mu300-installer-fixture-", dir=TOP) as temp:
            release = Path(temp) / "release"
            release.mkdir()
            names = ("mu300-kernel.tar.gz", "mu300-kernel-7.2.tar.gz",
                     "mu300-openwrt-luci-rootfs.tar.gz", "BUILD-MANIFEST.json")
            for name in names[:-1]:
                (release / name).write_bytes(("fixture:" + name).encode())
            (release / names[-1]).write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            lines = [f"{hashlib.sha256((release / name).read_bytes()).hexdigest()}  {name}"
                     for name in names]
            (release / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii")
            if tamper:
                with (release / "mu300-kernel-7.2.tar.gz").open("ab") as stream:
                    stream.write(b"tampered")
            return subprocess.run(
                [self.pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(TOP / "install.ps1"),
                 "-U30AirHostPackage", "-NoSelfUpdate", "-VerifyOnly", "-Lang", "en",
                 "-ReleaseDirectory", str(release)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )

    def test_source_installer_loads_verified_release_id_and_rejects_unknown_manifest(self):
        formal = {
            "release_id": "v2026.10.15-charge2-r2", "release_channel": "stable",
            "test_candidate": False,
        }
        result = self.run_source_installer_verify(formal)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("v2026.10.15-charge2-r2", result.stdout)

        test = {"release_id": "charge2-test1-20261012", "test_candidate": True}
        result = self.run_source_installer_verify(test)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        wrong = {"release_id": "v2026.10.15-charge2", "release_channel": "stable",
                 "test_candidate": False}
        self.assertNotEqual(self.run_source_installer_verify(wrong).returncode, 0)
        self.assertNotEqual(self.run_source_installer_verify(formal, tamper=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
