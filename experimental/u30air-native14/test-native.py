#!/usr/bin/env python3
"""Run actual native command and transaction scripts against isolated fake sysfs/disks."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
BUILD = Path('/root/u30air-native12-build-20261005')
KREL = '7.2.8-u30air-native12'
U30_FIRMWARE = (
    'gnssmodem.bin', 'wcnmodem.bin', 'wifi_board_config.ini',
    'wifi_board_config_ab.ini', 'bt_configure_pskey.ini', 'bt_configure_rf.ini',
)


def command_tests():
    binary = BUILD / 'mu300-usb'
    with tempfile.TemporaryDirectory(prefix='mu300-native-command-') as folder:
        root = Path(folder)
        env = dict(os.environ, MU300_SYSROOT=folder)
        role = root / 'sys/class/usb_role/dwc3/role'
        role.parent.mkdir(parents=True)
        role.write_text('device\n')
        def run(cmd):
            return subprocess.run([str(binary), cmd], env=env, capture_output=True, text=True)
        assert run('host').returncode == 1
        assert role.read_text() == 'device\n'
        assert run('status').returncode == 0
        assert run('device').returncode == 0
        charger = root / 'sys/bus/i2c/devices/3-006b'
        charger.mkdir(parents=True)
        (charger / 'driver').symlink_to('../../drivers/sgm41511-native')
        (charger / 'data_role').write_text('device\n')
        (charger / 'otg_boost').write_text('0\n')
        (charger / 'input_power_good').write_text('1\n')
        psy = root / 'sys/class/power_supply/sgm41511-charger'
        psy.mkdir(parents=True)
        (psy / 'status').write_text('Charging\n')
        usb = root / 'sys/bus/usb/devices/2-1'
        usb.mkdir(parents=True)
        for name, value in [('product', 'Realtek USB LAN'), ('idVendor', '0bda'), ('idProduct', '8153')]:
            (usb / name).write_text(value + '\n')
        result = run('host')
        assert result.returncode == 0, result.stderr
        assert (charger / 'data_role').read_text() == 'host\n'
        assert role.read_text() == 'device\n', 'command must use protected charger API, not bypass it'
        assert (charger / 'otg_boost').read_text() == '0\n'
        status = run('status').stdout
        assert 'role: host' in status and 'OTG boost): off' in status
        assert 'charger: Charging' in status and '0bda:8153' in status
        assert run('host-external').returncode == 0
        assert run('auto').returncode == 0
        assert (charger / 'data_role').read_text() == 'auto\n'
        assert run('device').returncode == 0
        assert (charger / 'data_role').read_text() == 'device\n'
        (charger / 'data_role').unlink()
        (charger / 'data_role').symlink_to('/dev/full')
        assert run('host').returncode == 1, 'kernel write failure must propagate'
        assert run('not-a-command').returncode == 2
    print('Native command: missing driver, safe API, status, Device recovery and write failure passed')


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def transaction_test(case):
    with tempfile.TemporaryDirectory(prefix='mu300-native-transaction-') as folder:
        root = Path(folder)
        disk = root / 'disk'
        old = disk / 'openwrt'
        pkg = root / 'package'
        fakebin = root / 'bin'
        pkg.mkdir()
        fakebin.mkdir()
        for k in ['etc/config', 'etc/mu300', 'etc/dropbear', 'etc/rc.d', 'opt/mu300/bin', 'lib/modules/7.2.8', 'lib/firmware', 'root']:
            (old / k).mkdir(parents=True, exist_ok=True)
        (old / 'etc/openwrt_release').write_text('OpenWrt test fixture\n')
        (old / 'etc/mu300-device').write_text('u30air\n')
        if case == 'device-cache-absent':
            (old / 'etc/mu300-device').unlink()
        for name in ['passwd', 'shadow', 'group']:
            (old / 'etc' / name).write_text('original-account-fixture\n')
        (old / 'etc/config/network').write_text("config device\n option name 'br-lan'\n option type 'bridge'\n list ports 'usb0'\n list ports 'rndis0'\nconfig interface 'lan'\n option device 'br-lan'\n option ipaddr '192.168.78.1'\n")
        original_network = (old / 'etc/config/network').read_bytes()
        (old / 'etc/dropbear/dropbear_ed25519_host_key').write_bytes(b'FAKE-KEY-TEST-ONLY')
        (old / 'lib/firmware/vendor-test.bin').write_bytes(b'VENDOR-FIXTURE')
        (old / 'lib/firmware/rtl_nic').mkdir(parents=True)
        (old / 'lib/firmware/rtl_nic/rtl8153b-2.fw').write_bytes(b'OLD-RTL8153B-FIRMWARE')
        (old / 'lib/modules/7.2.8/old.ko').write_bytes(b'OLD-MODULE-FIXTURE')
        android = old / 'opt/mu300/android'
        for name in ('apex/com.android.runtime/bin', 'dev-properties', 'linkerconfig',
                     'odm', 'product', 'system/bin', 'system_ext', 'vendor/bin', 'vendor_dlkm', 'data'):
            (android / name).mkdir(parents=True, exist_ok=True)
        executable(android / 'apex/com.android.runtime/bin/linker64', '#!/bin/sh\nexit 0\n')
        executable(android / 'vendor/bin/modem_control', '#!/bin/sh\nexit 0\n')
        (android / 'dev-properties/build.prop').write_text('U30-AIR-DEVICE-FIXTURE\n')
        (android / 'linkerconfig/ld.config.txt').write_bytes(b'')
        (android / 'system/bin/system-fixture').write_text('U30-AIR-SYSTEM-FIXTURE\n')
        for name in U30_FIRMWARE:
            (old / 'lib/firmware' / name).write_bytes(('U30-FIRMWARE-' + name).encode())
        executable(old / 'opt/mu300/bin/mu300-device', '#!/bin/sh\necho u30air\n')
        if case != 'metadata-absent':
            (disk / 'boot').mkdir()
            (disk / 'boot/kernel').write_text('7.2\n')
            (disk / 'boot/old-marker').write_text('OLD BOOT METADATA\n')
        (disk / 'ubuntu/lib/modules').mkdir(parents=True)
        blockdir = root / 'dev/block'
        blockdir.mkdir(parents=True)
        boot = blockdir / 'fakeboot'
        dtbo = blockdir / 'fakedtbo'
        boot.write_bytes(b'A' * 65536)
        dtbo.write_bytes(b'D' * 8192)
        for name, label, sectors in [('fakeboot', 'boot_b', 128), ('fakedtbo', 'dtbo_b', 16)]:
            sys = root / 'sys/class/block' / name
            sys.mkdir(parents=True)
            (sys / 'uevent').write_text('PARTNAME=' + label + '\n')
            (sys / 'size').write_text(str(sectors) + '\n')
            (root / 'dev' / name).symlink_to(blockdir / name)
        offset = root / 'sys/class/block/loop-test/loop'
        offset.mkdir(parents=True)
        (offset / 'offset').write_text('27762098176\n')
        (root / 'mounts').write_text(f'/dev/loop-test {disk} ext4 rw 0 0\n')
        dtbo_hash = hashlib.sha256(dtbo.read_bytes()).hexdigest()
        def translate(text):
            text = text.replace('DISK=/mnt/mu300-disk', f'DISK={disk}')
            text = text.replace('BIN=/opt/mu300/bin', f'BIN={old}/opt/mu300/bin')
            text = text.replace('[ -d "$OLD" ] && [ / -ef "$OLD" ]', '[ -d "$OLD" ]')
            text = text.replace('[ -f /etc/openwrt_release ]', f'[ -f {old}/etc/openwrt_release ]')
            text = text.replace('/sys/class/block', str(root / 'sys/class/block'))
            text = text.replace('/proc/mounts', str(root / 'mounts'))
            text = text.replace("printf '/dev/%s", f"printf '{root}/dev/%s")
            text = text.replace('boot_dev=/dev/', f'boot_dev={root}/dev/')
            text = text.replace('67108864', '65536').replace('8388608', '8192').replace('131072', '128')
            text = text.replace('581020c762acbce7dcc52a540860410f7c93752bec990e2b6b5e1cbb6b418d39', dtbo_hash)
            return text
        for name in ['install-device.sh', 'rollback-device.sh']:
            executable(pkg / name, translate((HERE / name).read_text()))
        executable(fakebin / 'uname', '#!/bin/sh\necho 7.2.8\n')
        executable(fakebin / 'uci', '''#!/usr/bin/python3
import pathlib, sys
a=sys.argv[1:]; config=None
for opt in ['-c','-t']:
 if opt in a:
  i=a.index(opt)
  if opt=='-c': config=pathlib.Path(a[i+1])
  del a[i:i+2]
a=[x for x in a if x!='-q']
if a[0]=='changes': pass
elif a[0]=='show': print("network.@device[0].name='br-lan'")
elif a[0]=='get': print('br-lan' if a[1]=='network.lan.device' else 'bridge')
elif a[0]=='add_list':
 with (config/'network').open('a') as f: f.write(" list ports 'eth0'\\n")
elif a[0] in ['del_list','commit']: pass
else: sys.exit(2)
''')
        executable(fakebin / 'mv', '''#!/usr/bin/python3
import os,sys
if os.getenv('FAIL_RENAME')=='1' and sys.argv[-2].endswith('/openwrt.native12-new') and sys.argv[-1].endswith('/openwrt'):
 sys.exit(66)
os.execv('/bin/mv',['mv']+sys.argv[1:])
''')
        executable(pkg / 'mu300-update', f'''#!/bin/sh
set -eu
printf 'NEW-BOOT' | dd of={boot} conv=notrunc 2>/dev/null
mkdir -p "$MU300_DISK/boot" "$MU300_DISK/openwrt/lib/modules/{KREL}" "$MU300_DISK/ubuntu/lib/modules/{KREL}"
echo new > "$MU300_DISK/boot/new-marker"
echo extra > "$MU300_DISK/openwrt/lib/modules/{KREL}/new.ko"
if [ "${{FAIL_UPDATER:-0}}" = 1 ]; then exit 23; fi
''')
        newbase = root / 'image'
        for k in ['sbin', 'etc/mu300', 'etc/config', 'etc/rc.d', 'etc/uci-defaults',
                  f'lib/modules/{KREL}', 'lib/firmware/rtl_nic', 'opt/mu300/bin',
                  'opt/mu300/android/apex/com.android.runtime/bin', 'opt/mu300/android/dev-properties',
                  'opt/mu300/android/linkerconfig', 'opt/mu300/android/system/bin',
                  'opt/mu300/android/vendor/bin']:
            (newbase / k).mkdir(parents=True, exist_ok=True)
        executable(newbase / 'sbin/init', '#!/bin/sh\nexit 0\n')
        executable(newbase / 'opt/mu300/bin/mu300-usb', '#!/bin/sh\nexit 0\n')
        (newbase / 'etc/mu300/image-version').write_text('u30air-native12-android-charge-2026.10.05-test\n')
        (newbase / 'etc/uci-defaults/90-mu300').write_text('would reset networking\n')
        (newbase / 'lib/firmware/rtl_nic/rtl8153b-2.fw').write_bytes(b'NEW-FIRMWARE-FIXTURE')
        (newbase / 'lib/modules' / KREL / 'new.ko').write_bytes(b'NEW-MODULE')
        (newbase / 'opt/mu300/android/apex/com.android.runtime/bin/linker64').write_text('PACKAGE-PLACEHOLDER\n')
        (newbase / 'opt/mu300/android/dev-properties/package-placeholder').write_text('PACKAGE-PLACEHOLDER\n')
        (newbase / 'opt/mu300/android/linkerconfig/ld.config.txt').write_text('PACKAGE-PLACEHOLDER\n')
        (newbase / 'opt/mu300/android/system/bin/system-fixture').write_text('PACKAGE-PLACEHOLDER\n')
        (newbase / 'opt/mu300/android/vendor/bin/modem_control').write_text('PACKAGE-PLACEHOLDER\n')
        for name in U30_FIRMWARE:
            (newbase / 'lib/firmware' / name).write_bytes(b'PACKAGE-PLACEHOLDER')
        with tarfile.open(pkg / 'mu300-openwrt-rootfs.tar.gz', 'w:gz') as tf:
            tf.add(newbase, arcname='.')
        (pkg / 'mu300-kernel-7.2.8-u30air-native12.tar.gz').write_bytes(b'KERNEL-FIXTURE')
        files = [p for p in pkg.iterdir() if p.is_file()]
        (pkg / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in files))
        if case == 'checksum-refusal':
            (pkg / 'mu300-openwrt-rootfs.tar.gz').write_bytes(b'CORRUPT')
        if case == 'missing-platform':
            (old / 'lib/firmware/wcnmodem.bin').unlink()
        env = dict(os.environ, PATH=str(fakebin) + ':' + os.environ['PATH'])
        if case == 'updater-failure': env['FAIL_UPDATER'] = '1'
        if case == 'rename-failure': env['FAIL_RENAME'] = '1'
        result = subprocess.run(['sh', str(pkg / 'install-device.sh')], env=env, capture_output=True, text=True)
        if case == 'checksum-refusal':
            assert result.returncode != 0 and boot.read_bytes() == b'A' * 65536
            assert not (disk / '.mu300-native12-rollback').exists()
            assert not (disk / 'openwrt.native12-new').exists()
        elif case == 'missing-platform':
            assert result.returncode != 0 and 'wcnmodem.bin' in result.stderr
            assert boot.read_bytes() == b'A' * 65536
            assert not (disk / '.mu300-native12-rollback').exists()
            assert not (disk / 'openwrt.native12-new').exists()
        elif case in ['updater-failure', 'rename-failure']:
            assert result.returncode != 0, result.stdout + result.stderr
            assert boot.read_bytes() == b'A' * 65536, result.stdout + result.stderr
            assert (old / 'etc/config/network').read_bytes() == original_network
            assert not (old / 'lib/modules' / KREL).exists()
            assert (disk / 'boot/old-marker').exists()
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            assert (old / 'etc/config/network').read_bytes() == original_network
            assert (old / 'etc/dropbear/dropbear_ed25519_host_key').read_bytes() == b'FAKE-KEY-TEST-ONLY'
            assert not (old / 'etc/uci-defaults/90-mu300').exists()
            assert (old / 'lib/firmware/vendor-test.bin').exists()
            assert (old / 'lib/firmware/rtl_nic/rtl8153b-2.fw').exists()
            assert (old / 'lib/firmware/rtl_nic/rtl8153b-2.fw').read_bytes() == b'NEW-FIRMWARE-FIXTURE'
            for name in U30_FIRMWARE:
                assert (old / 'lib/firmware' / name).read_bytes() == ('U30-FIRMWARE-' + name).encode()
            assert (old / 'opt/mu300/android/vendor/bin/modem_control').read_text() == '#!/bin/sh\nexit 0\n'
            assert (old / 'opt/mu300/android/apex/com.android.runtime/bin/linker64').read_text() == '#!/bin/sh\nexit 0\n'
            assert (old / 'opt/mu300/android/dev-properties/build.prop').read_text() == 'U30-AIR-DEVICE-FIXTURE\n'
            assert (old / 'opt/mu300/android/linkerconfig/ld.config.txt').read_bytes() == b''
            assert (old / 'opt/mu300/android/system/bin/system-fixture').read_text() == 'U30-AIR-SYSTEM-FIXTURE\n'
            backup = next((disk / '.mu300-native12-rollback').iterdir())
            result = subprocess.run(['sh', str(backup / 'rollback.sh'), str(backup)], env=env, capture_output=True, text=True)
            assert result.returncode == 0, result.stdout + result.stderr
            assert boot.read_bytes() == b'A' * 65536
            assert (old / 'etc/config/network').read_bytes() == original_network
            assert not (old / 'lib/modules' / KREL).exists()
            if case == 'metadata-absent': assert not (disk / 'boot').exists()
            else: assert (disk / 'boot/old-marker').exists()
        print(f'Transaction case {case}: passed')


if __name__ == '__main__':
    subprocess.run([str(BUILD / 'test-host-policy')], check=True)
    command_tests()
    for case in ['success-and-rollback', 'updater-failure', 'rename-failure', 'checksum-refusal', 'missing-platform', 'metadata-absent', 'device-cache-absent']:
        transaction_test(case)
    print('All native firmware simulation checks passed; hardware remains untested.')
