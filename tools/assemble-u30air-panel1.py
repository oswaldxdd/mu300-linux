#!/usr/bin/env python3
"""Offline U30 Air panel integration over a SHA-256 pinned complete native14 image.

No device connection, kernel recompilation, package downloads or APK installation.
Usage: python3 assemble-panel1.py --base RELEASE --repo SOURCE --output NEW_RELEASE
All new runtime files and the integration recipe are included in corresponding source.
"""
import argparse, copy, hashlib, importlib.util, io, json, os, shutil, subprocess, tarfile, tempfile, zipfile
from pathlib import Path, PurePosixPath

VERSION='u30air-native14-panel1-2026.10.08-test'
BASE_SHA='45d8ee5d82199226e10e4b753c8ad3abd02a504a06cfa5fb48e387272e3bd206'
SOURCE_SHA='2719af88a15c28780d96f040e10fcfffbf7b096db1a2b199b9cff5bf03572bc7'
LINUX_SHA='7065c5e06fbe81e497918366142f997bfe8b84c6222682ebbb6bc988fe35028e'
ARCHIVE='u30air-native14-firmware.tar.gz'

def sha(b): return hashlib.sha256(b).hexdigest()
def file_sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def norm(n):
 p=PurePosixPath(n)
 if p.is_absolute() or '..' in p.parts: raise ValueError('unsafe tar path '+n)
 return str(p)
def tar_map(data):
 result={}
 with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as t:
  for m in t:
   n=norm(m.name)
   if n in result: raise ValueError('duplicate tar member '+n)
   result[n]=(copy.copy(m),t.extractfile(m).read() if m.isreg() else None)
 return result
def put(d,n,b,mode=0o644,link=None):
 n=norm(n)
 # Reject a parent symlink: otherwise tar extraction could escape into a different tree.
 for parent in PurePosixPath(n).parents:
  if str(parent) in d and not d[str(parent)][0].isdir(): raise ValueError('non-directory parent '+str(parent))
 for parent in reversed(PurePosixPath(n).parents):
  p=str(parent)
  if p!='.' and p not in d:
   m=tarfile.TarInfo('./'+p);m.type=tarfile.DIRTYPE;m.mode=0o755;d[p]=(m,None)
 # Some consumers use exact getmember('package/SHA256SUMS'), not normalized paths.
 member_name=d[n][0].name if n in d else './'+n
 m=tarfile.TarInfo(member_name);m.mode=mode;m.uid=m.gid=0
 if link is not None: m.type=tarfile.SYMTYPE;m.linkname=link;d[n]=(m,None)
 else: m.size=len(b);d[n]=(m,b)
def write_tar(d,p):
 with tarfile.open(p,'w:gz',format=tarfile.PAX_FORMAT) as t:
  for n,(m,b) in d.items(): t.addfile(m,io.BytesIO(b) if b is not None else None)
def check_sums(d,key,prefix=''):
 rows=d[key][1].decode().splitlines()
 for row in rows:
  h,n=row.split('  ',1); n=prefix+norm(n)
  if sha(d[n][1])!=h: raise ValueError('checksum mismatch '+n)
def sums_tree(p):
 lines=[file_sha(f)+'  '+f.relative_to(p).as_posix()+'\n' for f in sorted(p.rglob('*')) if f.is_file() and f.name!='SHA256SUMS']
 (p/'SHA256SUMS').write_text(''.join(lines),encoding='utf-8')
def finalize(out):
 sums_tree(out)
 z=out.parent/(VERSION+'.zip')
 with zipfile.ZipFile(z,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as a:
  for p in sorted(out.rglob('*')):
   if p.is_file(): a.write(p,'u30air-native14-panel1/'+p.relative_to(out).as_posix())
 digest=file_sha(z)
 Path(str(z)+'.sha256').write_text(digest+'  '+z.name+'\n')
 print(json.dumps({'zip':str(z),'sha256':digest,'bytes':z.stat().st_size},ensure_ascii=False))
def build(base,repo,out):
 if out.exists(): raise FileExistsError(out)
 if (out.parent/(VERSION+'.zip')).exists(): raise FileExistsError('versioned ZIP already exists')
 for row in (base/'SHA256SUMS').read_text().splitlines():
  h,n=row.split('  ',1)
  if file_sha(base/n)!=h: raise ValueError('base release checksum '+n)
 for n,h in [(ARCHIVE,BASE_SHA),('corresponding-project-source.tar.gz',SOURCE_SHA),('corresponding-linux-7.2.8-source.tar.gz',LINUX_SHA)]:
  if file_sha(base/n)!=h: raise ValueError('pinned input '+n)
 package=tar_map((base/ARCHIVE).read_bytes());check_sums(package,'package/SHA256SUMS','package/')
 root=tar_map(package['package/mu300-openwrt-rootfs.tar.gz'][1])
 old=copy.deepcopy(root)
 manifest=json.loads(package['package/BUILD-MANIFEST.json'][1])
 if manifest['kernel_release']!='7.2.8-u30air-native14' or manifest['device']!='u30air': raise ValueError('wrong platform')
 panel=repo/'openwrt/luci-app-mu300'
 spec=importlib.util.spec_from_file_location('po2lmo',repo/'tools/po2lmo.py');po=importlib.util.module_from_spec(spec);spec.loader.exec_module(po)
 installed={};source_files={}
 for folder,prefix in [(panel/'root',''),(panel/'htdocs','www/'),(repo/'openwrt/luci-overlay','')]:
  for p in sorted(folder.rglob('*')):
   if not p.is_file(): continue
   n=prefix+p.relative_to(folder).as_posix()
   if n=='etc/uci-defaults/91-mu300-luci': raise ValueError('WAN modifying legacy defaults forbidden')
   b=p.read_bytes();mode=0o755 if b.startswith(b'#!') else 0o644
   put(root,n,b,mode);installed[n]=sha(b);source_files[p.relative_to(repo).as_posix()]=b
 # The existing dialer gains the panel lock API without changing APN, CID or IPv6 policy.
 for name in ['mobile-data','mu300-atd']:
  p=repo/'rootfs/overlay/opt/mu300/bin'/name;b=p.read_bytes()
  put(root,'opt/mu300/bin/'+name,b,0o755);installed['opt/mu300/bin/'+name]=sha(b);source_files[p.relative_to(repo).as_posix()]=b
 for lang,code in [('tr','tr'),('zh_Hans','zh-cn')]:
  p=panel/'po'/lang/'mu300.po'
  with tempfile.TemporaryDirectory() as tmp:
   target=Path(tmp)/'mu300.lmo'
   subprocess.run(['python3',str(repo/'tools/po2lmo.py'),str(p),str(target)],check=True)
   b=target.read_bytes()
  n='usr/lib/lua/luci/i18n/mu300.'+code+'.lmo';put(root,n,b);installed[n]=sha(b);source_files[p.relative_to(repo).as_posix()]=p.read_bytes()
 # Register languages in the already present UCI section, preserving every other option.
 n='etc/config/luci';text=root[n][1].decode()
 if text.count('config internal languages')!=1: raise ValueError('ambiguous LuCI languages section')
 text=text.replace('config internal languages','config internal languages\n\toption tr "Türkçe (Turkish)"\n\toption zh_cn "简体中文 (Chinese Simplified)"',1)
 put(root,n,text.encode());installed[n]=sha(text.encode())
 for n,target in [('etc/rc.d/S19mu300-atd-dash','../init.d/mu300-atd-dash'),('etc/rc.d/S95unisoc-modem-ui','../init.d/unisoc-modem-ui'),('etc/rc.d/S95mu300-smsd','../init.d/mu300-smsd'),('usr/bin/mu300-sms','/opt/mu300/bin/mu300-sms')]: put(root,n,b'',0o777,target)
 put(root,'etc/mu300/image-version',(VERSION+'\n').encode())
 required=['sbin/rpcd','sbin/uci','usr/bin/jsonfilter','usr/share/libubox/jshn.sh','www/luci-static/resources/luci.js','opt/mu300/bin/mu300-at','opt/mu300/bin/mu300-atd','opt/mu300/bin/mu300-usb','bin/bash']
 for n in required:
  if n not in root: raise ValueError('missing runtime dependency '+n)
 atd=root['opt/mu300/bin/mu300-atd'][1]
 for var in [b'MU300_AT_DEV',b'MU300_AT_DIR',b'MU300_AT_URC_CHANNELS']:
  if var not in atd: raise ValueError('AT daemon missing env API '+var.decode())
 commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
 changes=[]
 for n in sorted(set(old)|set(root)):
  before=old.get(n);after=root.get(n)
  def identity(x):
   if x is None:return None
   m,b=x;return {'type':m.type.decode(),'mode':oct(m.mode),'link':m.linkname,'sha256':sha(b) if b is not None else None}
  if identity(before)!=identity(after):changes.append({'path':n,'before':identity(before),'after':identity(after)})
 protected=['lib/modules/','etc/config/network','etc/config/wireless','opt/mu300/lib/fcc-record.sh','opt/mu300/bin/mu300-fcc-record','opt/mu300/bin/mu300-usb','opt/mu300/bin/mu300-lan-usb','etc/hotplug.d/net/90-u30air-lan']
 for c in changes:
  if any(c['path']==p or (p.endswith('/') and c['path'].startswith(p)) for p in protected):raise ValueError('protected payload changed '+c['path'])
 integration={'version':VERSION,'panel_base_commit':commit,'base_firmware_sha256':BASE_SHA,'kernel_recompiled':False,'hardware_validated':False,'battery_accuracy_fixed':False,'required_dependencies':required,'runtime_files_sha256':installed,'rootfs_changes':changes,'source_tree_state':'working tree integration files hashed individually'}
 put(root,'etc/mu300/panel-integration.json',(json.dumps(integration,indent=2,ensure_ascii=False)+'\n').encode())
 out.mkdir(parents=True)
 # Carry tools and legal source notices; old-version audit recipes remain explicitly historical.
 for p in base.iterdir():
  if p.is_file() and p.name not in [ARCHIVE,'SHA256SUMS','delivery.json','BUILD-MANIFEST.json','corresponding-project-source.tar.gz','README.md']:
   shutil.copy2(p,out/p.name)
 with tempfile.TemporaryDirectory(prefix='mu300-panel1-') as tmp:
  rp=Path(tmp)/'rootfs.tar.gz';write_tar(root,rp)
  put(package,'package/mu300-openwrt-rootfs.tar.gz',rp.read_bytes())
  manifest.update(version=VERSION,release_status='offline complete panel1 candidate; not flashed',hardware_boot_tested=False,hardware_charging_tested=False,hardware_panel_tested=False,panel_integration=integration)
  manifest['base_validation_history']={k:manifest.pop(k) for k in ['deployment_compatibility_tests','updater_idempotency_tests'] if k in manifest}
  manifest['runtime_restore_fix']='Inherited restorefix3 FCC persistence runtime, unchanged. Panel1 hardware validation and Android/OpenWrt SOC discrepancy remain pending.'
  manifest['hardware_fcc_learning_tested_scope']='prior native14 observation only; not this panel1 package'
  b=(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n').encode();put(package,'package/BUILD-MANIFEST.json',b)
  sums=''.join(sha(b)+'  '+n.removeprefix('package/')+'\n' for n,(m,b) in sorted(package.items()) if m.isreg() and n!='package/SHA256SUMS')
  put(package,'package/SHA256SUMS',sums.encode());write_tar(package,out/ARCHIVE)
 (out/'BUILD-MANIFEST.json').write_bytes(b)
 (out/'panel-integration.json').write_text(json.dumps(integration,indent=2,ensure_ascii=False)+'\n')
 (out/'delivery.json').write_text(json.dumps({'archive':ARCHIVE,'sha256':file_sha(out/ARCHIVE)},indent=2)+'\n')
 # Original kernel/driver source is retained byte-for-byte; append the exact panel source and recipe.
 for name in ['tools/po2lmo.py','openwrt/build-rootfs.sh','openwrt/luci-app-mu300/Makefile','openwrt/luci-app-mu300/README.md','tools/assemble-u30air-panel1.py','tools/verify-u30air-panel1.py','docs/U30AIR-PANEL1.md','tests/test_panel_runtime_contracts.py']:
  p=repo/name
  if not p.is_file():raise ValueError('missing corresponding source '+name)
  source_files[name]=p.read_bytes()
 with tarfile.open(base/'corresponding-project-source.tar.gz','r:gz') as inp, tarfile.open(out/'corresponding-project-source.tar.gz','w:gz',format=tarfile.PAX_FORMAT) as dst:
  for m in inp:dst.addfile(copy.copy(m),inp.extractfile(m) if m.isreg() else None)
  d={}
  for n,payload in source_files.items():put(d,'panel-integration/source/'+n,payload,0o755 if payload.startswith(b'#!') else 0o644)
  put(d,'panel-integration/manifest.json',(json.dumps(integration,indent=2,ensure_ascii=False)+'\n').encode())
  for m,payload in d.values():dst.addfile(m,io.BytesIO(payload) if payload is not None else None)
 (out/'README.md').write_bytes((repo/'docs/U30AIR-PANEL1.md').read_bytes())
 shutil.copy2(repo/'tools/assemble-u30air-panel1.py',out/'assemble-u30air-panel1.py')
 shutil.copy2(repo/'tools/verify-u30air-panel1.py',out/'verify-u30air-panel1.py')
 finalize(out)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--base',type=Path);p.add_argument('--repo',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--finalize',action='store_true');a=p.parse_args()
 if a.finalize:finalize(a.output)
 else:build(a.base,a.repo,a.output)
