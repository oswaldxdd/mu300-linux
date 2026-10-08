#!/usr/bin/env python3
from pathlib import Path
import hashlib
import io
import json
import tarfile
import zipfile

HERE=Path(__file__).resolve().parent
release=HERE/'release'
def digest(data): return hashlib.sha256(data).hexdigest()
for line in (release/'SHA256SUMS').read_text().splitlines():
    expected,name=line.split('  ',1)
    assert digest((release/name).read_bytes())==expected,name
info=json.loads((release/'delivery.json').read_text())
archive=release/info['archive']
assert digest(archive.read_bytes())==info['sha256']
with tarfile.open(archive) as tf:
    prefix='package/'
    def read(name): return tf.extractfile(prefix+name).read()
    for line in read('SHA256SUMS').decode().splitlines():
        expected,name=line.split('  ',1)
        assert digest(read(name))==expected,name
    installer=read('install-device.sh').decode()
    for required in ('apex', 'dev-properties', 'linkerconfig', 'system', 'vendor',
                     'gnssmodem.bin', 'wcnmodem.bin', 'wifi_board_config.ini',
                     'wifi_board_config_ab.ini', 'bt_configure_pskey.ini', 'bt_configure_rf.ini'):
        assert required in installer,required
    assert 'cp -a "$OLD/lib/firmware/." "$NEW/lib/firmware/"' in installer
    assert 'rtl_fw_saved' in installer
    assert 'cp -an "$OLD/opt/mu300/android' not in installer
    manifest=json.loads(read('BUILD-MANIFEST.json'))
    assert manifest['hardware_boot_tested'] is False
    assert manifest['hardware_charging_tested'] is False
    config=read('kernel.config').decode()
    assert 'CONFIG_CHARGER_SGM41511_NATIVE=y' in config
    assert 'CONFIG_USB_RTL8152=y' in config
    for s in ('CONFIG_USB_SC27XX_TYPEC=y','CONFIG_SC27XX_PD=y','CONFIG_SPRD_TYPEC_TCPM=y'):
        assert s in config
    with tarfile.open(fileobj=io.BytesIO(read('mu300-kernel-7.2.8-u30air-native14.tar.gz'))) as kernel, \
         tarfile.open(fileobj=io.BytesIO(read('mu300-openwrt-rootfs.tar.gz'))) as root:
        assert kernel.extractfile('./devices').read().strip()==b'u30air'
        krel=kernel.extractfile('./kernel.release').read().decode().strip()
        assert krel=='7.2.8-u30air-native14'
        assert root.extractfile('./etc/mu300/image-version').read().strip()==b'u30air-native14-android-charge-2026.10.05-test'
        binary=root.extractfile('./opt/mu300/bin/mu300-usb').read()
        assert binary[:4]==b'\x7fELF' and binary[18:20]==b'\xb7\x00'
        count=0
        for entry in kernel.getmembers():
            if not entry.name.endswith('.ko'): continue
            data=kernel.extractfile(entry).read()
            assert b'vermagic='+krel.encode()+b' ' in data
            other=root.extractfile('./lib/modules/'+krel+'/'+Path(entry.name).name).read()
            assert data==other,entry.name
            count+=1
        assert count==31,count
        for name in ('./etc/hotplug.d/net/90-u30air-lan','./etc/hotplug.d/iface/90-u30air-lan','./opt/mu300/bin/mu300-lan-usb'):
            assert root.getmember(name).mode & 0o111
        assert root.getmember('./lib/firmware/rtl_nic/rtl8153b-2.fw').size==1880
        lan_helper=root.extractfile('./opt/mu300/bin/mu300-lan-usb').read()
        assert lan_helper==(HERE/'overlay/opt/mu300/bin/mu300-lan-usb').read_bytes()
        assert b'ip link set dev "$iface" master br-lan' in lan_helper
    assert manifest['network_policy'].endswith('hotplug helper attaches the NIC to br-lan and brings it up')
zip_path=HERE/'u30air-native14-android-charge-2026.10.05-test.zip'
expected=(Path(str(zip_path)+'.sha256').read_text().split()[0])
assert digest(zip_path.read_bytes())==expected
with zipfile.ZipFile(zip_path) as z:
    assert z.testzip() is None
    assert z.read('u30air-native14/delivery.json')==(release/'delivery.json').read_bytes()
    names={Path(name).name for name in z.namelist()}
    assert 'corresponding-project-source.tar.gz' in names
    assert 'corresponding-linux-7.2.8-source.tar.gz' in names
    assert 'device_known_hosts' not in names and 'device_known_hosts.reinstalled' not in names
with tarfile.open(release/'corresponding-project-source.tar.gz') as tf:
    assert tf.extractfile('native14/install-device.sh').read()==(HERE/'install-device.sh').read_bytes()
    assert tf.extractfile('native14/test-native.py').read()==(HERE/'test-native.py').read_bytes()
    assert tf.extractfile('native14/test-lan.py').read()==(HERE/'test-lan.py').read_bytes()
    assert tf.extractfile('native14/overlay/opt/mu300/bin/mu300-lan-usb').read()==(HERE/'overlay/opt/mu300/bin/mu300-lan-usb').read_bytes()
    assert tf.extractfile('native14/verify-delivery.py').read()==(HERE/'verify-delivery.py').read_bytes()
    assert tf.extractfile('native14/patch-fgu.py').read()==(HERE/'patch-fgu.py').read_bytes()
    assert tf.extractfile('native14/test-battery.py').read()==(HERE/'test-battery.py').read_bytes()
    for name in ('finalize-kernel.py','test-soc-reconcile.py','test-soc-worker.py','install-kernel-device.sh'):
        assert tf.extractfile('native14/'+name).read()==(HERE/name).read_bytes()
with tarfile.open(release/'corresponding-linux-7.2.8-source.tar.gz') as tf:
    fgu = tf.extractfile('linux-7.2.8/drivers/power/supply/sc27xx_fuel_gauge.c').read()
    assert digest(fgu) == manifest['fgu_source_sha256']
    assert b'U30 Air saved-SOC repair' in fgu
    assert b'.shutdown = sc27xx_fgu_shutdown' in fgu
    assert b'#include "fgu-soc-reconcile.inc"' in fgu
    assert b'u30_charge_uah(data->init_cap' in fgu
    for name in ('fgu-soc-policy.h','fgu-soc-reconcile.inc'):
        assert tf.extractfile('linux-7.2.8/drivers/power/supply/'+name).read()==(HERE/'source'/name).read_bytes()
    for name in manifest['source_sha256']:
        if name.endswith(('.c','.h','.inc')) and name in (
            'sgm41511-native.c','sgm41511-policy.h','host-policy.h',
            'android-charge.inc','android-charge-policy.h','fgu-soc-policy.h','fgu-soc-reconcile.inc'):
            assert tf.extractfile('linux-7.2.8/drivers/power/supply/'+name).read()==(HERE/'source'/name).read_bytes(), name
print('PASS: ZIP and nested hashes; safe U30 resource preservation; installer and tests match source; native ARM64 command; 31 matching modules; RTL8153B firmware; hotplug modes; u30air-only target')
