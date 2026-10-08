/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef U30_FCC_QUIET_POLICY_H
#define U30_FCC_QUIET_POLICY_H
#include "fcc-policy.h"

/* Decision component only. A kernel adapter must enforce the same absolute
 * deadline in the charger itself, even if the FGU worker stops polling.
 * RELEASE means return to ordinary Android/JEITA policy, not force charge on.
 * This code never changes SOC, capacity, hardware counters, SYS or BATFET.
 */
#define U30_QUIET_MAX_S 600u
#define U30_QUIET_STABLE_S 180u
#define U30_QUIET_MAX_GAP_S 15u
#define U30_QUIET_OCV_BAND_UV 5000
enum u30_quiet_action {
	U30_QUIET_RELEASE, U30_QUIET_INHIBIT, U30_QUIET_ACCEPT_RELEASE,
};
enum u30_quiet_reason {
	U30_QUIET_NONE, U30_QUIET_QUALIFIED, U30_QUIET_TIMEOUT,
	U30_QUIET_FAULT, U30_QUIET_CLOCK, U30_QUIET_GAP,
};
struct u30_quiet_state {
	int active, attempted, stable, reason, ocv_min, ocv_max;
	unsigned int start_s, last_s, stable_s;
};
struct u30_quiet_sample {
	unsigned int now_s;
	int reads_ok, powered, battery_present, healthy;
	int temp_decic, voltage_uv, ocv_uv;
	int inhibited_readback, learning_active, learned_sequence;
	const struct u30_fcc_start_sample *low;
};
static inline enum u30_quiet_action
u30_quiet_release(struct u30_quiet_state *s, int reason)
{
	s->active = s->stable = 0;
	s->reason = reason;
	return U30_QUIET_RELEASE;
}

static inline enum u30_quiet_action
u30_quiet_tick(struct u30_quiet_state *s, const struct u30_quiet_sample *p)
{
	/* Reset the one-attempt latch only on actual readable input absence. */
	if (p->reads_ok && !p->powered) {
		*s = (struct u30_quiet_state){0};
		return U30_QUIET_RELEASE;
	}
	if (!p->reads_ok || !p->powered || !p->battery_present || !p->healthy ||
	    p->temp_decic < 150 || p->temp_decic >= 450 ||
	    p->voltage_uv <= 3400000)
		return u30_quiet_release(s, U30_QUIET_FAULT);
	if (p->learning_active || p->learned_sequence)
		return u30_quiet_release(s, s->reason == U30_QUIET_QUALIFIED ?
			U30_QUIET_QUALIFIED : U30_QUIET_NONE);
	if (!s->active) {
		if (s->attempted || p->ocv_uv < 3400000 || p->ocv_uv > 3650000)
			return U30_QUIET_RELEASE;
		*s = (struct u30_quiet_state){
			.active = 1, .attempted = 1,
			.start_s = p->now_s, .last_s = p->now_s,
		};
		return U30_QUIET_INHIBIT;
	}
	if (p->now_s <= s->last_s || p->now_s < s->start_s)
		return u30_quiet_release(s, U30_QUIET_CLOCK);
	if (p->now_s - s->last_s > U30_QUIET_MAX_GAP_S)
		return u30_quiet_release(s, U30_QUIET_GAP);
	s->last_s = p->now_s;
	if (p->now_s - s->start_s >= U30_QUIET_MAX_S)
		return u30_quiet_release(s, U30_QUIET_TIMEOUT);
	/* Inhibit readback is required: requesting it is not evidence. Every
 * low snapshot must retain the original real8-buffer qualification.
 */
	if (!p->inhibited_readback || !p->low || !u30_fcc_start_valid(p->low) ||
	    p->low->temp_decic != p->temp_decic) {
		s->stable = 0;
		return U30_QUIET_INHIBIT;
	}
	if (!s->stable) {
		s->stable = 1;
		s->stable_s = p->now_s;
		s->ocv_min = s->ocv_max = p->low->ocv_uv;
	}
	if (p->low->ocv_uv < s->ocv_min) s->ocv_min = p->low->ocv_uv;
	if (p->low->ocv_uv > s->ocv_max) s->ocv_max = p->low->ocv_uv;
	if (s->ocv_max - s->ocv_min > U30_QUIET_OCV_BAND_UV) {
		s->stable_s = p->now_s;
		s->ocv_min = s->ocv_max = p->low->ocv_uv;
	}
	if (p->now_s - s->stable_s >= U30_QUIET_STABLE_S) {
		u30_quiet_release(s, U30_QUIET_QUALIFIED);
		return U30_QUIET_ACCEPT_RELEASE;
	}
	return U30_QUIET_INHIBIT;
}
#endif
