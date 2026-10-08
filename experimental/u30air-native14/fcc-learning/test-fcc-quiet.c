#include <assert.h>
#include <stdio.h>
#include "source/fcc-quiet-policy.h"

static struct u30_fcc_start_sample low;
static struct u30_quiet_sample setup(struct u30_quiet_state *s)
{
	*s = (struct u30_quiet_state){0};
	low = (struct u30_fcc_start_sample){
		.temp_decic=250,.ocv_uv=3600000,.cap_tenth=30,
		.reads_ok=1,.battery_present=1,.counter_continuous=1,
	};
	for (int i=0;i<8;i++) low.buffered_ocv_uv[i]=3600000;
	return (struct u30_quiet_sample){
		.now_s=100,.reads_ok=1,.powered=1,.battery_present=1,
		.healthy=1,.temp_decic=250,.voltage_uv=3500000,.ocv_uv=3600000,
		.low=&low,
	};
}
int main(void)
{
	struct u30_quiet_state s;
	struct u30_quiet_sample p=setup(&s);
	assert(u30_quiet_tick(&s,&p)==U30_QUIET_INHIBIT);
	/* A request without actual charger readback cannot qualify. */
	for (int i=0;i<20;i++) {
		p.now_s+=10;
		assert(u30_quiet_tick(&s,&p)==U30_QUIET_INHIBIT && !s.stable);
	}
	p.inhibited_readback=1;
	for (int i=0;i<=18;i++) {
		p.now_s+=10;
		assert(u30_quiet_tick(&s,&p)==
			(i==18?U30_QUIET_ACCEPT_RELEASE:U30_QUIET_INHIBIT));
	}
	assert(!s.active && s.reason==U30_QUIET_QUALIFIED);
	p.now_s+=10;
	assert(u30_quiet_tick(&s,&p)==U30_QUIET_RELEASE);
	/* Every actual buffer must remain quiet; deadline cannot be renewed. */
	p=setup(&s);assert(u30_quiet_tick(&s,&p)==U30_QUIET_INHIBIT);
	p.inhibited_readback=1;low.buffered_current_ma[7]=51;
	for(int i=1;i<=60;i++) {
		p.now_s+=10;
		assert(u30_quiet_tick(&s,&p)==
			(i==60?U30_QUIET_RELEASE:U30_QUIET_INHIBIT));
	}
	assert(s.reason==U30_QUIET_TIMEOUT && !s.active);
	/* A drifting OCV cannot masquerade as a relaxed low reference. */
	p=setup(&s);u30_quiet_tick(&s,&p);p.inhibited_readback=1;
	for(int i=0;i<30;i++) {
		p.now_s+=10;low.ocv_uv=3600000+(i%2)*6000;
		assert(u30_quiet_tick(&s,&p)==U30_QUIET_INHIBIT);
	}
	/* Faults release control without overriding ordinary charge safety. */
	for(int fault=0;fault<9;fault++) {
		p=setup(&s);u30_quiet_tick(&s,&p);p.now_s+=10;
		switch(fault) {
		case 0:p.reads_ok=0;break;
		case 1:p.battery_present=0;break;
		case 2:p.healthy=0;break;
		case 3:p.temp_decic=450;break;
		case 4:p.voltage_uv=3400000;break;
		case 5:p.learning_active=1;break;
		case 6:p.learned_sequence=1;break;
		case 7:p.now_s=100;break;
		case 8:p.now_s=116;break;
		}
		assert(u30_quiet_tick(&s,&p)==U30_QUIET_RELEASE && !s.active);
		assert(s.attempted);
	}
	p=setup(&s);u30_quiet_tick(&s,&p);p.powered=0;p.now_s+=10;
	assert(u30_quiet_tick(&s,&p)==U30_QUIET_RELEASE && !s.attempted);
	p.powered=1;p.now_s+=10;
	assert(u30_quiet_tick(&s,&p)==U30_QUIET_INHIBIT);
	puts("PASS: real readback/buffer/stable-OCV qualification; bounded timeout; fault/clock/gap release; attachment attempt latch");
}
