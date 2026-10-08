/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef U30_FGU_SOC_POLICY_H
#define U30_FGU_SOC_POLICY_H
/* One tick is ten seconds. Rebase only a quiet, protected charge-done sample. */
struct u30_soc_state { unsigned int stable, step; int anchor_uv, correcting; };
/* Android integer bits 0..7, decimal bits 8..11; Linux integers compatible. */
static inline int u30_saved_soc(unsigned int raw)
{
 unsigned int whole = raw & 255, decimal = (raw >> 8) & 15;
 if (whole > 100 || decimal > 9 || (whole == 100 && decimal)) return -1;
 return (whole * 10 + decimal + 5) / 10;
}
/* Derived charge in uAh uses the same rebased SOC and coulomb delta. */
static inline int u30_charge_uah(int cap, int total_mah, long long delta_uah)
{
 long long full = (long long)total_mah * 1000;
 long long charge = (long long)cap * total_mah * 10 + delta_uah;
 if (charge < 0) return 0;
 if (charge > full) return (int)full;
 return (int)charge;
}
static inline int u30_soc_tick(struct u30_soc_state *s, int valid, int ocv_uv,
                              int ocv_cap, int cap, int full_confirmed)
{
 int delta;
 if (!valid || ocv_cap < 0 || ocv_cap > 100 || cap < 0 || cap > 100) {
  *s = (struct u30_soc_state){0}; return cap;
 }
 delta = ocv_uv - s->anchor_uv;
 if (!s->stable || delta > 10000 || delta < -10000) {
  *s = (struct u30_soc_state){.stable=1, .anchor_uv=ocv_uv}; return cap;
 }
 if (s->stable < 12) { s->stable++; return cap; }
 if (full_confirmed) return 100;
 /* Incomplete charge-done below the factory full voltage is not 100%. */
 else if (ocv_cap >= 100) ocv_cap = 99;
 if (cap >= ocv_cap) { s->correcting=0; s->step=0; return cap; }
 if (!s->correcting && ocv_cap-cap < 5 && !full_confirmed) return cap;
 s->correcting=1;
 if (++s->step < 3) return cap;
 s->step=0;
 return cap+1;
}
#endif
