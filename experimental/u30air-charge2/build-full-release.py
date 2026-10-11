"""Assemble the public U30 Air charge2 release using pinned, local inputs (Linux/WSL)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

KREL = '7.2.9-u30air-charge2'
PINS = {
    'mu300-openwrt-luci-rootfs.tar.gz': '8ff2008f00b891c021b139e5f2cd225f54718957176a827c677c055560229eff',
    'mu300-kernel.tar.gz': '2f38180a0f417fbc0343e6773e8e458edbbe5293143fbab938952e516c6b58eb',
    'mu300-update': '1253c7cbdd627254f006539abb89aa072c6c47ccc0b6ccf06218b0cc812ad379',
}
KERNEL_PIN = '0b5c32f83858c5323bd484e577ac37159601a373db7031ac76b60623f9b077b5'
MAPPINGS = {
    'opt/mu300/bin/mu300-usb': 'rootfs/overlay/opt/mu300/bin/mu300-usb',
    'opt/mu300/bin/mu300-fcc-record': 'rootfs/overlay/opt/mu300/bin/mu300-fcc-record',
    'opt/mu300/bin/mu300-power': 'rootfs/overlay/opt/mu300/bin/mu300-power',
    'opt/mu300/lib/fcc-device.sh': 'rootfs/overlay/opt/mu300/lib/fcc-device.sh',
    'opt/mu300/lib/fcc-record.sh': 'rootfs/overlay/opt/mu300/lib/fcc-record.sh',
    'etc/init.d/mu300-fcc-record': 'openwrt/overlay/etc/init.d/mu300-fcc-record',
    'etc/uci-defaults/90-mu300': 'openwrt/overlay/etc/uci-defaults/90-mu300',
    'usr/libexec/unisoc-modem/dashboard-info': 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/dashboard-info',
    'usr/libexec/unisoc-modem/power': 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/power',
    'www/luci-static/resources/view/mu300/home.js': 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/home.js',
    'www/luci-static/resources/view/mu300/power.js': 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/power.js',
}

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()

def run(*args, **kw):
    return subprocess.run(args, check=True, **kw)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--official', type=Path, required=True)
    p.add_argument('--kernel', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--docs', type=Path, required=True)
    a = p.parse_args()
    a.repo = a.repo.resolve()
    a.out = a.out.resolve()
    for name, expected in PINS.items():
        assert sha(a.official / name) == expected, ('input hash mismatch', name)
    assert sha(a.kernel) == KERNEL_PIN, 'candidate kernel hash mismatch'
    assert not a.out.exists(), 'output must be a new directory'
    base = '6a664c379f5adf466ac017f898f19148c96ef404'
    git_checkout = (a.repo / '.git').exists()
    if git_checkout:
        actual_base = subprocess.check_output(['git', '-C', str(a.repo), 'rev-parse', 'HEAD']).decode().strip()
        assert actual_base == base, ('unexpected source base', actual_base)
        diff = subprocess.check_output(['git', '-C', str(a.repo), 'diff', 'HEAD', '--binary'])
    else:
        assert (a.repo / 'upstream/u30air/typec/IMPORT-PROVENANCE.json').is_file()
        assert not (a.repo / 'artifacts').exists(), 'use the clean distributed source snapshot'
        diff = b''
    with tempfile.TemporaryDirectory(prefix='mu300-charge2-release-') as temp:
        stage = Path(temp)
        source = stage / 'source'
        source.mkdir()
        snapshot = stage / 'source.tar'
        if git_checkout:
            run('git', '-C', str(a.repo), 'archive', '-o', str(snapshot), 'HEAD')
            run('tar', '-xf', str(snapshot), '-C', str(source))
            run('git', '-C', str(source), 'init', '-q')
            patch = stage / 'charge2.patch'
            patch.write_bytes(diff)
            run('git', '-C', str(source), 'apply', str(patch))
            shutil.rmtree(source / '.git')
            shutil.copytree(a.repo / 'upstream/u30air/typec', source / 'upstream/u30air/typec')
            shutil.copyfile(a.repo / 'upstream/u30air/fcc-learning/source/charge-limit-policy.h',
                            source / 'upstream/u30air/fcc-learning/source/charge-limit-policy.h')
        else:
            shutil.copytree(a.repo, source, dirs_exist_ok=True)
        rootfs, kernel = stage / 'rootfs', stage / 'kernel'
        rootfs.mkdir()
        kernel.mkdir()
        run('tar', '-xzf', str(a.official / 'mu300-openwrt-luci-rootfs.tar.gz'), '-C', str(rootfs))
        run('tar', '-xzf', str(a.kernel), '-C', str(kernel))
        assert (kernel / 'kernel.release').read_text().strip() == KREL
        assert (kernel / 'devices').read_text().strip() == 'u30air'
        modules = sorted((kernel / 'modules').glob('*.ko'))
        assert len(modules) == 379
        for dst, src in MAPPINGS.items():
            target = rootfs / dst
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / src, target)
            target.chmod(0o644 if dst.endswith(('.sh', '.js')) else 0o755)
        for lang, catalog in [('zh_Hans', 'zh-cn'), ('tr', 'tr')]:
            target = rootfs / f'usr/lib/lua/luci/i18n/mu300.{catalog}.lmo'
            target.parent.mkdir(parents=True, exist_ok=True)
            run('python3', str(source / 'tools/po2lmo.py'),
                str(source / f'openwrt/luci-app-mu300/po/{lang}/mu300.po'), str(target))
            target.chmod(0o644)
        moddir = rootfs / 'lib/modules' / KREL
        assert not moddir.exists(), 'unexpected candidate modules in official rootfs'
        moddir.mkdir(parents=True)
        for f in modules + [kernel / 'modules.builtin', kernel / 'modules.builtin.modinfo', kernel / 'kernel.release']:
            shutil.copyfile(f, moddir / f.name)
            (moddir / f.name).chmod(0o644)
        a.out.mkdir()
        release = a.out / 'release'
        release.mkdir()
        shutil.copyfile(a.kernel, release / 'mu300-kernel-7.2.tar.gz')
        for name in ['mu300-kernel.tar.gz', 'mu300-update']:
            shutil.copyfile(a.official / name, release / name)
        # Fixed mtime and gzip metadata make the assembled rootfs reproducible.
        import gzip
        with (release / 'mu300-openwrt-luci-rootfs.tar.gz').open('wb') as raw:
            with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as gz:
                proc = subprocess.Popen(['tar', '-C', str(rootfs), '--sort=name', '--numeric-owner',
                    '--owner=0', '--group=0', '--mtime=@0', '-cf', '-', '.'], stdout=subprocess.PIPE)
                try:
                    shutil.copyfileobj(proc.stdout, gz)
                finally:
                    proc.stdout.close()
                assert proc.wait() == 0, 'rootfs tar failed'
        shutil.copytree(source, a.out / 'source')
        for name in ['README.md', '中文使用说明.md', 'RELEASE-NOTES.md', 'ROLLBACK.md',
                     'BUILD-PACKAGE.md', 'verify-firmware.py', 'install-u30air-host.ps1']:
            shutil.copyfile(a.docs / name, a.out / name)
        provenance = a.out / 'provenance'
        provenance.mkdir()
        (provenance / 'charge2.patch').write_bytes(diff)
        config = a.repo / 'artifacts/effective.config' if git_checkout else a.repo.parent / 'provenance/effective.config'
        shutil.copyfile(config, provenance / 'effective.config')
        shutil.copyfile(Path(__file__).resolve(), a.out / 'build-full-release.py')
        manifest = {
            'release_id': 'v2026.10.15-charge2', 'packaged_on': '2026-10-11',
            'upstream_tag': 'v2026.10.15', 'upstream_commit': '52093b28c9c3e0280b1a1be6f0d4b5437a6053a1',
            'integration_source_base': base, 'kernel_release': KREL, 'module_count': len(modules),
            'kernel_source_sha256': 'b4c5dfbe51a364a6c7f03869200f88c8e1f77403539005f14b7fc6bc91b8d8ba',
            'input_sha256': PINS, 'candidate_kernel_sha256': KERNEL_PIN,
            'typec_import': json.loads((source / 'upstream/u30air/typec/IMPORT-PROVENANCE.json').read_text()),
            'validation': {'candidate_kernel_booted': True, 'host_charging_and_rtl8153': True,
                           'prior_charge1_wifi_authenticated_and_luci_http': True, 'new_archive_wipe_installed': False,
                           'fcc_whole_cycle_accuracy': False, 'long_term_stability': False,
                           'charge_limit_0_pause': True, 'charge_limit_100_resume': True,
                           'charge_limit_80_hold_with_host': True, 'charge_limit_service_restart_persisted': True,
                           'charge_limit_natural_recovery_threshold_tested': False,
                           'regmap_debugfs_panic_fixed': False},
        }
        (release / 'BUILD-MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (release / 'SHA256SUMS').write_text(''.join(f'{sha(f)}  {f.name}\n' for f in sorted(release.iterdir()) if f.is_file()))
        print(json.dumps({'output': str(a.out), 'kernel': KREL, 'modules': len(modules),
                          'rootfs_sha256': sha(release / 'mu300-openwrt-luci-rootfs.tar.gz')}, indent=2))

if __name__ == '__main__':
    main()
