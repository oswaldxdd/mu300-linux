"""Independent artifact inspection; reads archives without extracting or accessing devices."""
import argparse
import hashlib
import json
import re
import struct
import tarfile
from pathlib import Path, PurePosixPath


def sha(data):
    return hashlib.sha256(data).hexdigest()


def members(path):
    result = {}
    with tarfile.open(path, 'r:gz') as archive:
        for item in archive.getmembers():
            name = item.name.removeprefix('./').rstrip('/')
            assert not item.name.startswith('/') and '..' not in PurePosixPath(name).parts, item.name
            if not name or name == '.':
                continue
            assert name not in result, ('duplicate archive entry', name)
            result[name] = (item, archive.extractfile(item).read() if item.isfile() else None)
    return result


def cpio(data):
    result, offset = {}, 0
    while offset < len(data):
        header = data[offset:offset + 110]
        assert header[:6] == b'070701'
        size = int(header[54:62], 16)
        length = int(header[94:102], 16)
        offset += 110
        name = data[offset:offset + length - 1].decode()
        offset = (offset + length + 3) & ~3
        contents = data[offset:offset + size]
        offset = (offset + size + 3) & ~3
        if name == 'TRAILER!!!':
            break
        result[name.removeprefix('./')] = contents
    return result


def unlz4(data):
    import lz4.block
    result, offset = bytearray(), 0
    while offset < len(data):
        assert data[offset:offset + 4] == b'\x02\x21\x4c\x18'
        offset += 4
        while offset < len(data) and data[offset:offset + 4] != b'\x02\x21\x4c\x18':
            size = int.from_bytes(data[offset:offset + 4], 'little')
            offset += 4
            result.extend(lz4.block.decompress(data[offset:offset + size], uncompressed_size=8 << 20))
            offset += size
    return bytes(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('repo', type=Path)
    parser.add_argument('kernel', type=Path)
    parser.add_argument('rootfs', type=Path)
    parser.add_argument('--official-rootfs', type=Path)
    args = parser.parse_args()
    kernel, root = members(args.kernel), members(args.rootfs)
    wrapped = kernel['Image'][1]
    gap = 0x180000
    assert wrapped[56:60] == b'ARMd' and wrapped[gap + 56:gap + 60] == b'ARMd'
    assert struct.unpack_from('<I', wrapped)[0] == 0x14000000 | (gap // 4), 'wrong LK branch'
    assert struct.unpack_from('<Q', wrapped, 8)[0] == 0x80000, 'wrong LK load offset'
    assert sha(args.kernel.read_bytes()) == '0b5c32f83858c5323bd484e577ac37159601a373db7031ac76b60623f9b077b5', 'kernel is not the device-verified charge2 bundle'
    release = kernel['kernel.release'][1].decode().strip()
    assert release == '7.2.9-u30air-charge2', release
    assert kernel['devices'][1].decode().strip() == 'u30air'
    modules = {name: value[1] for name, value in kernel.items() if name.startswith('modules/') and name.endswith('.ko')}
    assert len(modules) == 379, len(modules)
    for name, data in modules.items():
        assert data[:6] == b'\x7fELF\x02\x01', name
        assert int.from_bytes(data[18:20], 'little') == 183, name
        magic = re.search(rb'vermagic=([^\0]+)', data)
        assert magic and magic[1].decode().split()[0] == release, name
        installed = 'lib/modules/' + release + '/' + Path(name).name
        assert installed in root and root[installed][1] == data, ('rootfs module mismatch', installed)
    mappings = {
        'opt/mu300/bin/mu300-usb': 'rootfs/overlay/opt/mu300/bin/mu300-usb',
        'opt/mu300/bin/mu300-fcc-record': 'rootfs/overlay/opt/mu300/bin/mu300-fcc-record',
        'opt/mu300/lib/fcc-device.sh': 'rootfs/overlay/opt/mu300/lib/fcc-device.sh',
        'opt/mu300/lib/fcc-record.sh': 'rootfs/overlay/opt/mu300/lib/fcc-record.sh',
        'opt/mu300/bin/mu300-update': 'rootfs/overlay/opt/mu300/bin/mu300-update',
        'opt/mu300/bin/mu300-next-boot': 'rootfs/overlay/opt/mu300/bin/mu300-next-boot',
        'opt/mu300/bin/mu300-power': 'rootfs/overlay/opt/mu300/bin/mu300-power',
        'etc/init.d/mu300-fcc-record': 'openwrt/overlay/etc/init.d/mu300-fcc-record',
        'etc/uci-defaults/90-mu300': 'openwrt/overlay/etc/uci-defaults/90-mu300',
        'usr/libexec/unisoc-modem/dashboard-info': 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/dashboard-info',
        'usr/libexec/unisoc-modem/power': 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/power',
        'www/luci-static/resources/view/mu300/home.js': 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/home.js',
        'www/luci-static/resources/view/mu300/power.js': 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/power.js',
    }
    for name, source in mappings.items():
        assert name in root, ('missing rootfs runtime', name)
        assert root[name][1] == (args.repo / source).read_bytes(), ('source/archive mismatch', name)
        assert root[name][0].uid == 0 and root[name][0].gid == 0, ('wrong runtime ownership', name)
        assert not root[name][0].mode & 0o022, ('runtime writable by group/other', name)
        if name.endswith('mu300-fcc-record') or name.endswith('mu300-usb') or name.endswith('dashboard-info'):
            assert root[name][0].mode == 0o755, ('wrong executable mode', name, oct(root[name][0].mode))
    ramdisk = cpio(unlz4(kernel['ramdisk-generic.lz4'][1]))
    assert ramdisk['init'] == (args.repo / 'boot/init').read_bytes(), 'ramdisk uses a different init'
    boot_modules = [name for name in ramdisk if name.endswith('.ko')]
    assert boot_modules, 'ramdisk contains no modules'
    for name in boot_modules:
        key = 'modules/' + Path(name).name
        assert key in modules and ramdisk[name] == modules[key], ('ramdisk module mismatch', name)
    result = {'kernel_release': release, 'module_count': len(modules), 'ramdisk_modules': len(boot_modules),
              'runtime_files_match': len(mappings), 'kernel_bundle_sha256': sha(args.kernel.read_bytes()),
              'rootfs_sha256': sha(args.rootfs.read_bytes()), 'ramdisk_init_sha256': sha(ramdisk['init']),
              'embedded_image_sha256': sha(wrapped[gap:]), 'lk_wrapper_verified': True}
    if args.official_rootfs:
        official = members(args.official_rootfs)
        changed = []
        for name in sorted(set(official) | set(root)):
            a, b = official.get(name), root.get(name)
            if not a or not b or (a[0].type, a[0].mode, a[0].linkname, a[1]) != (b[0].type, b[0].mode, b[0].linkname, b[1]):
                changed.append(name)
            if b and b[0].isfile() and b[0].mode & 0o002:
                assert a and a[0].mode & 0o002, ('new world-writable regular file', name)
        result['changed_vs_official_rootfs'] = changed
        assert root['etc/mu300/packages.txt'][1] == official['etc/mu300/packages.txt'][1], 'official package list changed'
        assert root['usr/share/ucode/luci/dispatcher.uc'][1] == official['usr/share/ucode/luci/dispatcher.uc'][1], 'LuCI dispatcher changed'
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
