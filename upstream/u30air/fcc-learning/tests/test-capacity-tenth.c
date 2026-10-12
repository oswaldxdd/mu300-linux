/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
enum {
	POWER_SUPPLY_STATUS_UNKNOWN,
	POWER_SUPPLY_STATUS_CHARGING,
	POWER_SUPPLY_STATUS_DISCHARGING,
	POWER_SUPPLY_STATUS_NOT_CHARGING,
	POWER_SUPPLY_STATUS_FULL,
};
#include "../source/fgu-soc-policy.h"

int main(void)
{
	const int full = 4000;
	assert(u30_capacity_tenth(0, full, 0) == 0);
	assert(u30_capacity_tenth(4000, full, 0) == 1);
	assert(u30_capacity_tenth(2000000, full, 0) == 500);
	assert(u30_capacity_tenth(3123456, full, 0) == 781);
	assert(u30_capacity_tenth(3996000, full, 0) == 999);
	assert(u30_capacity_tenth(4000000, full, 0) == 999);
	assert(u30_capacity_tenth(4000000, full, 1) == 1000);
	assert(u30_capacity_tenth(9000000, full, 1) == 1000);
	assert(u30_capacity_tenth(-10, full, 1) == 0);
	assert(u30_capacity_tenth(100, 0, 0) == -1);
	assert(u30_unverified_full(POWER_SUPPLY_STATUS_FULL,
				   POWER_SUPPLY_STATUS_FULL, 0));
	assert(!u30_unverified_full(POWER_SUPPLY_STATUS_FULL,
				    POWER_SUPPLY_STATUS_FULL, 1));
	assert(!u30_unverified_full(POWER_SUPPLY_STATUS_CHARGING,
				    POWER_SUPPLY_STATUS_FULL, 0));
	return 0;
}
