/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef U30_FCC_POLICY_H
#define U30_FCC_POLICY_H

/* Passive capacity learning; this code never controls charger registers.
 * Based on the pinned Android software-OCV start and FCC acceptance limits.
 * Callers must obtain eight real buffered samples and protected full evidence.
 */
struct u30_fcc_start_sample {
	int temp_decic, ocv_uv, cap_tenth;
	int buffered_current_ma[8];
	int buffered_ocv_uv[8];
	int reads_ok, battery_present, counter_continuous;
};

static inline int u30_fcc_start_valid(const struct u30_fcc_start_sample *p)
{
	int i;
	if (!p->reads_ok || !p->battery_present || !p->counter_continuous ||
	    p->temp_decic < 150 || p->temp_decic > 450 ||
	    p->ocv_uv < 3400000 || p->ocv_uv > 3650000 ||
	    p->cap_tenth < 10 || p->cap_tenth > 100)
		return 0;
	for (i = 0; i < 8; i++)
		if (p->buffered_current_ma[i] < -50 ||
		    p->buffered_current_ma[i] > 50 ||
		    p->buffered_ocv_uv[i] < 3400000 ||
		    p->buffered_ocv_uv[i] > 3650000)
			return 0;
	return 1;
}

/* Net coulomb increase spans (1000 - start_cap_tenth)/1000 of total capacity.
 * Infer the total from that measured span, rather than adding residual charge
 * from the previous model. The previous model is only a continuity/sanity gate.
 * A qualified start and uninterrupted counters are required at the caller.
 * Return 0 for unusable evidence. Output is a candidate, not an applied model.
 */
static inline int u30_fcc_candidate_mah(int previous_mah, int design_mah,
		int start_cap_tenth, long long net_charge_uah,
		unsigned int elapsed_s, int protected_full, int counter_continuous)
{
	long long numerator, denominator;
	int candidate;
	if (!protected_full || !counter_continuous || !elapsed_s || elapsed_s > 108000 ||
	    previous_mah < 1000 || previous_mah > 6000 ||
	    design_mah < 1000 || design_mah > 6000 ||
	    start_cap_tenth < 10 || start_cap_tenth > 100 ||
	    net_charge_uah <= 0 || net_charge_uah > (long long)design_mah * 1100)
		return 0;
	/* Stock current ceiling is 3A. Reject a counter discontinuity that
	 * falsely claims more energy than this duration can provide; allow
	 * 10uAh for endpoint conversion/read timing quantization.
	 */
	/* The boottime snapshots use whole seconds; allow one second of
	 * interval quantization as well as the charge conversion margin.
	 */
	if (net_charge_uah * 3600 > ((long long)elapsed_s + 1) * 3000000 + 36000)
		return 0;
	/* Round only once, in mAh, preserving the measured fractional span. */
	numerator = net_charge_uah * 1000;
	denominator = (long long)(1000 - start_cap_tenth) * 1000;
	candidate = (int)((numerator + denominator / 2) / denominator);
	/* Stock strict outer limits; include the valid exact-design case,
	 * omitted by the vendor's separate greater/less-than branches.
	 */
	if ((long long)candidate * 2 <= design_mah ||
	    (long long)candidate * 10 >= (long long)design_mah * 11)
		return 0;
	return candidate;
}
#endif
