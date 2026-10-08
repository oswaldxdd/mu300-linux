#!/usr/bin/env python3
"""Build in an isolated Linux directory, preserving all previous prototypes."""
import os
import subprocess as sp
import shutil
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = Path(os.environ.get('U30_BUILD', '/root/u30air-native12-build-20261005'))
REPO = Path(os.environ.get('U30_REPO', '/root/src/mu300-linux'))
BASE = '65d1bc496aa8a15cbf535d7a746aa249bf2aa9d3'
TOP = BUILD / 'project'
WORK = BUILD / 'kernel-work'
TREE = WORK / 'linux-7.2.8'
BUILD.mkdir(parents=True, exist_ok=True)
(BUILD / 'logs').mkdir(exist_ok=True)
TOP.mkdir(exist_ok=True)
WORK.mkdir(exist_ok=True)
if not (TOP / '.prepared').exists():
    archive = sp.run(['git', '-C', str(REPO), 'archive', BASE], check=True, stdout=sp.PIPE).stdout
    sp.run(['tar', '-x', '-C', str(TOP)], input=archive, check=True)
    (TOP / '.prepared').write_text(BASE + '\n')
if not TREE.exists():
    sp.run(['cp', '-a', '--reflink=auto', '/src/linux-7.2.8', str(TREE)], check=True)
for filename in ('sgm41511-native.c', 'sgm41511-policy.h', 'host-policy.h', 'android-charge.inc', 'android-charge-policy.h', 'fgu-soc-policy.h', 'fgu-soc-reconcile.inc'):
    target = TOP / 'upstream/port/drivers/power/supply' / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / 'source' / filename, target)
header = TREE / 'include/linux/usb/u30air-usb.h'
shutil.copy2(HERE / 'source/u30air-usb.h', header)
udc = TREE / 'drivers/usb/gadget/udc/core.c'
s = udc.read_text()
if 'int usb_u30air_gadget_busy(void)' not in s:
    s = '#include <linux/usb/u30air-usb.h>\n' + s
    s += '\n' + (HERE / 'source/udc-observer.c.inc').read_text()
    udc.write_text(s)
drd = TREE / 'drivers/usb/dwc3/drd.c'
s = drd.read_text()
if 'usb_u30air_gadget_busy()' not in s:
    s = '#include <linux/usb/u30air-usb.h>\n' + s
    needle = '\tdwc3_pre_set_role(dwc, role);\n\tdwc3_set_mode(dwc, mode);\n\treturn 0;'
    assert s.count(needle) == 1
    s = s.replace(needle, '''	/* U30 Air: never take the data role away from an enumerating PC. */
	if (IS_ENABLED(CONFIG_CHARGER_SGM41511_NATIVE) &&
	    of_device_is_compatible(dwc->dev->of_node, "snps,sprd-dwc3") &&
	    role == USB_ROLE_HOST &&
	    dwc->current_dr_role == DWC3_GCTL_PRTCAP_DEVICE &&
	    usb_u30air_gadget_busy() != 0)
		return -EBUSY;
''' + needle)
    drd.write_text(s)
# Start from the pinned ported FGU source so upstream patch checks remain
# meaningful on rebuild; apply this revision after the upstream patch phase.
fgu_base = Path('/src/linux-7.2.8/drivers/power/supply/sc27xx_fuel_gauge.c')
assert hashlib.sha256(fgu_base.read_bytes()).hexdigest() == '949559cc377703fb27e4b5547f8ae539aa84614607dd4c3dc3dbc2c7d96cc9bd'
shutil.copy2(fgu_base, TREE/'drivers/power/supply/sc27xx_fuel_gauge.c')
tcpm = TREE / 'drivers/usb/typec/tcpm/sprd_tcpm.c'
s = tcpm.read_text()
marker = 'U30 Air external-power build: make this PD port sink-only.'
if marker not in s:
    needle = '\tport->typec_caps.type = ret;\n\tport->port_type = port->typec_caps.type;'
    assert s.count(needle) == 1
    s = s.replace(needle, needle + '''

	/* ''' + marker + '''
	 * USB data role remains under mu300-usb policy; this port only negotiates
	 * incoming power and cannot source or accept automatic role swaps.
	 */
#ifdef CONFIG_CHARGER_SGM41511_NATIVE
	port->typec_caps.type = TYPEC_PORT_SNK;
	port->port_type = TYPEC_PORT_SNK;
#endif''')
    tcpm.write_text(s)
tcpm = TREE / 'drivers/usb/typec/tcpm/sprd_tcpm.c'
s = tcpm.read_text()
marker = 'U30 Air external-power build: mu300-usb owns the DWC3 data role.'
if marker not in s:
    needle = '''	if (port->role_sw) {
		ret = usb_role_switch_set_role(port->role_sw, usb_role);
		if (ret)
			return ret;
	}'''
    assert s.count(needle) == 1
    s = s.replace(needle, '''#ifdef CONFIG_CHARGER_SGM41511_NATIVE
	/* ''' + marker + ''' */
	(void)usb_role;
#else
''' + needle + '''
#endif''')
    tcpm.write_text(s)
typec = TREE / 'drivers/usb/typec/sc27xx_typec.c'
s = typec.read_text()
if 'U30 native12: hardware CC controller remains a power sink.' not in s:
    needle = '\tsc->mode = mode;'
    assert s.count(needle) == 1
    s = s.replace(needle, needle + '''
#ifdef CONFIG_CHARGER_SGM41511_NATIVE
\t/* U30 native12: hardware CC controller remains a power sink. */
\tsc->mode = TYPEC_PORT_UFP;
#endif''')
    typec.write_text(s)
marker = 'U30 Air external-power build: preserve PD extcon but leave data role to mu300-usb.'
if marker not in s:
    needle = 'static int sc27xx_connect_set_status_use_pdhubc2c'
    assert s.count(needle) == 1
    helper = '''/* ''' + marker + '''
 * The charger policy owns DWC3 role changes; retain the separate SINK/SOURCE
 * extcon states consumed by the PD policy manager.
 */
static void u30air_set_typec_data_role(struct sc27xx_typec *sc,
				       unsigned int cable, bool connected)
{
#ifdef CONFIG_CHARGER_SGM41511_NATIVE
	(void)sc;
	(void)cable;
	(void)connected;
#else
	extcon_set_state_sync(sc->edev, cable, connected);
#endif
}

'''
    s = s.replace(needle, helper + needle)
    usb = 'extcon_set_state_sync(sc->edev, EXTCON_USB,'
    host = 'extcon_set_state_sync(sc->edev, EXTCON_USB_HOST,'
    assert s.count(usb) >= 4 and s.count(host) >= 4
    s = s.replace(usb, 'u30air_set_typec_data_role(sc, EXTCON_USB,')
    s = s.replace(host, 'u30air_set_typec_data_role(sc, EXTCON_USB_HOST,')
    typec.write_text(s)
tcpm = TREE / 'drivers/usb/typec/tcpm/sprd_tcpm.c'
s = tcpm.read_text()
marker = 'U30 native12: restrict sink capabilities to the stock fixed 5V PDO.'
if marker not in s:
    needle = '\tport->nr_snk_default_pdo = port->nr_snk_pdo;'
    assert s.count(needle) == 1
    s = s.replace(needle, '#ifdef CONFIG_CHARGER_SGM41511_NATIVE\n\t/* U30 native12: restrict sink capabilities to the stock fixed 5V PDO. */\n\tif (sprd_pdo_type(port->snk_pdo[0]) != SPRD_PDO_TYPE_FIXED ||\n\t    sprd_pdo_fixed_voltage(port->snk_pdo[0]) != 5000)\n\t\treturn -EINVAL;\n\tport->nr_snk_pdo = 1;\n#endif\n' + needle)
    tcpm.write_text(s)
p = TOP / 'upstream/port/install.py' 
s = p.read_text()
if 'CHARGER_SGM41511_NATIVE' not in s:
    s += '''
append_once('drivers/power/supply/Makefile', 'sgm41511-native.o',
            'obj-$(CONFIG_CHARGER_SGM41511_NATIVE) += sgm41511-native.o\\n')
append_once('drivers/power/supply/Kconfig', 'config CHARGER_SGM41511_NATIVE',
''' + repr('''
config CHARGER_SGM41511_NATIVE
    bool "U30 Air native externally-powered USB Host policy"
    depends on I2C && OF && POWER_SUPPLY && REGULATOR && USB_ROLE_SWITCH && USB_GADGET && USB
    help
      Native automatic data role policy. Never enables OTG boost or changes
      charge limits. Requires the local UDC observation patch.
''') + ')\n'
    p.write_text(s)
config = sp.check_output(['git', '-C', str(REPO), 'show', BASE + ':upstream/mu300-mainline.config'], text=True)
# Replace existing values, do not append contradictory settings.
options = dict(CONFIG_POWER_SUPPLY='y', CONFIG_CHARGER_SGM41511_NATIVE='y',
               CONFIG_TYPEC='y', CONFIG_EXTCON='y', CONFIG_NVMEM='y',
               CONFIG_USB_SC27XX_TYPEC='y', CONFIG_SC27XX_PD='y', CONFIG_SPRD_TYPEC_TCPM='y', CONFIG_CHARGER_MANAGER='n',
               CONFIG_LOCALVERSION='"-u30air-native12"', CONFIG_LOCALVERSION_AUTO='n')
config = '\n'.join(l for l in config.splitlines() if l.split('=')[0] not in options)
config += '\n' + '\n'.join(f'{k}={v}' for k,v in options.items()) + '\n'
(TOP / 'upstream/mu300-mainline.config').write_text(config)
for name in ('build.sh', 'build-modules.sh'):
    script = sp.check_output(['git','-C',str(REPO),'show',BASE + ':upstream/' + name], text=True)
    script = script.replace('/work', str(TOP / 'upstream')).replace('/src', str(WORK))
    if name == 'build.sh':
        needle = 'O=' + str(WORK) + '/out-$KV'
        assert script.count(needle) == 1
        script = script.replace(needle, 'python3 "' + str(HERE/'patch-fgu.py') + '" "' + str(TREE) + '"\n' + needle)
    (TOP / 'upstream' / name).write_text(script)
for name in ('test-host-policy', 'mu300-usb'):
    sp.run(['gcc','-Wall','-Wextra','-Werror','-O2', str(HERE/'source'/f'{name}.c'), '-o',str(BUILD/name)],check=True)
sp.run([str(BUILD/'test-host-policy')], check=True)
sp.run(['aarch64-linux-gnu-gcc','-Wall','-Wextra','-Werror','-Wformat=2','-O2','-static',
        str(HERE/'source/mu300-usb.c'),'-o',str(TOP/'rootfs/overlay/opt/mu300/bin/mu300-usb')],check=True)
env = dict(os.environ, KV='7.2.8', OUTDIR='out-native12', CROSS_COMPILE='aarch64-linux-gnu-')
for name in ('build.sh','build-modules.sh'):
    print('Building',name,flush=True)
    with (BUILD/'logs'/f'{name}.log').open('w') as log:
        sp.run(['bash',str(TOP/'upstream'/name)],env=env,stdout=log,stderr=sp.STDOUT,check=True)
sp.run(['make','-C',str(TREE),'O='+str(WORK/'out-7.2.8'),'ARCH=arm64','CROSS_COMPILE=aarch64-linux-gnu-',
        'W=1','drivers/power/supply/sgm41511-native.o'],check=True)
env['MU300_UPSTREAM_OUT'] = str(TOP/'upstream/out-native12')
sp.run(['sh',str(TOP/'upstream/make-bundle.sh'),str(BUILD/'mu300-kernel-7.2.8-u30air-native12.tar.gz'),
        '/root/mu300-reference/mu300-kernel.tar.gz'],env=env,check=True)
print('BUILD COMPLETE',flush=True)
