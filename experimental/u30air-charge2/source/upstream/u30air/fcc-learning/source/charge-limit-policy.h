/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef U30_CHARGE_LIMIT_POLICY_H
#define U30_CHARGE_LIMIT_POLICY_H
/* 100 means ordinary stock charging. Missing SOC must never resume a hold. */
static inline int u30_limit_hold(int end, int capacity, int valid, int held)
{
    if (end == 100)
        return 0;
    if (end == 0)
        return 1;
    if (!valid)
        return 1;
    if (capacity >= end)
        return 1;
    if (capacity <= (end > 5 ? end - 5 : 0))
        return 0;
    return held;
}
#endif
