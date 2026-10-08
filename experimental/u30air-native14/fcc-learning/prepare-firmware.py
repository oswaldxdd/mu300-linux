"""Prepare native14 full-release layout without mutating native13 templates."""
from pathlib import Path
import os
import shutil

here=Path(__file__).resolve().parent
base=here.parent/'firmware'
layout=here/'firmware'
layout.mkdir(exist_ok=True)
def merge_tree(src,dst):
    """Repeated preparation retains identical startup links without deletion."""
    dst.mkdir(exist_ok=True,parents=True)
    for p in src.iterdir():
        target=dst/p.name
        if p.is_symlink():
            link=os.readlink(p)
            if target.is_symlink():
                assert os.readlink(target)==link,target
            else:
                assert not target.exists(),target
                target.symlink_to(link)
        elif p.is_dir():
            assert not target.is_symlink(),target
            merge_tree(p,target)
        else:
            assert not target.is_symlink(),target
            shutil.copy2(p,target)
release_scripts={'install-device.sh','rollback-device.sh','install.ps1',
                 'install.cmd','rollback.ps1','collect-status.sh','verify-delivery.py'}
for p in base.iterdir():
    if p.is_file() and p.suffix in ('.py','.sh','.ps1','.cmd'):
        text=p.read_text(encoding='utf-8-sig')
        if p.name in release_scripts:
            text=text.replace('native13','native14').replace('Native13','Native14')
        (layout/p.name).write_text(text)
for name in ('source','overlay','android-reference'):
    merge_tree(base/name,layout/name)
for p in (here/'source').iterdir():
    if p.is_file():shutil.copy2(p,layout/'source'/p.name)
shutil.copy2(base/'.gitattributes',layout/'.gitattributes')
script=(base/'assemble.py').read_text()
script=script.replace('/root/u30air-fcc-development-20261005','/root/u30air-native14-build-20261005')
script=script.replace('native13','native14')
recovery="HERE.parent.parent/'usb-host-native-power/firmware/release'"
assert script.count(recovery)==1
script=script.replace(recovery,'Path('+repr(str(here.parent.parent/'usb-host-native-power/firmware/release'))+')')
script=script.replace("'fcc-policy.h', 'fcc-state.h', 'fcc-kernel.inc', 'sc27xx_fuel_gauge.c'):",
    "'fcc-policy.h', 'fcc-state.h', 'fcc-kernel.inc', 'fcc-quiet-policy.h', 'sc27xx_fuel_gauge.c'):")
script=script.replace('protected full endpoint plus passive Android-style low-OCV-to-full FCC learning',
    'protected full endpoint plus bounded charger-managed low-OCV-to-full FCC learning; measured-span capacity formula;600s lease with actual inhibit readback and stable real buffered reference')
# Assembly is permitted only after the actual complete builder verified outputs.
needle="    OUT.mkdir(exist_ok=True)"
assert script.count(needle)==1
script=script.replace(needle,needle+"\n    assert json.loads((BUILD/'build-state.json').read_text())['phase']=='kernel_and31modules_complete_not_deployed'\n    snapshot=json.loads((BUILD/'BUILD-MANIFEST.json').read_text())\n    for name,expected in snapshot['source_sha256'].items():\n        assert sha(HERE/'source'/name)==expected, name")
(layout/'assemble.py').write_text(script)
component=layout/'fcc-learning'
component.mkdir(exist_ok=True)
for p in here.iterdir():
    if p.is_file() and p.suffix in ('.py','.c','.md'):
        shutil.copy2(p,component/p.name)
shutil.copytree(here/'source',component/'source',dirs_exist_ok=True)
shutil.copytree(here.parent/'runtime',component/'runtime',dirs_exist_ok=True)
shutil.copytree(here.parent/'runtime',here/'runtime',dirs_exist_ok=True)
(layout/'README.md').write_text('''# U30 Air native14 capacity-learning test firmware

Complete rootfs/kernel/matching modules and corresponding sources. Hardware
boot, quiet-reference learning, real capacity accuracy and charging proof are
pending until live acceptance; local regression tests do not prove them.

Corrects old-model residual bias using measured net charge divided by the
qualified charged fraction. Adds a bounded600second charger lease to acquire
actual low-current buffered OCV while preserving external SYS power. Real
inhibit readback and stable low-reference samples are mandatory. Timeout,
fault, disconnection or teardown releases to ordinary Android/JEITA charging.
OTG boost stays off and existing RTL8153B sharing/hotplug support is retained.
Hardware guards, SYS/BATFET and stock DTBO remain unchanged.

Full installation preserves per-device cellular/WiFi resources. The separate
settings-preserving kernel/runtime transaction uses exactly this firmware's
kernel and FCC runtime and retains a verified native13 recovery snapshot.
Only remove older backups after boot and recovery validation. Never delete
the learned capacity record as part of backup cleanup.
''')
# Prepare a fresh native13 -> native14 kernel/runtime transaction. Do not allow
# the old first-service-only snapshot retry path on a baseline with a service.
text=(here.parent/'install-native13-runtime.sh').read_text()
text=text.replace('native13','native14').replace('Native13','Native14')
text=text.replace('native12','native13').replace('Native12','Native13')
start=text.index('if [ -n "${MU300_RECOVERY_SNAPSHOT:-}" ]; then')
finish=text.index('\nelse\nmkdir -p "$BACKUP/runtime-before"',start)
end=text.index('\nfi\n',finish)+len('\nfi')
fresh=text[finish+len('\nelse\n'):end-len('\nfi')]
text=text[:start]+('[ -z "${MU300_RECOVERY_SNAPSHOT:-}" ] || die '\
    "'native14 requires a fresh verified native13 snapshot'\n")+fresh+text[end:]
(here/'install-native14-runtime.sh').write_text(text)
rollback=(here.parent/'rollback-native13-runtime.sh').read_text()
rollback=rollback.replace('native13','native14').replace('Native13','Native14')
rollback=rollback.replace('native12','native13').replace('Native12','Native13')
(here/'rollback-native14-runtime.sh').write_text(rollback)
runtime_script=(here.parent/'prepare-runtime-update.py').read_text().replace('native13','native14').replace('native12','native13')
(here/'prepare-runtime-update.py').write_text(runtime_script)
print('NATIVE14_LAYOUT_PREPARED: full firmware and fresh-snapshot transaction templates; not assembled/deployed')
