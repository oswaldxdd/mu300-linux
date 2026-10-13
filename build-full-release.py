#!/usr/bin/env python3
"""Assemble pinned charge2 assets using the committed source at repository root."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

TOP = Path(__file__).resolve().parent
HISTORICAL = TOP / 'experimental/u30air-charge2'
SOURCE_PATHS = [
    '.gitattributes', '.github', '.gitignore', 'LICENSE', 'README.md', 'BUILD-CHARGE2.md', 'build-full-release.py', 'android',
    'android-vendor', 'arch', 'boot', 'docs', 'i18n', 'kernel', 'openwrt',
    'rootfs', 'stock', 'tests', 'tools', 'upstream', 'install.cmd',
    'install.ps1', 'install.sh', 'uninstall.cmd', 'uninstall.ps1', 'uninstall.sh',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--official', type=Path, required=True)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    # This root wrapper is pinned to historical release assets. The active r2 source
    # has a separate formal builder with explicit kernel-reuse provenance checks.
    if ((TOP / 'docs/U30AIR-TEST1.md').is_file()
            or (TOP / 'tools/build-charge2-test.py').is_file()):
        parser.error(
            'this is the active charge2 source tree; the historical wrapper uses old pinned assets. '
            'Use tools/build-charge2-release.py for the formal r2 package after source review, or '
            'tools/build-charge2-test.py only for a separately identified test candidate.'
        )
    # Export tracked, committed build sources only. Never copy the whole workspace,
    # its experimental archive, device backups, credentials or binary outputs.
    dirty = subprocess.check_output([
        'git', '-C', str(TOP), 'status', '--porcelain', '--', *SOURCE_PATHS,
        'build-full-release.py', 'experimental/u30air-charge2',
    ])
    if dirty:
        parser.error('commit source changes before assembling a release')
    if args.out.exists():
        parser.error('output must be a new directory')
    source_commit = subprocess.check_output([
        'git', '-C', str(TOP), 'rev-parse', 'HEAD',
    ]).decode().strip()
    with tempfile.TemporaryDirectory(prefix='mu300-charge2-root-') as temp:
        stage = Path(temp)
        source = stage / 'source'
        source.mkdir()
        archive = stage / 'source.tar'
        subprocess.run([
            'git', '-C', str(TOP), 'archive', '--format=tar',
            '-o', str(archive), 'HEAD', '--', *SOURCE_PATHS,
        ], check=True)
        with tarfile.open(archive) as tar:
            tar.extractall(source, filter='data')
        provenance = stage / 'provenance'
        provenance.mkdir()
        shutil.copyfile(HISTORICAL / 'provenance/effective.config',
                        provenance / 'effective.config')
        subprocess.run([
            sys.executable, str(HISTORICAL / 'build-full-release.py'),
            '--repo', str(source), '--docs', str(HISTORICAL),
            '--official', str(args.official.resolve()),
            '--kernel', str(args.kernel.resolve()), '--out', str(args.out.resolve()),
        ], check=True)
        (args.out / 'provenance/root-source.json').write_text(json.dumps({
            'source_commit': source_commit,
            'source_layout': 'u30air branch root',
            'frozen_release_commit': '321f771017fe273447b3ae9f2f3ccbc5d6209879',
            'input_assets': 'pinned v2026.10.15-charge2 assets',
            'new_hardware_validation': False,
        }, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
