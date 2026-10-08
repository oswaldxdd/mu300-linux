"""Exercise staged real worker, full endpoint regression and write lifecycle."""
from pathlib import Path
import re
import subprocess
import tempfile

here = Path(__file__).resolve().parent
baseline = here.parent.parent/'usb-host-soc-accuracy-v12/test-soc-worker.py'
if not baseline.exists():
    baseline = here.parent/'test-soc-worker.py'
code = re.search(r"code = r'''([\s\S]*?)'''",baseline.read_text()).group(1)
code = code.replace('#include "fgu-soc-policy.h"',
    '#include "fgu-soc-policy.h"\n#include "fcc-state.h"\n#include "fcc-quiet-policy.h"')
code = code.replace('POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX };',
    'POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX, POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR };\n'
    'enum {POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO, POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE};')
code = code.replace('bool soc_stopping, soc_full_verified;',
    'bool soc_stopping, soc_full_verified;\n struct u30_fcc_state fcc_state;\n'
    ' struct u30_quiet_state quiet_state; int quiet_error, fcc_pending;')
code = code.replace('#define mutex_lock(x) ((void)(x))',
    'static int lock_depth;\n#define mutex_lock(x) ((void)(x), lock_depth++)')
code = code.replace('#define mutex_unlock(x) ((void)(x))',
    '#define mutex_unlock(x) ((void)(x), lock_depth--)')
stub = r'''
static bool last_fcc_full;
static int command_error,last_command,command_count;
static enum u30_quiet_action requested_action;
static bool sc27xx_fgu_fcc_poll(struct sc27xx_fgu_data *d,bool full,
 struct power_supply *charger,enum u30_quiet_action *action) {
 (void)d;(void)charger;last_fcc_full=full;*action=requested_action;return false;
}
static int power_supply_set_property(struct power_supply *p,int key,
 const union power_supply_propval *v) {
 assert(!lock_depth&&p==&supply&&key==POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR);
 last_command=v->intval;command_count++;return command_error;
}
'''
worker = (here/'source/fgu-soc-reconcile.inc').read_text()
assert code.count('#include "fgu-soc-reconcile.inc"')==1
code = code.replace('#include "fgu-soc-reconcile.inc"',stub+worker)
warm = r'''
 for(int t=0;t<7;t++) {
  int temperatures[]={149,150,199,401,410,450,451};
  d=reset();last_fcc_full=false;temp=temperatures[t];vbat=4231;ocv=4231000;
  run(&d,12);assert(!d.soc_full_verified&&!last_fcc_full);
  run(&d,1);bool permitted=temp>=150&&temp<=450;
  assert(d.soc_full_verified==permitted&&last_fcc_full==permitted);
  assert(d.init_cap==(permitted?100:76));
 }
 d=reset();temp=410;run(&d,100);assert(d.init_cap==76&&!d.soc_full_verified);
 d=reset();temp=410;cur=-14;vbat=4231;ocv=4231000;
 run(&d,12);assert(!d.soc_full_verified&&d.soc_state.stable==12);
 cur=0;run(&d,1);assert(d.soc_full_verified&&d.init_cap==100&&last_fcc_full);
 d=reset();temp=410;props[0]=POWER_SUPPLY_STATUS_CHARGING;
 vbat=4231;ocv=4231000;run(&d,100);assert(d.init_cap==76&&!d.soc_full_verified);
 /* Actual worker writes outside FGU mutex and handles callback failure. */
 d=reset();requested_action=U30_QUIET_INHIBIT;command_count=0;
 run(&d,1);assert(command_count==1&&last_command==POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE);
 d.quiet_state.active=d.fcc_state.active=1;d.fcc_pending=3000;command_error=-5;
 run(&d,1);assert(d.quiet_error==-5&&!d.quiet_state.active&&!d.fcc_state.active&&!d.fcc_pending);
 assert(d.fcc_state.reason==U30_FCC_READ_ERROR);
 command_error=0;command_count=0;sc27xx_fgu_soc_stop(&d);
 assert(command_count==1&&last_command==POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO);
 run(&d,1);assert(last_command==POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO);
 assert(!lock_depth);
'''
assert code.count('sc27xx_fgu_soc_stop(&d);')==1
code = code.replace('sc27xx_fgu_soc_stop(&d);',warm+'sc27xx_fgu_soc_stop(&d);')
with tempfile.TemporaryDirectory(prefix='native14-worker-') as tmp:
    path=Path(tmp)
    (path/'worker.c').write_text(code)
    subprocess.run(['gcc','-Wall','-Wextra','-Werror','-fsanitize=undefined,address',
                    '-I'+str(here/'source'),str(path/'worker.c'),'-o',str(path/'worker')],check=True)
    subprocess.run([str(path/'worker')],check=True)
print('PASS: actual staged worker preserves native13 full/fault/dwell guards; charger writes outside lock; failed command clears learning; teardown/stopping forces AUTO')
