
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
static bool sc27xx_fgu_fcc_poll(struct sc27xx_fgu_data *data, bool protected_full,
		struct power_supply *charger, enum u30_quiet_action *action)
{
	struct u30_fcc_start_sample low = {0};
	struct u30_fcc_tick_sample sample = {
		.now_s = (u32)ktime_get_boottime_seconds(),
		.gain = data->cur_1000ma_adc,
		.previous_mah = data->total_cap, .design_mah = data->design_cap,
		.battery_present = data->bat_present, .protected_full = protected_full,
	};
	struct u30_quiet_sample quiet = {
		.now_s = sample.now_s, .battery_present = data->bat_present,
		.learning_active = data->fcc_state.active,
		.learned_sequence = data->fcc_sequence || data->fcc_restored,
	};
	union power_supply_propval val;
	int counter, ocv = 0, temp = 0, vbat = 0, ret, supply_ret = -ENODEV;
	bool was_active = data->fcc_state.active;
	bool was_quiet = data->quiet_state.active;
	ret = sc27xx_fgu_get_temp(data, &temp);
	if (!ret)
		ret = sc27xx_fgu_get_vbat_ocv(data, &ocv);
	if (!ret)
		ret = sc27xx_fgu_get_vbat_vol(data, &vbat);
	if (!ret && !data->fcc_state.active && !protected_full && ocv <= 3650000) {
		ret = u30_fcc_read_low(data, temp, &low);
		if (!ret)
			quiet.low = &low;
	}
	if (!ret)
		ret = sc27xx_fgu_get_clbcnt(data, &counter);
	if (!ret) {
		sample.counter = (u32)counter;
		sample.reads_ok = 1;
	}
	quiet.temp_decic = temp;
	quiet.voltage_uv = vbat * 1000;
	quiet.ocv_uv = ocv;
	if (charger) {
		supply_ret = power_supply_get_property(charger, POWER_SUPPLY_PROP_ONLINE, &val);
		if (!supply_ret) quiet.powered = val.intval != 0;
		if (!supply_ret)
			supply_ret = power_supply_get_property(charger, POWER_SUPPLY_PROP_HEALTH, &val);
		if (!supply_ret) quiet.healthy = val.intval == POWER_SUPPLY_HEALTH_GOOD;
		if (!supply_ret)
			supply_ret = power_supply_get_property(charger, POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, &val);
		if (!supply_ret)
			quiet.inhibited_readback = val.intval == POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE;
	}
	quiet.reads_ok = sample.reads_ok && !supply_ret;
	*action = u30_quiet_tick(&data->quiet_state, &quiet);
	/* The same real low reference and hardware counter qualify the learning
	 * start; requesting inhibition or just displaying low SOC never does.
	 */
	if (*action == U30_QUIET_ACCEPT_RELEASE) sample.low = &low;
	if (was_active && (supply_ret || !quiet.powered))
		ret = u30_fcc_abort(&data->fcc_state,
			supply_ret ? U30_FCC_READ_ERROR : U30_FCC_INPUT_LOST);
	else
		ret = u30_fcc_tick(&data->fcc_state, &sample);
	if (!was_quiet && data->quiet_state.active)
		dev_info(data->dev, "FCC quiet window requested; timeout=%us\n", U30_QUIET_MAX_S);
	if (!was_active && data->fcc_state.active) {
		data->fcc_pending = 0;
		dev_info(data->dev, "FCC learning start, OCV=%duV cap=%d/1000\n",
			 low.ocv_uv, low.cap_tenth);
	}
	if (ret < 0) {
		data->fcc_pending = 0;
		dev_warn_ratelimited(data->dev, "FCC learning invalidated: %d\n", -ret);
	} else if (ret > 0) {
		data->fcc_pending = ret;
	}
	if (quiet.reads_ok && quiet.powered && quiet.healthy &&
	    sample.battery_present && protected_full && data->fcc_pending) {
		ret = u30_fcc_apply_full(data, data->fcc_pending);
		if (!ret)
			return true;
		dev_warn_ratelimited(data->dev, "FCC apply failed: %d\n", ret);
	}
	return false;
}


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
