"""Execute the actual reconciliation worker against sensor and supply failures."""
from pathlib import Path
import tempfile
import subprocess as sp
root = Path(__file__).resolve().parent
code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include "fgu-soc-policy.h"
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; };
struct power_supply { int unused; };
union power_supply_propval { int intval; };
enum { POWER_SUPPLY_PROP_STATUS, POWER_SUPPLY_PROP_ONLINE, POWER_SUPPLY_PROP_HEALTH,
       POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX };
enum { POWER_SUPPLY_STATUS_CHARGING=1, POWER_SUPPLY_STATUS_FULL=4, POWER_SUPPLY_HEALTH_GOOD=1 };
struct sc27xx_fgu_data {
 struct delayed_work soc_work; struct u30_soc_state soc_state;
 int lock, max_volt, bat_present, init_cap, init_clbcnt, soc_full_uv, soc_full_ua;
 int *cap_table, table_len; void *dev; struct power_supply *battery;
 bool soc_stopping, soc_full_verified;
};
static struct power_supply supply;
static int props[4], temp, cur, ocv, vbat, err, missing, stored, events, puts, saves;
#define container_of(ptr,type,member) ((type *)((char *)(ptr)-offsetof(type,member)))
#define to_delayed_work(ptr) container_of(ptr,struct delayed_work,work)
#define mutex_lock(x) ((void)(x))
#define mutex_unlock(x) ((void)(x))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define msecs_to_jiffies(x) (x)
#define dev_info(...) ((void)0)
#define dev_err_ratelimited(...) ((void)0)
static void schedule_delayed_work(struct delayed_work *w,int ms) { (void)w; assert(ms==10000); }
static void cancel_delayed_work_sync(struct delayed_work *w) { (void)w; }
static struct power_supply *power_supply_get_by_name(const char *n) { (void)n; return missing?NULL:&supply; }
static int power_supply_get_property(struct power_supply *p,int key,union power_supply_propval *v) {
 (void)p; v->intval=props[key]; return err==key+1?-5:0;
}
static void power_supply_put(struct power_supply *p) { assert(p==&supply); puts++; }
static void power_supply_changed(struct power_supply *p) { (void)p; events++; }
static int power_supply_ocv2cap_simple(int *t,int n,int u) { (void)t;(void)n;(void)u;return 93; }
static int sc27xx_fgu_get_temp(struct sc27xx_fgu_data *d,int *v) { (void)d;*v=temp;return err==5?-5:0; }
static int sc27xx_fgu_get_cur_now(struct sc27xx_fgu_data *d,int *v) { (void)d;*v=cur;return err==6?-5:0; }
static int sc27xx_fgu_get_vbat_ocv(struct sc27xx_fgu_data *d,int *v) { (void)d;*v=ocv;return err==7?-5:0; }
static int sc27xx_fgu_get_vbat_vol(struct sc27xx_fgu_data *d,int *v) { (void)d;*v=vbat;return err==8?-5:0; }
static int sc27xx_fgu_get_capacity(struct sc27xx_fgu_data *d,int *v) { *v=d->init_cap;return err==9?-5:0; }
static int sc27xx_fgu_get_clbcnt(struct sc27xx_fgu_data *d,int *v) { (void)d;*v=12345;return err==10?-5:0; }
static int sc27xx_fgu_save_last_cap(struct sc27xx_fgu_data *d,int v) { (void)d;if(err==11)return -5;stored=v;saves++;return 0; }
#include "fgu-soc-reconcile.inc"
static struct sc27xx_fgu_data reset(void) {
 props[0]=4;props[1]=1;props[2]=1;props[3]=4272000;
 temp=370;cur=0;ocv=4179000;vbat=4179;err=missing=stored=events=puts=saves=0;
 return (struct sc27xx_fgu_data){.max_volt=4300,.bat_present=1,.init_cap=76,
 .init_clbcnt=99,.soc_full_uv=4200000,.soc_full_ua=120000,.battery=&supply};
}
static void run(struct sc27xx_fgu_data *d,int n) { while(n--)sc27xx_fgu_soc_work(&d->soc_work.work); }
int main(void) {
 struct sc27xx_fgu_data d=reset();run(&d,100);assert(d.init_cap==93&&stored==93&&events==17&&saves==17&&puts==100);
 for(int e=1;e<=11;e++){d=reset();err=e;run(&d,100);assert(d.init_cap==76&&d.init_clbcnt==99&&events==0);}
 for(int c=0;c<10;c++){
  d=reset();
  switch(c){case 0:missing=1;break;case 1:props[0]=1;break;case 2:props[1]=0;break;
   case 3:props[2]=0;break;case 4:props[3]=4100000;break;case 5:d.bat_present=0;break;
   case 6:temp=401;break;case 7:temp=199;break;case 8:cur=-21;break;case 9:cur=21;break;}
  run(&d,100);assert(d.init_cap==76&&events==0&&d.soc_state.stable==0);
 }
 d=reset();vbat=4200;ocv=4200000;run(&d,120);assert(d.init_cap==100&&d.soc_full_verified);
 d=reset();d.init_cap=100;run(&d,100);assert(!d.soc_full_verified);
 d=reset();d.init_cap=100;vbat=4200;ocv=4200000;run(&d,12);assert(!d.soc_full_verified);
 run(&d,1);assert(d.soc_full_verified);
 props[0]=1;run(&d,1);assert(!d.soc_full_verified);
 d=reset();d.soc_full_verified=true;props[0]=2;run(&d,1);assert(d.soc_full_verified);
 sc27xx_fgu_soc_stop(&d);assert(d.soc_stopping);
}
'''
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp)/'worker.c'; p.write_text(code); exe=Path(tmp)/'worker'
    sp.run(['gcc','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I'+str(root/'source'),str(p),'-o',str(exe)],check=True)
    sp.run([str(exe)],check=True)
print('PASS: actual worker supply/sensor/read-write guards, persistent OCV/full targets and lifecycle stop')
