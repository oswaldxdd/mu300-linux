"""Test actual FGU FCC coordinator with controlled battery/input snapshots."""
from pathlib import Path
import subprocess

here = Path(__file__).resolve().parent
src = (here/'source/fcc-kernel.inc').read_text()
actual = src[src.index('static bool sc27xx_fgu_fcc_poll('):src.index('/* Restore only a model')]
prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
#include "source/fcc-state.h"
#include "source/fcc-quiet-policy.h"
typedef uint32_t u32;
#define dev_info(...) ((void)0)
#define dev_warn_ratelimited(...) ((void)0)
struct power_supply {int unused;};
union power_supply_propval {int intval;};
enum {POWER_SUPPLY_PROP_ONLINE,POWER_SUPPLY_PROP_HEALTH,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR};
enum {POWER_SUPPLY_HEALTH_GOOD=1};
enum {POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO,POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE};
struct sc27xx_fgu_data {
 struct u30_fcc_state fcc_state;struct u30_quiet_state quiet_state;
 int cur_1000ma_adc,total_cap,design_cap,bat_present,fcc_restored,fcc_pending;
 unsigned int fcc_sequence;void *dev;
};
static unsigned int now,counter;
static int fail,temp,ocv,vbat,online,health,inhibited,noise,applies;
static unsigned int ktime_get_boottime_seconds(void){return now;}
static int sc27xx_fgu_get_temp(struct sc27xx_fgu_data *d,int *v){(void)d;*v=temp;return fail==1?-EIO:0;}
static int sc27xx_fgu_get_vbat_ocv(struct sc27xx_fgu_data *d,int *v){(void)d;*v=ocv;return fail==2?-EIO:0;}
static int sc27xx_fgu_get_vbat_vol(struct sc27xx_fgu_data *d,int *v){(void)d;*v=vbat;return fail==3?-EIO:0;}
static int u30_fcc_read_low(struct sc27xx_fgu_data *d,int t,struct u30_fcc_start_sample *p) {
 (void)d;*p=(struct u30_fcc_start_sample){.temp_decic=t,.ocv_uv=ocv,.cap_tenth=30,
  .reads_ok=1,.battery_present=1,.counter_continuous=1};
 for(int i=0;i<8;i++)p->buffered_ocv_uv[i]=ocv;
 p->buffered_current_ma[7]=noise;return fail==4?-EIO:0;
}
static int sc27xx_fgu_get_clbcnt(struct sc27xx_fgu_data *d,int *v){(void)d;*v=(int)counter;return fail==5?-EIO:0;}
static int power_supply_get_property(struct power_supply *p,int prop,union power_supply_propval *v){
 (void)p;if(fail==prop+6)return -EIO;
 switch(prop){case POWER_SUPPLY_PROP_ONLINE:v->intval=online;break;
  case POWER_SUPPLY_PROP_HEALTH:v->intval=health;break;
  default:v->intval=inhibited?POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE:POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO;}
 return 0;
}
static int u30_fcc_apply_full(struct sc27xx_fgu_data *d,int candidate){
 applies++;d->total_cap=candidate;d->fcc_pending=0;d->fcc_sequence++;return 0;
}
static struct sc27xx_fgu_data reset(void){
 now=100;counter=0xfffffff0u;fail=0;temp=250;ocv=3600000;vbat=3600;
 online=health=1;inhibited=noise=applies=0;
 return (struct sc27xx_fgu_data){.cur_1000ma_adc=650,.total_cap=3847,.design_cap=4050,.bat_present=1};
}
'''
main = r'''
static void qualify(struct sc27xx_fgu_data *d,struct power_supply *charger){
 enum u30_quiet_action action;
 assert(!sc27xx_fgu_fcc_poll(d,false,charger,&action));
 assert(action==U30_QUIET_INHIBIT&&!d->fcc_state.active);
 inhibited=1;
 for(int i=0;i<=18;i++){
  now+=10;assert(!sc27xx_fgu_fcc_poll(d,false,charger,&action));
  assert(action==(i==18?U30_QUIET_ACCEPT_RELEASE:U30_QUIET_INHIBIT));
  assert(d->fcc_state.active==(i==18));
 }
 assert(d->fcc_state.start_cap_tenth==30&&d->fcc_state.last_counter==counter);
 inhibited=0;
}
int main(void){
 struct power_supply charger;enum u30_quiet_action action;
 struct sc27xx_fgu_data d=reset();qualify(&d,&charger);
 for(int i=1;i<=1440;i++){
  now+=10;counter+=13000;
  if(i==1440){ocv=4272000;vbat=4272;}
  assert(sc27xx_fgu_fcc_poll(&d,i==1440,&charger,&action)==(i==1440));
  assert(action==U30_QUIET_RELEASE);
 }
 assert(d.total_cap==4124&&d.fcc_sequence==1&&applies==1&&!d.fcc_state.active);
 /* No requested pause/readback means no start, even with quiet-looking ADC. */
 d=reset();
 for(int i=0;i<=60;i++){
  sc27xx_fgu_fcc_poll(&d,false,&charger,&action);now+=10;
  assert(!d.fcc_state.active&&!applies);
 }
 assert(d.quiet_state.reason==U30_QUIET_TIMEOUT);
 /* One noisy real buffer also blocks start until timeout. */
 d=reset();inhibited=1;noise=51;
 for(int i=0;i<=60;i++){
  sc27xx_fgu_fcc_poll(&d,false,&charger,&action);now+=10;
  assert(!d.fcc_state.active&&!applies);
 }
 /* Fault any real FGU or source read: release quiet, no model mutation. */
 for(int f=1;f<=8;f++){
  d=reset();sc27xx_fgu_fcc_poll(&d,false,&charger,&action);
  now+=10;inhibited=1;fail=f;
  assert(!sc27xx_fgu_fcc_poll(&d,false,&charger,&action));
  assert(action==U30_QUIET_RELEASE&&!d.fcc_state.active&&!applies);
 }
 /* Real input loss invalidates active learning rather than reusing its span. */
 d=reset();qualify(&d,&charger);online=0;now+=10;
 assert(!sc27xx_fgu_fcc_poll(&d,false,&charger,&action));
 assert(!d.fcc_state.active&&d.fcc_state.reason==U30_FCC_INPUT_LOST&&!applies);
 /* A staged candidate cannot commit from an unreadable/unhealthy snapshot. */
 for(int f=0;f<2;f++){
  d=reset();d.fcc_pending=3000;ocv=4272000;vbat=4272;
  if(f==0)fail=8;else health=0;
  assert(!sc27xx_fgu_fcc_poll(&d,true,&charger,&action)&&!applies);
 }
 d=reset();d.fcc_restored=1;
 sc27xx_fgu_fcc_poll(&d,false,&charger,&action);
 assert(action==U30_QUIET_RELEASE&&!d.quiet_state.active&&!d.fcc_state.active);
 puts("PASS: actual FGU coordinator requires180s readback/real-buffer evidence; exact counter start; full measured-span learning with wrap; all snapshot/input-loss failures reject model; restored model stays intact");
}
'''
fixture = here/'fgu-quiet-fixture.c'
fixture.write_text(prefix+actual+main)
binary = here/'test-fgu-quiet'
subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror',
                '-I'+str(here),str(fixture),'-o',str(binary)],check=True)
subprocess.run([str(binary)],check=True)

# Supplemental source ordering checks; these do not replace runtime lock tests.
worker = (here/'source/fgu-soc-reconcile.inc').read_text()
call = worker.index('ret = power_supply_set_property(')
assert worker.rfind('mutex_unlock(&data->lock);',0,call) > worker.index('sc27xx_fgu_fcc_poll(')
assert worker.index('power_supply_put(charger);') > call
assert 'cancel_delayed_work_sync(&data->soc_work);' in worker
print('PASS: write callback outside FGU lock; retained charger reference; teardown releases AUTO')
