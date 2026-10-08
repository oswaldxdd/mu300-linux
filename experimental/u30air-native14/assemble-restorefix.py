#!/usr/bin/env python3
"""Build a complete, audited offline release from pinned local inputs."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess as sp
import tarfile
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
BUILD = Path(os.environ.get('U30_BUILD','/root/u30air-native14-build-20261005'))
TOP = BUILD/'project'
DOWNLOAD = Path('/root/u30air-native-download')
KREL = '7.2.8-u30air-native14'
VERSION = 'u30air-native14-restorefix-2026.10.07-test'
OUT = HERE/'restorefix-release'

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def main():
    OUT.mkdir(exist_ok=True)
    assert json.loads((BUILD/'build-state.json').read_text())['phase']=='kernel_and31modules_complete_not_deployed'
    snapshot=json.loads((BUILD/'BUILD-MANIFEST.json').read_text())
    for name,expected in snapshot['source_sha256'].items():
        assert sha(HERE/'source'/name)==expected, name
    base=DOWNLOAD/'mu300-openwrt-rootfs.tar.gz'
    assert sha(base)=='1a5b8ad81bab9ae13108d058c7ed136e129f9d54b3eb5a589001a304fb67d4c1'
    fw=DOWNLOAD/'rtl8153b-2.fw'
    assert sha(fw)=='137c9c752e8648ecfcf3b74ffc7d675c098145c222e2969721e6257af31716bc'
    data=fw.read_bytes()
    assert data[:32]==hashlib.sha256(data[32:]).digest()
    # Refuse a source change after compilation/staging.
    for n in ('sgm41511-native.c','sgm41511-policy.h','host-policy.h','android-charge.inc','android-charge-policy.h', 'fgu-soc-policy.h', 'fgu-soc-reconcile.inc', 'fcc-policy.h', 'fcc-state.h', 'fcc-kernel.inc', 'fcc-quiet-policy.h', 'sc27xx_fuel_gauge.c'):
        assert sha(HERE/'source'/n)==sha(TOP/'upstream/port/drivers/power/supply'/n), n
    config=(BUILD/'out-7.2.8/.config').read_text()
    for option in ('CONFIG_CHARGER_SGM41511_NATIVE=y','CONFIG_USB_RTL8152=y',
                   'CONFIG_USB_DWC3_DUAL_ROLE=y','CONFIG_USB_ROLE_SWITCH=y'):
        assert option in config, option
    with tempfile.TemporaryDirectory(prefix='package-',dir=BUILD) as tmp:
        work=Path(tmp); pkg=work/'package'; pkg.mkdir()
        root=work/'rootfs'; root.mkdir()
        sp.run(['tar','-xzpf',str(base),'-C',str(root)],check=True)
        shutil.copytree(HERE/'overlay',root,dirs_exist_ok=True,symlinks=True)
        for p in (HERE/'overlay').rglob('*'):
            if p.is_file(): (root/p.relative_to(HERE/'overlay')).chmod(0o755)
        command=TOP/'rootfs/overlay/opt/mu300/bin/mu300-usb'
        binary=command.read_bytes()
        assert binary[:4]==b'\x7fELF' and binary[18:20]==b'\xb7\x00'
        shutil.copy2(command,root/'opt/mu300/bin/mu300-usb')
        (root/'etc/mu300/image-version').write_text(VERSION+'\n')
        (root/'lib/firmware/rtl_nic').mkdir(parents=True,exist_ok=True)
        shutil.copy2(fw,root/'lib/firmware/rtl_nic/rtl8153b-2.fw')
        license_dir=root/'usr/share/licenses/r8152-firmware'; license_dir.mkdir(parents=True,exist_ok=True)
        shutil.copy2(DOWNLOAD/'LICENCE.rtlwifi_firmware.txt',license_dir)
        modules=root/'lib/modules'/KREL; modules.mkdir(parents=True,exist_ok=True)
        outputs=TOP/'upstream/out-native14'
        for p in (outputs/'modules').glob('*.ko'):
            assert b'vermagic='+KREL.encode()+b' ' in p.read_bytes(), p
            shutil.copy2(p,modules)
        for n in ('modules.builtin','modules.builtin.modinfo'): shutil.copy2(outputs/n,modules/n)
        # Public images contain neither per-device vendor data nor private settings.
        for p in root.rglob('*'):
            relative=p.relative_to(root).as_posix()
            if p.is_symlink(): continue
            if p.is_file():
                assert not relative.startswith('opt/mu300/android/') or relative=='opt/mu300/android/system/bin/cltest'
                assert not relative.startswith('etc/dropbear/dropbear_')
                assert 'ssh_host_' not in relative and '__properties__' not in relative
                assert p.name not in ('wcnmodem.bin','gnssmodem.bin')
                assert not p.name.startswith('wifi_board_config')
                if relative=='etc/machine-id': assert p.stat().st_size==0
        with tarfile.open(pkg/'mu300-openwrt-rootfs.tar.gz','w:gz') as tf: tf.add(root,arcname='.')
        kernel=BUILD/f'mu300-kernel-{KREL}.tar.gz'
        kdir=work/'kernel'; kdir.mkdir()
        sp.run(['tar','-xzf',str(kernel),'-C',str(kdir)],check=True)
        assert (kdir/'kernel.release').read_text().strip()==KREL
        (kdir/'devices').write_text('u30air\n')
        with tarfile.open(pkg/kernel.name,'w:gz') as tf: tf.add(kdir,arcname='.')
        for n in ('install-device.sh','rollback-device.sh'): shutil.copy2(HERE/n,pkg/n)
        shutil.copy2(TOP/'rootfs/overlay/opt/mu300/bin/mu300-update',pkg)
        shutil.copy2(BUILD/'out-7.2.8/.config',pkg/'kernel.config')
        shutil.copytree(BUILD/'logs',pkg/'build-logs')
        manifest={
            'version':VERSION,'kernel_release':KREL,'device':'u30air',
            'project_commit':'65d1bc496aa8a15cbf535d7a746aa249bf2aa9d3',
            'base_rootfs_sha256':sha(base),'base_rootfs_origin':'previously cached upstream artifact; identity pinned by SHA-256',
            'power_policy':'stock DT battery profiles and JEITA; source-qualified 5V Type-C/PD current; BC1.2 capped to 1.5A; SDP/unknown capped to 0.5A; boost off; hardware safety protection retained',
            'soc_policy':'native14: protected full endpoint plus bounded charger-managed low-OCV-to-full FCC learning; measured-span capacity formula;600s lease with actual inhibit readback and stable real buffered reference; default 3847mAh, design 4050mAh; qualified learned model persisted with device/DTBO/ADC binding and bounded boot restore; 3983mAh learned on test device; independent accuracy validation pending',
            'hardware_fcc_learning_tested':True,
            'independent_capacity_accuracy_verified':False,
            'runtime_restore_fix':'Complete bounded restore request staged in private temporary then cat to sysfs; verified on native14 runtime, this full package not yet flashed',
            'runtime_restore_sha256':{n:sha(HERE/'overlay/opt/mu300'/p) for n,p in [('library','lib/fcc-record.sh'),('service','bin/mu300-fcc-record')]},
            'hardware_termination_current_ua':120000,
            'baseline_soc_refinement_sha256':sha(HERE/'refine-fgu.py'),
            'fgu_source_sha256':sha(BUILD/'linux-7.2.8/drivers/power/supply/sc27xx_fuel_gauge.c'),
            'baseline_fgu_patch_sha256':sha(HERE/'patch-fgu.py'),
            'data_policy':'native kernel auto; PC enumeration guard; manual device/host/auto commands',
            'network_policy':'Realtek 0bda:8153 and r8152; hotplug helper attaches the NIC to br-lan and brings it up',
            'dtb_policy':'retain stock vendor DTB and DTBO',
            'hardware_boot_tested':False,'hardware_charging_tested':False,
            'source_sha256':{p.name:sha(p) for p in sorted((HERE/'source').iterdir()) if p.is_file()},
            'toolchain':sp.check_output(['aarch64-linux-gnu-gcc','--version'],text=True).splitlines()[0],
        }
        (pkg/'BUILD-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
        shutil.copy2(pkg/'BUILD-MANIFEST.json',OUT)
        paths=sorted(p for p in pkg.rglob('*') if p.is_file())
        (pkg/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(pkg).as_posix()}\n' for p in paths))
        archive=OUT/'u30air-native14-firmware.tar.gz'
        with tarfile.open(archive,'w:gz') as tf: tf.add(pkg,arcname='package')
    for name in ('install.ps1','install.cmd','collect-status.sh','README.md','rollback.ps1',
                 'rollback-device.sh','recover-android.sh','recover-android.ps1'):
        shutil.copy2(HERE/name,OUT/name)
    # Android recovery tools kept local and separate from the public rootfs.
    recovery=Path('/mnt/c/Users/Oswald/OneDrive/文档/ChatGPT/Openwrt/usb-host-native-power/firmware/release')
    for name in ('adb.exe','AdbWinApi.dll','AdbWinUsbApi.dll','NOTICE.txt','busybox','android-mount-mu300root.sh'):
        shutil.copy2(recovery/name,OUT/name)
    (OUT/'delivery.json').write_text(json.dumps({'archive':archive.name,'sha256':sha(archive)},indent=2)+'\n')
    with tarfile.open(OUT/'corresponding-project-source.tar.gz','w:gz') as tf:
        for p in sorted(TOP.iterdir()):
            if p.name.startswith('.'): continue
            if p.name=='upstream':
                for child in p.iterdir():
                    if not child.name.startswith('out'): tf.add(child,arcname='project/upstream/'+child.name)
            else: tf.add(p,arcname='project/'+p.name)
        source_names=('source','overlay','assemble.py','assemble-restorefix.py','prepare-build.py','finalize-kernel.py','patch-fgu.py','refine-fgu.py','test-saved-soc.py','test-charge-init.py','test-battery.py','install-device.sh',
                      'rollback-device.sh','install.ps1','install.cmd','collect-status.sh',
                      'rollback.ps1','recover-android.sh','recover-android.ps1',
                      'test-native.py','test-lan.py','verify-delivery.py',
                      'README.md','install-kernel-device.sh','test-android-charge.py','test-bus-pm.py','test-soc-reconcile.py','test-soc-worker.py','android-reference','.gitattributes','fcc-learning')
        for name in source_names:
            p=HERE/name
            if p.is_file() or p.is_dir(): tf.add(p,arcname='native14/'+p.name)
    # Exact patched Linux sources, not a claim that the cached tree is vanilla.
    kernel_source=OUT/'corresponding-linux-7.2.8-source.tar.gz'
    if True:
        sp.run(['tar','--exclude=.git','-czf',str(kernel_source),'-C',str(BUILD),'linux-7.2.8'],check=True)
    paths=sorted(p for p in OUT.iterdir() if p.is_file() and p.name!='SHA256SUMS')
    (OUT/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in paths))
    zip_path=HERE/'u30air-native14-restorefix-2026.10.07-test.zip'
    with zipfile.ZipFile(zip_path,'w',compression=zipfile.ZIP_STORED) as z:
        for p in sorted(OUT.iterdir()):
            if p.is_file(): z.write(p,'u30air-native14/'+p.name)
    Path(str(zip_path)+'.sha256').write_text(f'{sha(zip_path)}  {zip_path.name}\n')
    print(json.dumps({'zip':str(zip_path),'bytes':zip_path.stat().st_size,'sha256':sha(zip_path)},indent=2))

if __name__=='__main__': main()
