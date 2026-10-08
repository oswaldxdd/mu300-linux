"""Build native14 in new Linux trees; never alter native13 or flash a device."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess as sp

here = Path(__file__).resolve().parent
base = Path('/root/u30air-fcc-development-20261005')
vendor_base = Path('/root/u30air-native12-build-20261005')
build = Path('/root/u30air-native14-build-20261005')
tree, out, project = (build/'linux-7.2.8',build/'out-7.2.8',build/'project')
assert build != base and build != vendor_base
build.mkdir(exist_ok=True)
logs = build/'logs'
logs.mkdir(exist_ok=True)
state = build/'build-state.json'

def phase(value):
    state.write_text(json.dumps({'phase':value,'pid':os.getpid(),
                                'kernel_release':'7.2.8-u30air-native14'},indent=2)+'\n')
    print('NATIVE14_PHASE: '+value,flush=True)

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

inputs = {p.name:digest(p) for p in (here/'source').iterdir() if p.is_file()}
marker = build/'prepared-v1'
if not marker.exists():
    # Partial preparation must be audited explicitly; do not silently restart.
    assert not tree.exists() and not out.exists() and not project.exists()
    phase('copy_isolated_kernel_tree')
    shutil.copytree(base/'linux-7.2.8',tree,symlinks=True)
    phase('copy_isolated_build_outputs')
    shutil.copytree(base/'out-7.2.8',out,symlinks=True)
    phase('copy_isolated_vendor_project')
    shutil.copytree(base/'project',project,symlinks=True,
                    ignore=shutil.ignore_patterns('.git','out-native13','out-native12'))
    link = out/'source'
    if link.is_symlink():
        link.unlink()
        link.symlink_to(tree)
    marker.write_text('Copied independent regular files; no hardlinks to native13.\n')

phase('stage_native14_sources')
dest = tree/'drivers/power/supply'
for p in (here/'source').iterdir():
    if p.is_file():
        shutil.copy2(p,dest/p.name)
config = out/'.config'
text = config.read_text()
assert 'CONFIG_LOCALVERSION="-u30air-native13"' in text or 'CONFIG_LOCALVERSION="-u30air-native14"' in text
text = text.replace('CONFIG_LOCALVERSION="-u30air-native13"','CONFIG_LOCALVERSION="-u30air-native14"')
config.write_text(text)
cmd = ['make','-C',str(tree),'O='+str(out),'ARCH=arm64','CROSS_COMPILE=aarch64-linux-gnu-']
sp.run(cmd+['olddefconfig'],check=True)
phase('build_full_image')
with (logs/'native14-image.log').open('w') as log:
    sp.run(cmd+['-j12','Image'],stdout=log,stderr=sp.STDOUT,check=True)
outputs = project/'upstream/out-native14'
outputs.mkdir(exist_ok=True)
for name, origin in (
    ('Image',out/'arch/arm64/boot/Image'),
    ('modules.builtin',out/'modules.builtin'),
    ('modules.builtin.modinfo',out/'modules.builtin.modinfo'),
    ('kernel.release',out/'include/config/kernel.release')):
    shutil.copy2(origin,outputs/name)
assert (outputs/'kernel.release').read_text().strip()=='7.2.8-u30air-native14'
phase('build_matching_vendor_modules')
script=(vendor_base/'project/upstream/build-modules.sh').read_text()
script=script.replace(str(vendor_base/'kernel-work'),str(build)).replace(str(vendor_base/'project'),str(project))
script=script.replace('-j"$(nproc)"','-j12')
assert str(vendor_base) not in script
module_script=project/'upstream/build-modules-native14.sh'
module_script.write_text(script)
env=dict(os.environ,KV='7.2.8',OUTDIR='out-native14',CROSS_COMPILE='aarch64-linux-gnu-')
with (logs/'native14-modules.log').open('w') as log:
    sp.run(['bash',str(module_script)],env=env,stdout=log,stderr=sp.STDOUT,check=True)
modules=list((outputs/'modules').glob('*.ko'))
assert len(modules)==31,len(modules)
for p in modules:
    assert b'vermagic=7.2.8-u30air-native14 ' in p.read_bytes(),p
assert inputs=={p.name:digest(p) for p in (here/'source').iterdir() if p.is_file()},'Source changed during build'
for name in inputs:
    shutil.copy2(dest/name,project/'upstream/port/drivers/power/supply'/name)
phase('bundle_full_kernel_and_modules')
env['MU300_UPSTREAM_OUT']=str(outputs)
bundle=build/'mu300-kernel-7.2.8-u30air-native14.tar.gz'
sp.run(['sh',str(project/'upstream/make-bundle.sh'),str(bundle),
        '/root/mu300-reference/mu300-kernel.tar.gz'],env=env,check=True)
(build/'BUILD-MANIFEST.json').write_text(json.dumps({
    'kernel_release':'7.2.8-u30air-native14','source_sha256':inputs,
    'image_sha256':digest(outputs/'Image'),'config_sha256':digest(config),
    'kernel_bundle_sha256':digest(bundle),
    'modules_sha256':{p.name:digest(p) for p in modules},
    'full_firmware_packaged':False,'hardware_boot_tested':False,
    'hardware_capacity_learning_tested':False,
},indent=2)+'\n')
phase('kernel_and31modules_complete_not_deployed')
