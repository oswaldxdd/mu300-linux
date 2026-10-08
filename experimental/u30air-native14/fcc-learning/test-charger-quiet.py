"""Exercise actual isolated driver setter/expiry code with faulting I2C mocks."""
from pathlib import Path
import subprocess

here = Path(__file__).resolve().parent
source = (here/'source/sgm41511-native.c').read_text()
setter = source[source.index('static int sgm_set_property('):source.index('static int sgm_property_is_writeable(')]
refresh = source[source.index('static bool sgm_quiet_refresh('):source.index('static void sgm_poll(')]
prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define EINVAL 22
#define EIO 5
#define ENODEV 19
#define EAGAIN 11
#define EBUSY 16
#define SGM_STATUS 8
#define SGM_VBUS 10
#define SGM_FAULT 9
#define SGM_CONTROL 1
#define SGM_CHARGE 16
#define SGM_BOOST 32
#define U30_QUIET_MAX_S 600u
enum power_supply_property { POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR };
enum {POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO, POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE};
union power_supply_propval {int intval;};
struct power_supply {int unused;};
struct sgm41511_native {
 int lock,poll;struct power_supply *psy;
 bool stopping,quiet_requested,quiet_attempted,charge_allowed;
 uint64_t quiet_deadline;int charge_temp;
};
static struct sgm41511_native dev;
static unsigned int regs[16];
static uint64_t now;
static int reads,fail_read,fail_write,stuck,locked,notifications,schedules;
static void *system_wq;
static void *power_supply_get_drvdata(struct power_supply *p) {(void)p;return &dev;}
static void mutex_lock(int *m) {(void)m;assert(!locked);locked=1;}
static void mutex_unlock(int *m) {(void)m;assert(locked);locked=0;}
static uint64_t ktime_get_boottime_seconds(void) {return now;}
static int sgm_read(struct sgm41511_native *d,int r,unsigned int *v) {
 (void)d;assert(locked);if(++reads==fail_read)return -EIO;*v=regs[r];return 0;
}
static int sgm_update(struct sgm41511_native *d,int r,int mask,int val) {
 (void)d;assert(locked);assert(r==SGM_CONTROL&&mask==SGM_CHARGE);
 if(fail_write)return -EIO;
 if(!stuck)regs[r]=(regs[r]&~mask)|val;
 return 0;
}
static int sgm41511_external_input(unsigned int s,unsigned int v) {return s==1&&v==1;}
static void mod_delayed_work(void *q,int *work,int delay) {
 (void)q;(void)work;(void)delay;assert(locked);schedules++;
}
static void power_supply_changed(struct power_supply *p) {(void)p;assert(!locked);notifications++;}
static void reset(void) {
 dev=(struct sgm41511_native){.charge_allowed=1,.charge_temp=250};
 for(int i=0;i<16;i++)regs[i]=0;
 regs[SGM_STATUS]=regs[SGM_VBUS]=1;regs[SGM_CONTROL]=0x1a;
 now=100;reads=fail_read=fail_write=stuck=locked=notifications=schedules=0;
}
'''
main = r'''
int main(void) {
 union power_supply_propval inhibit={.intval=POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE};
 union power_supply_propval automatic={.intval=POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO};
 reset();assert(!sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&inhibit));
 assert(dev.quiet_requested&&dev.quiet_attempted&&dev.quiet_deadline==700);
 assert(regs[SGM_CONTROL]==0x0a&&notifications==1&&schedules==1);
 for(now=110;now<700;now+=10) {
  assert(!sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&inhibit));
  assert(dev.quiet_deadline==700);
 }
 /* Expiry is charger-owned, with no FGU function called. */
 assert(sgm_quiet_refresh(&dev,true,true,0,true,250));
 assert(!dev.quiet_requested&&dev.quiet_attempted);
 assert(sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&inhibit)==-EBUSY);
 /* Read failure is not an actual detach and cannot reset the latch. */
 sgm_quiet_refresh(&dev,false,false,0,true,250);assert(dev.quiet_attempted);
 sgm_quiet_refresh(&dev,true,false,0,true,250);assert(!dev.quiet_attempted);
 assert(!sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&inhibit));
 assert(!sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&automatic));
 assert(!dev.quiet_requested&&dev.quiet_attempted&&regs[SGM_CONTROL]==0x0a);
 /* AUTO releases policy ownership but never forcibly enables charging. */
 for(int fault=0;fault<7;fault++) {
  reset();assert(!sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&inhibit));
  bool readable=true,powered=true,valid=true;unsigned int f=0;int t=250;
  switch(fault) {case 0:readable=false;break;case 1:powered=false;break;
   case 2:f=1;break;case 3:valid=false;break;case 4:t=149;break;
   case 5:t=450;break;case 6:now=700;break;}
  assert(sgm_quiet_refresh(&dev,readable,powered,f,valid,t)&&!dev.quiet_requested);
 }
 for(int i=1;i<=6;i++) {
  reset();fail_read=i;
  assert(sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&inhibit)==-EIO);
  assert(!dev.quiet_requested&&!dev.quiet_attempted);
 }
 reset();fail_write=1;assert(sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&inhibit)==-EIO);
 reset();stuck=1;assert(sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&inhibit)==-EIO);
 assert(!dev.quiet_requested&&!dev.quiet_attempted);
 for(int f=0;f<6;f++) {
  reset();switch(f){case 0:regs[SGM_STATUS]=0;break;case 1:regs[SGM_FAULT]=1;break;
   case 2:regs[SGM_CONTROL]|=SGM_BOOST;break;case 3:dev.charge_allowed=false;break;
   case 4:dev.charge_temp=149;break;case 5:dev.charge_temp=450;break;}
  assert(sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&inhibit)==-EAGAIN);
  assert(!dev.quiet_requested&&!dev.quiet_attempted);
 }
 reset();dev.stopping=true;assert(sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&automatic)==-ENODEV);
 assert(!schedules);
 puts("PASS: actual driver lease never renewed; independent expiry; readable detach latch; fault/read/write/readback/stop guards; SYS/OTG preserved; AUTO respects ordinary policy");
}
'''
fixture = here/'charger-quiet-fixture.c'
fixture.write_text(prefix + setter + refresh + main)
binary = here/'test-charger-quiet'
build = subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror',
                        str(fixture),'-o',str(binary)], text=True,
                       stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
if build.returncode:
    print(build.stdout)
    raise SystemExit(build.returncode)
subprocess.run([str(binary)], check=True)
