/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef SGM41511_POLICY_H
#define SGM41511_POLICY_H

/* Shared by the kernel driver and host-side policy tests. */
static inline int sgm41511_external_input(unsigned int status,
					unsigned int vbus)
{
	return (status & 4) && (vbus & 128) && (status & 224) != 224;
}

static inline unsigned int sgm41511_voltage_uv(unsigned int value)
{
	unsigned int code = (value >> 3) & 31;

	if (code == 15)
		return 4352000;
	if (code > 24)
		code = 24;
	return 3856000 + code * 32000;
}

static inline unsigned int sgm41511_voltage_code(unsigned int ceiling)
{
	/* Conservative first release: never exceed the chip's 4.208 V default. */
	if (ceiling > 4208000)
		ceiling = 4208000;
	return (ceiling - 3856000) / 32000;
}

static inline unsigned int sgm41511_current_code(unsigned int ceiling)
{
	/* 1.02 A is a charger limit, not a promise about battery net current. */
	if (ceiling > 1020000)
		ceiling = 1020000;
	return ceiling / 60000;
}

/* USB data role is deliberately absent: one charge policy for both modes. */
static inline int sgm41511_charge_needed(int powered, unsigned int status,
                                       unsigned int fault, unsigned int control)
{
    return powered && !fault && !(control & 0x20) && !(control & 0x10) &&
           ((status >> 3) & 3) != 3;
}

#endif
