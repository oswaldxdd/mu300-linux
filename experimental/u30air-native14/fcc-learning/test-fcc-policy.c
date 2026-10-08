#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include "source/fcc-policy.h"

int main(void)
{
	struct u30_fcc_start_sample p = {
		.temp_decic=250, .ocv_uv=3600000, .cap_tenth=30,
		.reads_ok=1, .battery_present=1, .counter_continuous=1,
	};
	for (int i=0; i<8; i++) p.buffered_ocv_uv[i]=3600000;
	assert(u30_fcc_start_valid(&p));
	for (int i=0; i<8; i++) {
		p.buffered_current_ma[i]=51; assert(!u30_fcc_start_valid(&p));
		p.buffered_current_ma[i]=-51; assert(!u30_fcc_start_valid(&p));
		p.buffered_current_ma[i]=0;
		p.buffered_ocv_uv[i]=3650001; assert(!u30_fcc_start_valid(&p));
		p.buffered_ocv_uv[i]=3600000;
	}
	p.temp_decic=149; assert(!u30_fcc_start_valid(&p));
	p.temp_decic=451; assert(!u30_fcc_start_valid(&p));
	p.temp_decic=250; p.reads_ok=0; assert(!u30_fcc_start_valid(&p));
	p.reads_ok=1; p.counter_continuous=0; assert(!u30_fcc_start_valid(&p));
	p.counter_continuous=1; p.battery_present=0; assert(!u30_fcc_start_valid(&p));
	p.battery_present=1; p.cap_tenth=101; assert(!u30_fcc_start_valid(&p));
	/* Measured 97% span: 3726mAh / 0.97 = 3841.237mAh. */
	assert(u30_fcc_candidate_mah(3847,4050,30,3726000,14400,1,1)==3841);
	assert(u30_fcc_candidate_mah(3847,4050,30,3928500,14400,1,1)==4050);
	/* A genuinely 3000mAh aged cell at 10% gains2700mAh to Full.
	 * Residual inferred from the old3847mAh model would bias this to3085.
	 * Every valid old model must give the same measured result instead.
	 */
	for (int old=1000; old<=6000; old+=37)
		assert(u30_fcc_candidate_mah(old,4050,100,2700000,14400,1,1)==3000);
	for (int soc=10; soc<=100; soc++)
		assert(u30_fcc_candidate_mah(3847,4050,soc,
			3000LL*(1000-soc),14400,1,1)==3000);
	assert(u30_fcc_candidate_mah(3847,4050,100,2700449,14400,1,1)==3000);
	assert(u30_fcc_candidate_mah(3847,4050,100,2700450,14400,1,1)==3001);
	assert(!u30_fcc_candidate_mah(3847,4050,30,-1,14400,1,1));
	assert(!u30_fcc_candidate_mah(3847,4050,30,LLONG_MAX,14400,1,1));
	assert(!u30_fcc_candidate_mah(3847,4050,30,3726000,108001,1,1));
	assert(!u30_fcc_candidate_mah(3847,4050,30,3726000,0,1,1));
	assert(!u30_fcc_candidate_mah(3847,4050,30,3726000,120,1,1));
	assert(!u30_fcc_candidate_mah(3847,4050,30,3726000,14400,0,1));
	assert(!u30_fcc_candidate_mah(3847,4050,30,3726000,14400,1,0));
	assert(!u30_fcc_candidate_mah(3847,4050,30,1000000,14400,1,1));
	assert(!u30_fcc_candidate_mah(3847,4050,30,4340000,14400,1,1));
	puts("PASS: low-OCV qualification and FCC candidate units, faults, timeout, range and overflow guards");
}
