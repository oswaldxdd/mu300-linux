"""Hardware-free tests for the panel1 radio-lock, AT-drainer and USB-role contracts."""
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[1]
MOBILE = TOP / "rootfs/overlay/opt/mu300/bin/mobile-data"
ATD = TOP / "rootfs/overlay/opt/mu300/bin/mu300-atd"
DEVICE_USB = TOP / "openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/device-usb"
PANEL_LIB = TOP / "openwrt/luci-app-mu300/root/usr/share/unisoc-modem/lib.sh"


@unittest.skipUnless(os.name == "posix" and Path("/proc/stat").exists(), "requires Linux /proc")
class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mu300-panel-contract-"))
        self.radio = self.tmp / "radio"
        self.env = dict(os.environ)
        self.env.pop("MU300_RADIO_LOCK_WAIT", None)
        self.env["MU300_RADIO_LOCK_DIR"] = str(self.radio)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_mobile(self, *args, env=None, timeout=8):
        e = dict(self.env)
        if env:
            e.update({k: str(v) for k, v in env.items()})
        return subprocess.run([str(MOBILE), *map(str, args)], env=e, text=True,
                              capture_output=True, timeout=timeout)

    def wait_owner(self, timeout=3):
        owner = self.radio / "lock" / "owner"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if owner.is_file():
                return owner
            time.sleep(0.02)
        self.fail("radio lock owner was not published")

    def test_idle_busy_and_default_wait_zero(self):
        self.assertNotEqual(self.run_mobile("radio-busy").returncode, 0)
        holder = subprocess.Popen([str(MOBILE), "radio-locked", "/bin/bash", "-c", "sleep 1.0"],
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.wait_owner()
            self.assertEqual(self.run_mobile("radio-busy").returncode, 0)
            start = time.monotonic()
            blocked = self.run_mobile("radio-locked", "/bin/true")
            elapsed = time.monotonic() - start
            self.assertEqual(blocked.returncode, 75, blocked.stderr)
            self.assertLess(elapsed, 0.75, "omitted wait must default to zero")
        finally:
            holder.communicate(timeout=4)

    def test_waits_for_lock_release(self):
        holder = subprocess.Popen([str(MOBILE), "radio-locked", "/bin/bash", "-c", "sleep 0.45"],
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.wait_owner()
            waited = self.run_mobile("radio-locked", "/bin/true", env={"MU300_RADIO_LOCK_WAIT": "4"})
            self.assertEqual(waited.returncode, 0, waited.stderr)
        finally:
            holder.communicate(timeout=4)

    def test_subcommand_exit_status_is_propagated(self):
        result = self.run_mobile("radio-locked", "/bin/bash", "-c", "exit 37")
        self.assertEqual(result.returncode, 37, result.stderr)

    def test_stale_nonexistent_pid_is_reaped(self):
        lock = self.radio / "lock"
        lock.mkdir(parents=True)
        (lock / "owner").write_text("999999999 1\n")
        self.assertNotEqual(self.run_mobile("radio-busy").returncode, 0)
        result = self.run_mobile("radio-locked", "/bin/true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(lock.exists())

    def test_reused_pid_with_wrong_start_token_is_reaped(self):
        stat = Path(f"/proc/{os.getpid()}/stat").read_text()
        fields_after_comm = stat[stat.rfind(")") + 2:].split()
        actual_start = int(fields_after_comm[19])  # stat field 22 (starttime)
        lock = self.radio / "lock"
        lock.mkdir(parents=True)
        (lock / "owner").write_text(f"{os.getpid()} {actual_start + 1}\n")
        self.assertNotEqual(self.run_mobile("radio-busy").returncode, 0)
        result = self.run_mobile("radio-locked", "/bin/true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(lock.exists())

    def test_nested_up_radio_on_takes_one_lock(self):
        source = MOBILE.read_text()
        begin = source.index("# Serialize dial/recovery and panel radio resets")
        end = source.index('case "${1:-status}" in', begin)
        definitions = source[begin:end]
        marker = self.tmp / "radio-on-called"
        harness = (
            "set -euo pipefail\n" + definitions +
            "\nup_unlocked() { radio_on; }\n"
            "radio_on_unlocked() { printf 'called\\n' >> \"$MU300_RADIO_TEST_MARKER\"; }\n"
            "up\n"
        )
        e = dict(self.env, MU300_RADIO_LOCK_WAIT="0", MU300_RADIO_TEST_MARKER=str(marker))
        result = subprocess.run(["/bin/bash", "-c", harness], env=e, text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text().splitlines(), ["called"])

    def test_urc_empty_list_means_none_and_unset_defaults_to_zero(self):
        line = next(line.strip() for line in ATD.read_text().splitlines()
                    if line.strip().startswith("for n in ${MU300_AT_URC_CHANNELS"))
        match = re.fullmatch(r"for n in (.+); do", line)
        self.assertIsNotNone(match, line)
        expression = match.group(1)
        empty = subprocess.run(["/bin/bash", "-c",
                                f'set -u; MU300_AT_URC_CHANNELS=""; for n in {expression}; do printf "%s\\n" "$n"; done'],
                               text=True, capture_output=True, timeout=3)
        self.assertEqual((empty.returncode, empty.stdout), (0, ""))
        unset = subprocess.run(["/bin/bash", "-c",
                                f'unset MU300_AT_URC_CHANNELS; set -u; for n in {expression}; do printf "%s\\n" "$n"; done'],
                               text=True, capture_output=True, timeout=3)
        self.assertEqual((unset.returncode, unset.stdout), (0, "0\n"))

    def make_usb_fixture(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        uci = bin_dir / "uci"
        uci.write_text("""#!/bin/sh
[ "$1" = -q ] && shift
[ "$1" = get ] || exit 2
case "$2" in
  unisoc_modem.usb.role_auto|unisoc_modem.usb.net_auto) echo 0 ;;
  unisoc_modem.usb.net_mode) echo ncm ;;
  unisoc_modem.usb.net_scope) echo permanent ;;
  *) exit 1 ;;
esac
""")
        uci.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ.get('PATH', '')}")
        return env

    def run_usb_get(self, script, env):
        result = subprocess.run([str(script), "get"], env=env, text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_unsupported_usb_net_rejects_before_uci_write(self):
        source = DEVICE_USB.read_text().replace(
            "LIB=${0%/*}/../../share/unisoc-modem/lib.sh",
            f"LIB={PANEL_LIB}"
        )
        script = self.tmp / "device-usb-unsupported"
        script.write_text(source)
        script.chmod(0o755)
        role = self.tmp / "role"
        role.write_text("device\n")
        bin_dir = self.tmp / "bin-unsupported"
        bin_dir.mkdir()
        uci = bin_dir / "uci"
        uci.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_UCI_LOG"
[ "$1" = -q ] && shift
case "$1" in
  get) case "$2" in unisoc_modem.usb.role_auto) echo 0 ;; *) exit 1 ;; esac ;;
  *) exit 0 ;;
esac
""")
        uci.chmod(0o755)
        log = self.tmp / "uci.log"
        boot_file = self.tmp / "etc/mu300/usb-net"
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ.get('PATH', '')}",
                   MU300_USB_ROLE_FILE=str(role), MU300_USB_NET_SUPPORTED="0",
                   MU300_USB_NET_FILE=str(boot_file), STUB_UCI_LOG=str(log))
        result = subprocess.run([str(script), "set-net", "rndis", "once", "1"],
                                env=env, text=True, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        response = json.loads(result.stdout)
        self.assertEqual(response["ok"], 0)
        self.assertIn("not supported", response["error"])
        self.assertFalse(log.exists(), "unsupported API must reject before any UCI read or write")
        self.assertFalse(boot_file.exists(), "unsupported API must not persist a dead-end boot setting")

    def test_usb_role_env_override_takes_precedence(self):
        source = DEVICE_USB.read_text().replace(
            "LIB=${0%/*}/../../share/unisoc-modem/lib.sh",
            f"LIB={PANEL_LIB}"
        )
        fixture_glob = self.tmp / "sys" / "class" / "usb_role" / "*" / "role"
        source = source.replace("for candidate in /sys/class/usb_role/*/role; do",
                                f"for candidate in {fixture_glob}; do")
        script = self.tmp / "device-usb"
        script.write_text(source)
        script.chmod(0o755)
        discovered = self.tmp / "sys/class/usb_role/switch0/role"
        override = self.tmp / "override/role"
        discovered.parent.mkdir(parents=True)
        override.parent.mkdir(parents=True)
        discovered.write_text("host\n")
        override.write_text("device\n")
        env = self.make_usb_fixture()
        env["MU300_USB_ROLE_FILE"] = str(override)
        data = self.run_usb_get(script, env)
        self.assertEqual(data["role"], "device")
        self.assertEqual(data["host_supported"], 1)

    def test_usb_role_auto_discovers_single_switch(self):
        source = DEVICE_USB.read_text().replace(
            "LIB=${0%/*}/../../share/unisoc-modem/lib.sh",
            f"LIB={PANEL_LIB}"
        )
        fixture_glob = self.tmp / "sys" / "class" / "usb_role" / "*" / "role"
        source = source.replace("for candidate in /sys/class/usb_role/*/role; do",
                                f"for candidate in {fixture_glob}; do")
        script = self.tmp / "device-usb-auto"
        script.write_text(source)
        script.chmod(0o755)
        role = self.tmp / "sys/class/usb_role/switch0/role"
        role.parent.mkdir(parents=True)
        role.write_text("host\n")
        env = self.make_usb_fixture()
        env.pop("MU300_USB_ROLE_FILE", None)
        data = self.run_usb_get(script, env)
        self.assertEqual(data["role"], "host")
        self.assertEqual(data["host_supported"], 1)


if __name__ == "__main__":
    unittest.main()
