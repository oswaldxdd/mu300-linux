#!/usr/bin/env python3
"""Independent archive-level QA for U30 Air native14 panel1. Does not prove boot or hardware."""
import argparse, hashlib, io, json, tarfile, zipfile
from pathlib import Path, PurePosixPath

def digest(b): return hashlib.sha256(b).hexdigest()
def members(data):
 result={}
 with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as t:
  for m in t:
   p=PurePosixPath(m.name)
   assert not p.is_absolute() and '..' not in p.parts, m.name
   n=str(p);assert n not in result,n
   result[n]=(m,t.extractfile(m).read() if m.isfile() else None)
 return result
def identity(item):
 m,b=item
 return {'type':m.type.decode(),'mode':oct(m.mode),'link':m.linkname,'sha256':digest(b) if b is not None else None}
def sums(files,key,prefix=''):
 records={}
 for line in files[key].decode().splitlines():
  h,n=line.split('  ',1);assert n not in records;n=prefix+n;records[n]=h;assert digest(files[n])==h,n
 assert set(files)-{key}==set(records),'checksum coverage mismatch'
def verify(base,out):
 release={p.relative_to(out).as_posix():p.read_bytes() for p in out.rglob('*') if p.is_file()}
 sums(release,'SHA256SUMS')
 delivery=json.loads(release['delivery.json']);assert digest(release[delivery['archive']])==delivery['sha256']
 new=members(release[delivery['archive']]);old=members((base/delivery['archive']).read_bytes())
 for name in ['package/SHA256SUMS','package/BUILD-MANIFEST.json','package/mu300-update','package/mu300-kernel-7.2.8-u30air-native14.tar.gz']:
  assert new[name][0].name==name,'exact package reader path incompatible '+name
 sums({n:b for n,(m,b) in new.items() if m.isfile()},'package/SHA256SUMS','package/')
 for n,(m,b) in old.items():
  if m.isfile() and n not in ['package/SHA256SUMS','package/BUILD-MANIFEST.json','package/mu300-openwrt-rootfs.tar.gz']:assert new[n][1]==b,'non-rootfs runtime payload changed '+n
 manifest=json.loads(new['package/BUILD-MANIFEST.json'][1]);assert new['package/BUILD-MANIFEST.json'][1]==release['BUILD-MANIFEST.json']
 assert manifest['device']=='u30air' and manifest['kernel_release']=='7.2.8-u30air-native14'
 assert manifest['hardware_panel_tested'] is False and manifest['independent_capacity_accuracy_verified'] is False
 root=members(new['package/mu300-openwrt-rootfs.tar.gz'][1]);prior=members(old['package/mu300-openwrt-rootfs.tar.gz'][1])
 integration=json.loads(release['panel-integration.json']);assert integration==json.loads(root['etc/mu300/panel-integration.json'][1])
 assert root['etc/mu300/image-version'][1].decode().strip()==manifest['version']
 reported={r['path']:r for r in integration['rootfs_changes']}
 actual={n for n in set(root)|set(prior) if n!='etc/mu300/panel-integration.json' and (n not in prior or n not in root or identity(prior[n])!=identity(root[n]))}
 assert set(reported)==actual,'rootfs change ledger mismatch'
 allowed=['etc/config/unisoc_modem','etc/config/luci','etc/mu300/image-version','etc/hotplug.d/iface/90-unisoc-usb-host','etc/hotplug.d/net/90-unisoc-usb-host','etc/init.d/unisoc-modem-ui','etc/init.d/mu300-atd-dash','etc/init.d/mu300-smsd','etc/rc.d/S19mu300-atd-dash','etc/rc.d/S95unisoc-modem-ui','etc/rc.d/S95mu300-smsd','opt/mu300/bin/mu300-sms','opt/mu300/bin/mu300-smsd','opt/mu300/bin/mobile-data','usr/bin/mu300-sms']
 prefixes=['www/luci-static/resources/mu300/','www/luci-static/resources/view/mu300/','usr/libexec/unisoc-modem/','usr/share/unisoc-modem/']
 allowed.extend(['opt/mu300/bin/mu300-atd','usr/libexec/rpcd/mu300dash','usr/share/luci/menu.d/luci-app-mu300.json','usr/share/rpcd/acl.d/luci-app-mu300.json','usr/lib/lua/luci/i18n/mu300.tr.lmo','usr/lib/lua/luci/i18n/mu300.zh-cn.lmo'])
 for n in actual:
  assert n in root,'rootfs deletion forbidden'
  m,b=root[n]
  assert m.isdir() or n in allowed or any(n.startswith(p) for p in prefixes),'unapproved change '+n
  row=reported[n];assert row['after']==identity(root[n]);assert row['before']==(identity(prior[n]) if n in prior else None)
 for n,h in integration['runtime_files_sha256'].items():
  m,b=root[n];assert digest(b)==h,n
  if b.startswith(b'#!'):assert m.mode&0o111,n
 for n in integration['required_dependencies']:assert n in root,n
 for n in ['usr/share/luci/menu.d/luci-app-mu300.json','usr/share/rpcd/acl.d/luci-app-mu300.json']:json.loads(root[n][1])
 source=members(release['corresponding-project-source.tar.gz']);prior_source=members((base/'corresponding-project-source.tar.gz').read_bytes())
 for n,item in prior_source.items():assert identity(source[n])==identity(item),'original corresponding source changed '+n
 source_prefix='panel-integration/source/'
 for n,h in integration['runtime_files_sha256'].items():
  if n=='etc/config/luci' or n.endswith('.lmo'):continue
  candidates=[source_prefix+'openwrt/luci-app-mu300/root/'+n,source_prefix+'openwrt/luci-app-mu300/htdocs/'+n.removeprefix('www/'),source_prefix+'openwrt/luci-overlay/'+n,source_prefix+'rootfs/overlay/'+n]
  assert any(c in source and digest(source[c][1])==h for c in candidates),'corresponding source missing '+n
 assert release['corresponding-linux-7.2.8-source.tar.gz']==(base/'corresponding-linux-7.2.8-source.tar.gz').read_bytes()
 z=out.parent/(manifest['version']+'.zip')
 with zipfile.ZipFile(z) as a:
  expected={'u30air-native14-panel1/'+n for n in release};assert set(a.namelist())==expected
  for n,b in release.items():assert a.read('u30air-native14-panel1/'+n)==b,n
 assert Path(str(z)+'.sha256').read_text().split()[0]==digest(z.read_bytes())
 return {'status':'PASS','rootfs_changes':len(actual),'runtime_files':len(integration['runtime_files_sha256']),'protected_kernel_modules_network_fcc':'byte identical','corresponding_source':'complete baseline retained plus exact panel source and recipe','zip_sha256':digest(z.read_bytes()),'hardware_validation':False}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(verify(a.base,a.output),indent=2))
