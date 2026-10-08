"""Verify exact native14 full image/modules, controller sources and FCC runtime."""
from pathlib import Path
import hashlib
import io
import json
import subprocess
import tarfile

here=Path(__file__).resolve().parent
layout=here/'firmware'
release=layout/'release'
subprocess.run(['python3',str(layout/'verify-delivery.py')],check=True)
snapshot=json.loads(Path('/root/u30air-native14-build-20261005/BUILD-MANIFEST.json').read_text())
manifest=json.loads((release/'BUILD-MANIFEST.json').read_text())
assert snapshot['kernel_release']==manifest['kernel_release']=='7.2.8-u30air-native14'
assert not manifest['hardware_fcc_learning_tested']
assert 'bounded charger-managed' in manifest['soc_policy']
for name,expected in snapshot['source_sha256'].items():
    assert manifest['source_sha256'][name]==expected,name
    assert hashlib.sha256((here/'source'/name).read_bytes()).hexdigest()==expected,name
info=json.loads((release/'delivery.json').read_text())
with tarfile.open(release/info['archive']) as package:
    with tarfile.open(fileobj=io.BytesIO(package.extractfile('package/mu300-kernel-7.2.8-u30air-native14.tar.gz').read())) as kernel:
        with tarfile.open('/root/u30air-native14-build-20261005/mu300-kernel-7.2.8-u30air-native14.tar.gz') as built:
            assert kernel.extractfile('./Image').read()==built.extractfile('./Image').read()
        for entry in kernel.getmembers():
            if entry.name.endswith('.ko'):
                data=kernel.extractfile(entry).read()
                assert hashlib.sha256(data).hexdigest()==snapshot['modules_sha256'][Path(entry.name).name]
    with tarfile.open(fileobj=io.BytesIO(package.extractfile('package/mu300-openwrt-rootfs.tar.gz').read())) as root:
        for src,dst in (('fcc-record.sh','opt/mu300/lib/fcc-record.sh'),
                        ('mu300-fcc-record','opt/mu300/bin/mu300-fcc-record'),
                        ('mu300-fcc-record.init','etc/init.d/mu300-fcc-record')):
            assert root.extractfile('./'+dst).read()==(here/'runtime'/src).read_bytes()
            assert root.getmember('./'+dst).mode&0o111
        link=root.getmember('./etc/rc.d/S20mu300-fcc-record')
        assert link.issym() and link.linkname=='../init.d/mu300-fcc-record'
        assert not any('.mu300-fcc/learned-v1' in p.name for p in root.getmembers())
with tarfile.open(release/'corresponding-linux-7.2.8-source.tar.gz') as linux:
    prefix='linux-7.2.8/drivers/power/supply/'
    for name in snapshot['source_sha256']:
        assert linux.extractfile(prefix+name).read()==(here/'source'/name).read_bytes(),name
with tarfile.open(release/'corresponding-project-source.tar.gz') as project:
    for name in ('fcc-quiet-policy.h','fcc-kernel.inc','sgm41511-native.c','fcc-policy.h'):
        assert project.extractfile('native14/fcc-learning/source/'+name).read()==(here/'source'/name).read_bytes()
    for name in ('test-full-worker.py','test-runtime-rollback.py'):
        assert project.extractfile('native14/fcc-learning/'+name).read()==(here/name).read_bytes()
print('PASS: complete native14 package matches frozen full kernel/31modules and controller sources; exact FCC service/startup; no fabricated learned record; corresponding tests retained; hardware proof still pending')
