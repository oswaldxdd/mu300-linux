/* Run real driver code without touching hardware; model runtime PM references. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>

typedef uint16_t u16;
typedef uint32_t u32;
struct device { int unused; };
struct sipa_delegator {
	struct device *pdev;
	int prod_id;
	bool pd_eb_flag, pd_get_flag;
};
#define SMSG_FLG_DELE_ENABLE 7
#define SMSG_FLG_DELE_DISABLE 8
#define SMSG_VAL_DELE_REQ_SUCCESS 0
#define SMSG_VAL_DELE_REQ_FAIL 1
#define pr_info(...) ((void)0)
#define pr_warn_ratelimited(...) ((void)0)

static int results[5], count, gets, refs, puts, failed_puts, sleeps, enabled, reply;
static int pm_runtime_get_sync(struct device *dev)
{
	int i = gets < count ? gets : count - 1;
	(void)dev;
	gets++;
	refs++;
	return results[i];
}
static void pm_runtime_put_noidle(struct device *dev)
{
	(void)dev;
	failed_puts++;
	assert(refs > 0);
	refs--;
}
static void pm_runtime_put(struct device *dev)
{
	(void)dev;
	puts++;
	assert(refs > 0);
	refs--;
}
static void msleep(unsigned int delay)
{
	assert(delay == 20);
	sleeps++;
}
static void sipa_set_enabled(bool value) { enabled = value; }
static void sipa_dele_start_done_work(struct sipa_delegator *d, u16 flag, u32 val)
{
	(void)d;
	assert(flag == SMSG_FLG_DELE_ENABLE);
	reply = val;
}
static void sipa_dele_on_commad(void *priv, u16 flag, u32 data)
{
	(void)priv; (void)flag; (void)data;
}
#include "sipa_pm_under_test.h"

static void reset(int result)
{
	int i;
	for (i = 0; i < 5; i++) results[i] = result;
	count = 1;
	gets = refs = puts = failed_puts = sleeps = enabled = 0;
	reply = -1;
}
int main(void)
{
	struct device dev = {0};
	struct sipa_delegator d = { .pdev = &dev };
	int i;
	/* Both 0 and 1 are success; duplicate commands must not leak/underflow. */
	for (i = 0; i <= 1; i++) {
		reset(i);
		cp_dele_on_commad(&d, SMSG_FLG_DELE_ENABLE, 0);
		assert(gets == 1 && refs == 1 && failed_puts == 0 && sleeps == 0);
		assert(enabled && d.pd_get_flag && d.pd_eb_flag && reply == 0);
		cp_dele_on_commad(&d, SMSG_FLG_DELE_ENABLE, 0);
		assert(gets == 1 && refs == 1);
		cp_dele_on_commad(&d, SMSG_FLG_DELE_DISABLE, 0);
		cp_dele_on_commad(&d, SMSG_FLG_DELE_DISABLE, 0);
		assert(refs == 0 && puts == 1 && !d.pd_get_flag && !d.pd_eb_flag);
	}
	/* Persistent and temporary failures: no success ack and no owned reference. */
	for (i = 0; i < 3; i++) {
		int ret = i == 0 ? -EACCES : i == 1 ? -EAGAIN : -EBUSY;
		reset(ret);
		cp_dele_on_commad(&d, SMSG_FLG_DELE_ENABLE, 0);
		assert(gets == (i == 0 ? 1 : 5) && failed_puts == gets && refs == 0);
		assert(sleeps == (i == 0 ? 0 : 4));
		assert(!enabled && !d.pd_get_flag && !d.pd_eb_flag && reply == 1);
		cp_dele_on_commad(&d, SMSG_FLG_DELE_DISABLE, 0);
		assert(puts == 0 && refs == 0);
	}
	reset(-EAGAIN);
	count = 2; results[1] = 1;
	cp_dele_on_commad(&d, SMSG_FLG_DELE_ENABLE, 0);
	assert(gets == 2 && failed_puts == 1 && sleeps == 1 && refs == 1);
	assert(enabled && reply == 0);
	cp_dele_on_commad(&d, SMSG_FLG_DELE_DISABLE, 0);
	assert(refs == 0 && puts == 1);
	return 0;
}
