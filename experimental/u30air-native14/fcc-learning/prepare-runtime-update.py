"""Package the exact complete-firmware kernel/runtime with transaction rollback."""
from pathlib import Path
import hashlib
import io
import json
import shutil
import tarfile
here = Path(__file__).resolve().parent
release = here/'firmware/release'
info = json.loads((release/'delivery.json').read_text())
firmware = release/info['archive']
def sha(p):
    with p.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
assert sha(firmware) == info['sha256']
folder = here/'runtime-update'
folder.mkdir(exist_ok=True)
with tarfile.open(firmware) as package:
    checks = dict(line.split('  ',1)[::-1] for line in
                  package.extractfile('package/SHA256SUMS').read().decode().splitlines())
    for name in ('mu300-kernel-7.2.8-u30air-native14.tar.gz', 'mu300-update'):
        data = package.extractfile('package/'+name).read()
        assert hashlib.sha256(data).hexdigest() == checks[name]
        (folder/name).write_bytes(data)
    root_data = package.extractfile('package/mu300-openwrt-rootfs.tar.gz').read()
    assert hashlib.sha256(root_data).hexdigest() == checks['mu300-openwrt-rootfs.tar.gz']
    with tarfile.open(fileobj=io.BytesIO(root_data)) as root:
        runtime = folder/'runtime'
        runtime.mkdir(exist_ok=True)
        for src, dst in (
            ('fcc-record.sh', 'opt/mu300/lib/fcc-record.sh'),
            ('mu300-fcc-record', 'opt/mu300/bin/mu300-fcc-record'),
            ('mu300-fcc-record.init', 'etc/init.d/mu300-fcc-record')):
            data = root.extractfile('./'+dst).read()
            assert data == (here/'runtime'/src).read_bytes()
            (runtime/src).write_bytes(data)
for name in ('install-native14-runtime.sh', 'rollback-native14-runtime.sh'):
    shutil.copy2(here/name, folder/name)
for p in folder.rglob('*'):
    if p.is_file() and p.suffix != '.gz':
        p.chmod(0o755)
paths = sorted(p for p in folder.rglob('*') if p.is_file() and p.name != 'KERNEL-SHA256SUMS')
(folder/'KERNEL-SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(folder).as_posix()}\n' for p in paths))
archive = here/'native14-runtime-update.tar.gz'
with tarfile.open(archive, 'w:gz') as tar:
    tar.add(folder, arcname='native14-runtime-update')
report = {'firmware_archive_sha256': info['sha256'], 'update_archive_sha256': sha(archive),
          'same_kernel_and_runtime_as_full_firmware':True, 'baseline':'7.2.8-u30air-native13',
          'rollback_tested_on_temporary_files':True, 'hardware_deployed':False}
(here/'runtime-update-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'archive':str(archive), 'sha256':sha(archive), 'bytes':archive.stat().st_size}))
