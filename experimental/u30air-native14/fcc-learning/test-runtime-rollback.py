"""Execute the actual rollback script against regular files under a temp root."""
from pathlib import Path
import gzip
import hashlib
import os
import subprocess
import tarfile
import tempfile
here = Path(__file__).resolve().parent
original = (here/'rollback-native14-runtime.sh').read_text()
paths = ['opt/mu300/bin/mu300-fcc-record', 'opt/mu300/lib/fcc-record.sh',
         'etc/init.d/mu300-fcc-record', 'etc/rc.d/S20mu300-fcc-record',
         'etc/rc.d/K89mu300-fcc-record']
with tempfile.TemporaryDirectory(prefix='u30-runtime-rollback-') as tmp:
    root = Path(tmp)
    backup = root/'mnt/mu300-disk/.mu300-native14-kernel-rollback/test'
    backup.mkdir(parents=True)
    for part in ('dev', 'sys/class/block/mmcblk0p38', 'fakebin', 'etc/mu300'):
        (root/part).mkdir(parents=True, exist_ok=True)
    (root/'sys/class/block/mmcblk0p38/uevent').write_text('PARTNAME=boot_b\n')
    (root/'sys/class/block/mmcblk0p38/size').write_text('131072\n')
    device = root/'dev/mmcblk0p38'
    with device.open('wb') as stream:
        stream.write(b'new kernel')
        stream.truncate(67108864)
    before = hashlib.file_digest(device.open('rb'), 'sha256').hexdigest()
    digest = hashlib.sha256()
    with gzip.open(backup/'boot_b.img.gz', 'wb') as stream:
        chunk = bytes(1048576)
        for _ in range(64):
            stream.write(chunk); digest.update(chunk)
    (backup/'boot_b.sha256').write_text(digest.hexdigest()+'\n')
    metadata = root/'metadata/boot'
    metadata.mkdir(parents=True)
    (metadata/'kernel').write_text('7.2\n')
    with tarfile.open(backup/'boot-metadata.tar.gz', 'w:gz') as tar:
        tar.add(metadata, arcname='boot')
    (backup/'boot-metadata.sha256').write_text(
        hashlib.file_digest((backup/'boot-metadata.tar.gz').open('rb'),'sha256').hexdigest()+'\n')
    (backup/'image-version').write_text('previous native13\n')
    command = root/'fakebin/mu300-device'
    command.write_text('#!/bin/sh\necho u30air\n'); command.chmod(0o755)
    for p in paths:
        target = root/p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('#!/bin/sh\nexit 0\n' if 'init.d' in p else 'new runtime\n')
        target.chmod(0o755)
    # Previous first file and init link existed; other paths were absent.
    old = backup/'runtime-before'/paths[0]
    old.parent.mkdir(parents=True)
    old.write_text('previous runtime\n'); old.chmod(0o700)
    link = backup/'runtime-before'/paths[3]
    link.parent.mkdir(parents=True)
    link.symlink_to('../init.d/previous-service')
    script = original.replace('/mnt/mu300-disk', str(root/'mnt/mu300-disk'))
    script = script.replace('/sys/class/block', str(root/'sys/class/block'))
    script = script.replace('dev=/dev/', 'dev='+str(root/'dev')+'/')
    script = script.replace('/etc/', str(root/'etc')+'/')
    script = script.replace('"/$p', '"'+str(root)+'/$p')
    assert 'dev=/dev/' not in script and '"/$p' not in script
    runner = backup/'rollback.sh'
    runner.write_text(script)
    env = dict(os.environ, PATH=str(root/'fakebin')+':'+os.environ['PATH'])
    # Bad snapshot hash must fail before changing boot or runtime.
    good_hash = (backup/'boot_b.sha256').read_text()
    (backup/'boot_b.sha256').write_text('0'*64+'\n')
    result = subprocess.run(['sh',str(runner)], env=env, capture_output=True)
    assert result.returncode != 0
    assert hashlib.file_digest(device.open('rb'),'sha256').hexdigest() == before
    assert (root/paths[0]).read_text() == 'new runtime\n'
    (backup/'boot_b.sha256').write_text(good_hash)
    result = subprocess.run(['sh',str(runner)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert hashlib.file_digest(device.open('rb'),'sha256').hexdigest() == digest.hexdigest()
    assert (root/'mnt/mu300-disk/boot/kernel').read_text() == '7.2\n'
    assert (root/'etc/mu300/image-version').read_text() == 'previous native13\n'
    assert (root/paths[0]).read_text() == 'previous runtime\n'
    assert (root/paths[0]).stat().st_mode & 0o777 == 0o700
    assert (root/paths[3]).is_symlink() and os.readlink(root/paths[3]) == '../init.d/previous-service'
    for p in (paths[1], paths[2], paths[4]):
        assert not (root/p).exists()
    print('PASS: actual rollback refuses corrupt snapshot before writes; restores boot, metadata, runtime bytes/mode/symlink and removes initially absent service paths')
