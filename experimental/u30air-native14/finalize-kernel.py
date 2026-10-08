"""Rebuild the changed driver and relink/stage the final kernel bundle."""
from pathlib import Path
import os
import shutil
import subprocess as sp
HERE = Path(__file__).resolve().parent
BUILD = Path('/root/u30air-native12-build-20261005')
TREE = BUILD/'kernel-work/linux-7.2.8'
OUT = BUILD/'kernel-work/out-7.2.8'
TOP = BUILD/'project'
sp.run(['python3', str(HERE/'refine-fgu.py'), str(TREE)], check=True)
for name in ('sgm41511-native.c', 'sgm41511-policy.h', 'host-policy.h', 'android-charge.inc', 'android-charge-policy.h', 'fgu-soc-policy.h', 'fgu-soc-reconcile.inc'):
    for dest in (TREE/'drivers/power/supply', TOP/'upstream/port/drivers/power/supply'):
        shutil.copy2(HERE/'source'/name, dest/name)
cmd = ['make','-C',str(TREE),'O='+str(OUT),'ARCH=arm64','CROSS_COMPILE=aarch64-linux-gnu-']
sp.run(cmd+['W=1','drivers/power/supply/sgm41511-native.o','drivers/power/supply/sc27xx_fuel_gauge.o'],check=True)
with (BUILD/'logs/final-kernel.log').open('w') as log:
    sp.run(cmd+['-j12','Image'],stdout=log,stderr=sp.STDOUT,check=True)
shutil.copy2(OUT/'arch/arm64/boot/Image', TOP/'upstream/out-native12/Image')
env=dict(os.environ, MU300_UPSTREAM_OUT=str(TOP/'upstream/out-native12'))
sp.run(['sh',str(TOP/'upstream/make-bundle.sh'),str(BUILD/'mu300-kernel-7.2.8-u30air-native12.tar.gz'),'/root/mu300-reference/mu300-kernel.tar.gz'],env=env,check=True)
