#include <assert.h>
#include <stdio.h>
#include "source/fcc-state.h"

static struct u30_fcc_start_sample low(void)
{
	struct u30_fcc_start_sample p={.temp_decic=250,.ocv_uv=3600000,
		.cap_tenth=30,.reads_ok=1,.battery_present=1,.counter_continuous=1};
	for (int i=0;i<8;i++) p.buffered_ocv_uv[i]=3600000;
	return p;
}
static void start(struct u30_fcc_state *s, struct u30_fcc_tick_sample *p,
		  const struct u30_fcc_start_sample *reference)
{
	*s=(struct u30_fcc_state){0};
	*p=(struct u30_fcc_tick_sample){.now_s=100,.counter=0xfffffff0u,
		.gain=650,.previous_mah=3847,.design_mah=4050,
		.reads_ok=1,.battery_present=1,.low=reference};
	assert(u30_fcc_tick(s,p)==0 && s->active);
	p->low=NULL;
}
int main(void)
{
	struct u30_fcc_state s;
	struct u30_fcc_tick_sample p;
	struct u30_fcc_start_sample r=low();
	/* 1A net, 4 hours: measured4000mAh / 0.97 -> 4124mAh.
	 * 650 ADC/mA gain means 13000 counts in each 10-second interval.
	 * Counter wraps on the first interval without losing charge.
	 */
	start(&s,&p,&r);
	for (int i=1;i<=1440;i++) {
		p.now_s+=10; p.counter+=13000;
		p.protected_full=(i==1440);
		int result=u30_fcc_tick(&s,&p);
		assert(result==(i==1440?4124:0));
	}
	assert(!s.active && s.candidate_mah==4124);
	/* A brief real discharge before charging is a signed net decrease. */
	start(&s,&p,&r);p.now_s+=10;p.counter-=6500;
	assert(!u30_fcc_tick(&s,&p)&&s.net_counts==-6500);
	/* A 3A interval can occupy up to one extra fractional second when
	 * boottime is rounded down. This is timing tolerance, not a 3.3A policy.
	 */
	start(&s,&p,&r);p.now_s+=10;p.counter+=42900;
	assert(!u30_fcc_tick(&s,&p)&&s.active);
	start(&s,&p,&r);p.now_s+=10;p.counter+=44200;
	assert(u30_fcc_tick(&s,&p)==-U30_FCC_COUNTER_JUMP);
	/* Every continuity failure must prevent model output. */
	for (int fault=1;fault<=8;fault++) {
		start(&s,&p,&r);p.now_s+=10;p.counter+=13000;
		switch(fault) {
		case 1:p.reads_ok=0;break;
		case 2:p.battery_present=0;break;
		case 3:p.now_s=s.last_s;break;
		case 4:p.now_s+=121;break;
		case 5:p.gain++;break;
		case 6:p.counter=500000000;break;
		case 7:p.now_s=s.start_s+108001;break;
		case 8:p.previous_mah++;break;
		}
		assert(u30_fcc_tick(&s,&p)<0&&!s.active&&!s.candidate_mah);
	}
	start(&s,&p,&r);p.now_s+=10;p.counter+=13000;p.protected_full=1;
	assert(u30_fcc_tick(&s,&p)==-U30_FCC_RANGE_REJECTED);
	/* Wrong/absent low endpoint cannot start a seemingly good full cycle. */
	r.buffered_current_ma[7]=51;
	s=(struct u30_fcc_state){0};p.low=&r;p.protected_full=0;
	assert(!u30_fcc_tick(&s,&p)&&!s.active);
	puts("PASS: full cycle with counter wrap; signed net discharge; all continuity faults and invalid endpoints rejected");
}
