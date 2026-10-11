"""Exercise actual isolated driver setter/expiry code with faulting I2C mocks."""
from pathlib import Path
import subprocess
import tempfile

here = Path(__file__).resolve().parent
source = (here.parent/'source/sgm41511-native.c').read_text()
setter = source[source.index('static int sgm_set_property('):source.index('static int sgm_property_is_writeable(')]
refresh = source[source.index('static bool sgm_quiet_refresh('):source.index('static void sgm_poll(')]
stopper = source[source.index('static void sgm_stop('):source.index('static void sgm_bus_put(')]
prefix = (here.parent/'source/charge-limit-policy.h').read_text() + r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
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
#define WRITE_ONCE(x,v) ((x)=(v))
#define dev_warn(...) ((void)0)
#define dev_err(...) ((void)0)
enum power_supply_property { POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, POWER_SUPPLY_PROP_CHARGE_CONTROL_END_THRESHOLD };
enum {POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO, POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE};
union power_supply_propval {int intval;};
struct power_supply {int unused;};
struct u30_charge_sample {int valid_temp,temp,capacity,valid_capacity;};
struct sgm41511_native {
 int lock,poll;struct power_supply *psy;
 bool stopping,quiet_requested,quiet_attempted,charge_allowed;
 int charge_end;bool limit_held;
 uint64_t quiet_deadline;int charge_temp;
 void *client,*role_sw;
};
static struct sgm41511_native dev;
static unsigned int regs[16];
static uint64_t now;
static int reads,fail_read,fail_write,stuck,locked,notifications,schedules;
static int cancelled,sample_calls,restore_calls,boost_off_calls,role_put_calls;
static struct u30_charge_sample fresh_sample;
static void *system_wq;
static struct u30_charge_sample sgm_android_sample(void);
static void *power_supply_get_drvdata(struct power_supply *p) {(void)p;return &dev;}
static void mutex_lock(int *m) {(void)m;assert(!locked);locked=1;}
static void mutex_unlock(int *m) {(void)m;assert(locked);locked=0;}
static uint64_t ktime_get_boottime_seconds(void) {return now;}
static int sgm_read(struct sgm41511_native *d,int r,unsigned int *v) {
 (void)d;assert(locked);if(++reads==fail_read)return -EIO;*v=regs[r];return 0;
}
static int sgm_update(struct sgm41511_native *d,int r,int mask,int val) {
 (void)d;assert(locked);assert(r==SGM_CONTROL&&(mask==SGM_CHARGE||mask==SGM_BOOST));
 if(fail_write)return -EIO;
 if(!stuck)regs[r]=(regs[r]&~mask)|val;
 return 0;
}
static int sgm41511_external_input(unsigned int s,unsigned int v) {return s==1&&v==1;}
static void cancel_delayed_work_sync(int *work) {
 (void)work;assert(!locked&&dev.stopping);cancelled++;
}
static struct u30_charge_sample sgm_android_sample(void) {
 assert(!locked);if(dev.stopping)assert(cancelled==1);sample_calls++;return fresh_sample;
}
static int sgm_android_apply(struct sgm41511_native *d,struct u30_charge_sample s,
 unsigned int powered,unsigned int fault,bool *allowed,bool *changed) {
 assert(locked);(void)changed;
 *allowed=powered&&!fault&&s.valid_temp&&s.temp>=150&&s.temp<450;
 d->charge_allowed=*allowed;
 if(!*allowed)return sgm_update(d,SGM_CONTROL,SGM_CHARGE,0);
 return 0;
}
static int sgm_keep_charging(struct sgm41511_native *d,bool powered,
 unsigned int status,unsigned int fault,unsigned int control,bool *changed) {
 unsigned int readback;(void)changed;assert(locked);
 if(!powered||fault||(control&SGM_BOOST)||(control&SGM_CHARGE)||((status>>3)&3)==3)return 0;
 restore_calls++;
 if(sgm_update(d,SGM_CONTROL,SGM_CHARGE,SGM_CHARGE))return -EIO;
 if(sgm_read(d,SGM_CONTROL,&readback))return -EIO;
 return (readback&SGM_CHARGE)?0:-EIO;
}
static int sgm_boost_off(struct sgm41511_native *d) {
 unsigned int control;assert(locked);boost_off_calls++;
 if(sgm_update(d,SGM_CONTROL,SGM_BOOST,0))return -EIO;
 if(sgm_read(d,SGM_CONTROL,&control))return -EIO;
 return (control&SGM_BOOST)?-EIO:0;
}
static void usb_role_switch_put(void *sw) {assert(!locked);assert(sw);role_put_calls++;}
static void mod_delayed_work(void *q,int *work,int delay) {
 (void)q;(void)work;(void)delay;assert(locked);schedules++;
}
static void power_supply_changed(struct power_supply *p) {(void)p;assert(!locked);notifications++;}
static void reset(void) {
 dev=(struct sgm41511_native){.charge_allowed=1,.charge_temp=250,.charge_end=100};
 for(int i=0;i<16;i++)regs[i]=0;
 regs[SGM_STATUS]=regs[SGM_VBUS]=1;regs[SGM_CONTROL]=0x1a;
 now=100;reads=fail_read=fail_write=stuck=locked=notifications=schedules=0;
 cancelled=sample_calls=restore_calls=boost_off_calls=role_put_calls=0;
 fresh_sample=(struct u30_charge_sample){.valid_temp=1,.temp=250};
}
'''
main = r'''
int main(void) {
 union power_supply_propval limit={.intval=80};
 reset();fresh_sample.capacity=87;fresh_sample.valid_capacity=1;
 assert(!sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_CONTROL_END_THRESHOLD,&limit));
 assert(dev.charge_end==80&&dev.limit_held&&regs[SGM_CONTROL]==0x0a);
 assert(!dev.quiet_requested&&!dev.quiet_attempted);
 assert(u30_limit_hold(80,79,1,1)&&u30_limit_hold(80,76,1,1));
 assert(!u30_limit_hold(80,75,1,1)&&!u30_limit_hold(80,79,1,0));
 assert(u30_limit_hold(80,0,0,0)&&!u30_limit_hold(100,0,0,1));
 assert(u30_limit_hold(0,0,1,0)&&u30_limit_hold(0,100,1,0));
 assert(u30_limit_hold(1,1,1,0)&&!u30_limit_hold(1,0,1,1));
 reset();fresh_sample.capacity=87;fresh_sample.valid_capacity=1;fail_write=1;
 assert(sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_CONTROL_END_THRESHOLD,&limit)==-EIO);
 assert(dev.charge_end==100&&!dev.limit_held);
 reset();fresh_sample.capacity=87;fresh_sample.valid_capacity=1;stuck=1;
 assert(sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_CONTROL_END_THRESHOLD,&limit)==-EIO);
 assert(dev.charge_end==100&&!dev.limit_held);
 reset();limit.intval=-1;
 assert(sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_CONTROL_END_THRESHOLD,&limit)==-EINVAL);
 limit.intval=101;
 assert(sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_CONTROL_END_THRESHOLD,&limit)==-EINVAL);
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
 /* Stop cancels the only lease worker, samples FGU unlocked, then restores via
  * common source/fault/JEITA checks. Unknown state leaves CHG_CONFIG off. */
 reset();assert(!sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&inhibit));
 dev.role_sw=(void *)1;sgm_stop(&dev);
 assert(dev.stopping&&!dev.quiet_requested&&!dev.quiet_attempted&&cancelled==1);
 assert(sample_calls==1&&restore_calls==1&&boost_off_calls>=2&&role_put_calls==1);
 assert((regs[SGM_CONTROL]&SGM_CHARGE)&&!(regs[SGM_CONTROL]&SGM_BOOST));
 for(int f=0;f<6;f++) {
  reset();assert(!sgm_set_property(NULL,POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,&inhibit));
  if(f==0)regs[SGM_STATUS]=0;
  if(f==1)regs[SGM_FAULT]=1;
  if(f==2)fresh_sample.valid_temp=0;
  if(f==3)fresh_sample.temp=149;
  if(f==4)fresh_sample.temp=450;
  if(f==5){reads=0;fail_read=1;}
  sgm_stop(&dev);
  assert(dev.stopping&&!dev.quiet_requested&&!dev.quiet_attempted);
  assert(!(regs[SGM_CONTROL]&SGM_CHARGE));
  assert(!(regs[SGM_CONTROL]&SGM_BOOST));
  assert(sample_calls==1&&cancelled==1);
 }
 puts("PASS: actual driver lease never renewed; independent expiry; detach latch; safe quiet stop/unbind restore; source/fault/temperature/read-failure shutdown; boost stays off");
}
'''
with tempfile.TemporaryDirectory(prefix='mu300-charger-quiet-') as tmp:
    fixture = Path(tmp)/'charger-quiet-fixture.c'
    binary = Path(tmp)/'test-charger-quiet'
    fixture.write_text(prefix + setter + refresh + stopper + main)
    build = subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror',
                            str(fixture),'-o',str(binary)], text=True,
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if build.returncode:
        print(build.stdout)
        raise SystemExit(build.returncode)
    subprocess.run([str(binary)], check=True)
