/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef U30_FCC_STATE_H
#define U30_FCC_STATE_H
#include "fcc-policy.h"

enum u30_fcc_reason {
	U30_FCC_NONE, U30_FCC_READ_ERROR, U30_FCC_BATTERY_REMOVED,
	U30_FCC_CLOCK_ERROR, U30_FCC_SAMPLE_GAP, U30_FCC_GAIN_CHANGED,
	U30_FCC_COUNTER_JUMP, U30_FCC_TIMEOUT, U30_FCC_MODEL_CHANGED,
	U30_FCC_RANGE_REJECTED,
	U30_FCC_INPUT_LOST,
};
struct u30_fcc_state {
	int active, candidate_mah, reason, start_cap_tenth;
	int gain, previous_mah, design_mah;
	unsigned int start_s, last_s, last_counter;
	long long net_counts;
};
struct u30_fcc_tick_sample {
	unsigned int now_s, counter;
	int gain, previous_mah, design_mah, reads_ok, battery_present;
	int protected_full;
	const struct u30_fcc_start_sample *low;
};

static inline long long u30_fcc_counts_uah(long long counts, int gain)
{
	long long numerator = counts * 1250;
	long long denominator = (long long)gain * 9;
	return numerator >= 0 ? (numerator + denominator / 2) / denominator :
		-((-numerator + denominator / 2) / denominator);
}
static inline int u30_fcc_abort(struct u30_fcc_state *s, int reason)
{
	s->active = 0;
	s->candidate_mah = 0;
	s->reason = reason;
	return -reason;
}

/* Call once per coherent, read-only hardware snapshot. Learned output is
 * only a candidate; the kernel must apply model and SOC reference together.
 * Missing/invalid snapshots terminate a pending cycle, never invent energy.
 */
static inline int u30_fcc_tick(struct u30_fcc_state *s,
			      const struct u30_fcc_tick_sample *p)
{
	unsigned int dt;
	int delta;
	long long amount_uah;
	if (!p->reads_ok)
		return s->active ? u30_fcc_abort(s, U30_FCC_READ_ERROR) : 0;
	if (!p->battery_present)
		return u30_fcc_abort(s, U30_FCC_BATTERY_REMOVED);
	if (!s->active) {
		if (!p->low || !u30_fcc_start_valid(p->low) ||
		    p->protected_full || p->gain <= 0 || p->gain > 4096 ||
		    p->previous_mah < 1000 || p->previous_mah > 6000 ||
		    p->design_mah < 1000 || p->design_mah > 6000)
			return 0;
		*s = (struct u30_fcc_state) {
			.active=1, .start_cap_tenth=p->low->cap_tenth,
			.gain=p->gain, .previous_mah=p->previous_mah,
			.design_mah=p->design_mah, .start_s=p->now_s,
			.last_s=p->now_s, .last_counter=p->counter,
		};
		return 0;
	}
	if (p->now_s <= s->last_s || p->now_s < s->start_s)
		return u30_fcc_abort(s, U30_FCC_CLOCK_ERROR);
	if (p->now_s - s->start_s > 108000)
		return u30_fcc_abort(s, U30_FCC_TIMEOUT);
	dt = p->now_s - s->last_s;
	if (dt > 120)
		return u30_fcc_abort(s, U30_FCC_SAMPLE_GAP);
	if (p->gain != s->gain)
		return u30_fcc_abort(s, U30_FCC_GAIN_CHANGED);
	if (p->previous_mah != s->previous_mah || p->design_mah != s->design_mah)
		return u30_fcc_abort(s, U30_FCC_MODEL_CHANGED);
	/* Incremental signed 32-bit deltas survive counter wrap. Accumulate in
	 * 64 bits rather than losing a long cycle to a signed whole-cycle wrap.
	 */
	delta = (int)(p->counter - s->last_counter);
	amount_uah = u30_fcc_counts_uah(delta, s->gain);
	/* Positive current is limited by the stock 3A charger ceiling. The
	 * 10A discharge envelope is a discontinuity guard, not a discharge
	 * policy; the actual current and all charging limits remain untouched.
	 */
	if (amount_uah * 3600 > ((long long)dt + 1) * 3000000 + 36000 ||
	    -amount_uah * 3600 > ((long long)dt + 1) * 10000000 + 36000)
		return u30_fcc_abort(s, U30_FCC_COUNTER_JUMP);
	s->net_counts += delta;
	s->last_counter = p->counter;
	s->last_s = p->now_s;
	if (!p->protected_full)
		return 0;
	s->candidate_mah = u30_fcc_candidate_mah(s->previous_mah, s->design_mah,
		s->start_cap_tenth, u30_fcc_counts_uah(s->net_counts, s->gain),
		p->now_s - s->start_s, 1, 1);
	s->active = 0;
	if (!s->candidate_mah)
		return u30_fcc_abort(s, U30_FCC_RANGE_REJECTED);
	s->reason = U30_FCC_NONE;
	return s->candidate_mah;
}
#endif
