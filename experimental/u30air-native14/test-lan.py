#!/usr/bin/env python3
"""Exercise the real event helper with fake sysfs and link commands."""
import json
import os
from pathlib import Path
import subprocess as sp
import tempfile
from collections import defaultdict
HERE=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='u30-lan-test-') as tmp:
    root=Path(tmp); bindir=root/'bin'; bindir.mkdir()
    net=root/'sys/class/net'; net.mkdir(parents=True)
    (net/'br-lan').mkdir()
    calls=root/'calls'
    def executable(p,s): p.write_text(s); p.chmod(0o755)
    executable(bindir/'uci', '#!/bin/sh\necho "${TEST_LAN:-br-lan}"\n')
    executable(bindir/'ip', '#!/usr/bin/python3\nimport sys,json,os\nwith open(os.environ["CALLS"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n')
    executable(bindir/'logger', '#!/bin/sh\nexit 0\n')
    def nic(name,vendor,product,driver):
        device=root/'sys/bus/usb/devices'/name
        interface=device/(name+'-if0'); interface.mkdir(parents=True)
        (device/'idVendor').write_text(vendor); (device/'idProduct').write_text(product)
        target=root/'drivers'/driver; target.mkdir(parents=True,exist_ok=True)
        (interface/'driver').symlink_to(target)
        port=net/name; port.mkdir(); (port/'device').symlink_to(interface)
    nic('enx001122334455','0bda','8153','r8152')
    nic('eth9','0bda','8153','r8152')
    nic('eth0','1234','8153','r8152')
    nic('eth1','0bda','8153','other')
    nic('eth2','0bda','8152','r8152')
    s=(HERE/'overlay/opt/mu300/bin/mu300-lan-usb').read_text()
    s=s.replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin:/opt/mu300/bin',f'PATH={bindir}:/usr/bin:/bin')
    s=s.replace('/sys/class/net',str(net))
    helper=root/'helper'; executable(helper,s)
    env=dict(os.environ,CALLS=str(calls))
    sp.run(['sh',str(helper)],env=env,check=True)
    rows=[json.loads(x) for x in calls.read_text().splitlines()]
    by_iface=defaultdict(list)
    for row in rows:
        assert row[:2]==['link','set']
        by_iface[row[3]].append(row[4:])
    assert set(by_iface)=={'enx001122334455','eth9'}
    assert all(ops==[['master','br-lan'],['up']] for ops in by_iface.values())
    bridge=root/'sys/devices/virtual/net/br-lan'; bridge.mkdir(parents=True)
    other=root/'sys/devices/virtual/net/br-guest'; other.mkdir()
    (net/'enx001122334455/master').symlink_to(bridge)
    (net/'eth9/master').symlink_to(other)
    before=len(rows)
    sp.run(['sh',str(helper)],env=env,check=True)
    rows=[json.loads(x) for x in calls.read_text().splitlines()]
    assert rows[before:]==[['link','set','dev','enx001122334455','up']], \
        'existing LAN members are brought up; foreign bridge members remain unchanged'
    before=len(rows)
    sp.run(['sh',str(helper)],env=dict(env,TEST_LAN='custom-bridge'),check=True)
    assert len(calls.read_text().splitlines())==before
    print('PASS: RTL8153B filtering, bridge attachment/up, idempotence, foreign bridge and non-target LAN preserved')
