"""Compile isolated adapter objects against pinned native13 kernel headers.

This does not update the pinned tree, build a kernel/module bundle or flash.
Run through WSL from the shared workspace. All outputs stay in this directory.
"""
from pathlib import Path
import hashlib
import json
import shlex
import subprocess

here = Path(__file__).resolve().parent
out = Path('/root/u30air-fcc-development-20261005/out-7.2.8')
results = []
for name in ('sgm41511-native', 'sc27xx_fuel_gauge'):
    record = out / f'drivers/power/supply/.{name}.o.cmd'
    line = record.read_text().splitlines()[0]
    assert line.startswith('savedcmd_') and ' := ' in line
    args = shlex.split(line.split(' := ', 1)[1])
    assert args[0] == 'aarch64-linux-gnu-gcc'
    # The saved dependency filename points into the old build: omit it.
    args = [a for a in args if not a.startswith('-Wp,-MMD,')]
    assert args[-1].endswith(f'/{name}.c')
    args[-1] = str(here / 'source' / f'{name}.c')
    assert args.count('-o') == 1
    args[args.index('-o') + 1] = str(here / f'{name}.o')
    args.insert(1, '-I' + str(here / 'source'))
    run = subprocess.run(args, cwd=out, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (here / f'{name}.compile.log').write_text(run.stdout)
    print(f'{name}: compile exit={run.returncode}')
    if run.returncode:
        print(run.stdout)
        raise SystemExit(run.returncode)
    target = here / f'{name}.o'
    results.append({'object': target.name,
                    'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                    'source': args[-1], 'command': args})
(here / 'adapter-compile.json').write_text(json.dumps({
    'scope': 'Object compilation only against pinned native13 headers; no kernel link or hardware proof',
    'objects': results,
    'local_source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in (here / 'source').iterdir() if p.is_file()},
}, indent=2) + '\n')
