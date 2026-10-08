#!/usr/bin/env python3
"""Run native14 source fixtures in temporary directories, never on a device."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

source = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--kernel-bundle', type=Path,
                    help='optional native14 kernel archive for real-updater/installer fixtures')
args = parser.parse_args()
manifest = json.loads((source/'SOURCE-PROVENANCE.json').read_text())
for record in manifest['files']:
    p = source/record['path']
    content = p.readlink().as_posix().encode() if p.is_symlink() else p.read_bytes()
    assert hashlib.sha256(content).hexdigest() == record['sha256'], record['path']
print('PASS: imported source provenance', flush=True)
with tempfile.TemporaryDirectory(prefix='native14-source-tests-') as name:
    work = Path(name)
    tree = work/'native14'
    shutil.copytree(source, tree, symlinks=True, ignore=shutil.ignore_patterns('__pycache__'))
    fcc = tree/'fcc-learning'
    for name in ['test-fcc-policy', 'test-fcc-quiet', 'test-fcc-state']:
        binary = work/name
        subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror',
                        str(fcc/(name+'.c')),'-o',str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
    for name in ['test-charger-quiet.py','test-fgu-quiet.py','test-kernel-adapter.py',
                 'test-full-worker.py','test-restore-write.py']:
        subprocess.run([sys.executable, str(fcc/name)], cwd=tree, check=True)
    for p in tree.rglob('*'):
        if p.is_file() and not p.is_symlink():
            if p.suffix == '.py':
                compile(p.read_text(), str(p), 'exec')
            if p.suffix == '.sh' or p.name in {
                    'mu300-update','mu300-fcc-record','mu300-fcc-record.init',
                    'mu300-lan-usb','90-u30air-lan'}:
                subprocess.run(['sh','-n',str(p)], check=True)
    print('PASS: Python and shell syntax', flush=True)
    if args.kernel_bundle:
        bundle = args.kernel_bundle.resolve()
        assert hashlib.sha256(bundle.read_bytes()).hexdigest() == manifest['kernel_bundle_sha256']
        runtime = work/'runtime-update'
        runtime.mkdir()
        shutil.copy2(bundle, runtime/'mu300-kernel-7.2.8-u30air-native14.tar.gz')
        shutil.copy2(tree/'mu300-update', runtime/'mu300-update')
        shutil.copy2(bundle, tree/'mu300-kernel-7.2.8-u30air-native14.tar.gz')
        subprocess.run([sys.executable,str(tree/'test-deployment-compat.py')], cwd=tree, check=True)
        subprocess.run([sys.executable,str(tree/'test-mu300-update-idempotent.py')], cwd=tree, check=True)
        print('PASS: actual updater/installer fixtures against regular files', flush=True)
    else:
        print('SKIP: actual updater/installer fixtures need --kernel-bundle', flush=True)
print('PASS: native14 offline checks; battery accuracy and hardware acceptance remain unproven')
