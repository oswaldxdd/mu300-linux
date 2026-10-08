// SPDX-License-Identifier: GPL-2.0-only
/*
 * ZTE U30 Air SGM41511 charger and externally-powered USB Host policy.
 *
 * This second release never sources connector VBUS. A Host request requires
 * valid external input; data role and charging are independent. The original
 * vendor DTB is preserved, and DWC3 still starts as Device. Charger temperature,
 * input-current limiting and safety-timer protection stay in the chip.
 */
#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/power_supply.h>
#include <linux/pm_runtime.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/of_regulator.h>
#include <linux/usb/role.h>
#include <linux/usb.h>
#include <linux/usb/u30air-usb.h>
#include <linux/jiffies.h>
#include <linux/workqueue.h>
#include <linux/timekeeping.h>
#include <linux/regmap.h>

#include "sgm41511-policy.h"
#include "host-policy.h"
#include "android-charge-policy.h"
#include "fcc-quiet-policy.h"

#define SGM_CONTROL 0x01
#define SGM_CURRENT 0x02
#define SGM_VOLTAGE 0x04
#define SGM_TIMER 0x05
#define SGM_STATUS 0x08
#define SGM_FAULT 0x09
#define SGM_VBUS 0x0a
#define SGM_PART 0x0b
#define SGM_BOOST BIT(5)
#define SGM_CHARGE BIT(4)
#define SGM_WATCHDOG GENMASK(5, 4)
#define SGM_POLL_MS 2000

struct sgm41511_native {
	struct i2c_client *client;
	struct mutex lock;
	struct delayed_work poll;
	struct power_supply *psy;
	struct usb_role_switch *role_sw;
	struct u30_policy policy;
	unsigned long auto_after;
	bool host_pending;
	unsigned int old_status;
	unsigned int old_fault;
	unsigned int old_vbus;

	struct u30_charge_profile profiles[5];
	struct u30_jeita_state jeita;
	unsigned int stock_current, stock_voltage;
	unsigned int stock_term;
	bool rearm_pending;
	int charge_source, charge_temp;
	bool charge_allowed;
	bool stopping;
	/* Bounded lease owned by this charger, independent of FGU polling. */
	bool quiet_requested, quiet_attempted;
	u64 quiet_deadline;
};

static int sgm_read(struct sgm41511_native *sgm, u8 reg, unsigned int *value)
{
	int ret = i2c_smbus_read_byte_data(sgm->client, reg);

	if (ret < 0)
		return ret;
	*value = ret;
	return 0;
}

/* Caller holds lock. Avoid replaying the self-clearing watchdog-reset bit. */
static int sgm_update(struct sgm41511_native *sgm, u8 reg, u8 mask, u8 value)
{
	unsigned int old, next;
	int ret = sgm_read(sgm, reg, &old);

	if (ret)
		return ret;
	next = (old & ~mask) | (value & mask);
	if (reg == SGM_CONTROL)
		next &= ~BIT(6);
	if (next == old)
		return 0;
	return i2c_smbus_write_byte_data(sgm->client, reg, next);
}

static int sgm_boost_off(struct sgm41511_native *sgm)
{
	unsigned int control;
	int ret = sgm_update(sgm, SGM_CONTROL, SGM_BOOST, 0);

	if (ret)
		return ret;
	ret = sgm_read(sgm, SGM_CONTROL, &control);
	if (ret)
		return ret;
	return (control & SGM_BOOST) ? -EIO : 0;
}

#include "android-charge.inc"

/* Caller holds lock. Only CHG_CONFIG changes; hardware owns termination. */
static int sgm_keep_charging(struct sgm41511_native *sgm, bool powered,
                            unsigned int status, unsigned int fault,
                            unsigned int control, bool *changed)
{
    unsigned int readback;
    int ret;

    if (!sgm41511_charge_needed(powered, status, fault, control))
        return 0;
    ret = sgm_update(sgm, SGM_CONTROL, SGM_CHARGE, SGM_CHARGE);
    if (!ret)
        ret = sgm_read(sgm, SGM_CONTROL, &readback);
    if (ret)
        return ret;
    if (!(readback & SGM_CHARGE))
        return -EIO;
    *changed = true;
    dev_info(&sgm->client->dev, "charging enabled by common Device/Host policy\n");
    return 0;
}

static int sgm_find_role(struct sgm41511_native *sgm)
{
	struct device_node *node;
	struct usb_role_switch *sw;

	if (sgm->role_sw)
		return 0;
	node = of_find_compatible_node(NULL, NULL, "snps,sprd-dwc3");
	if (!node)
		return -ENODEV;
	sw = usb_role_switch_find_by_fwnode(of_fwnode_handle(node));
	of_node_put(node);
	if (IS_ERR(sw))
		return PTR_ERR(sw);
	if (!sw)
		return -EPROBE_DEFER;
	sgm->role_sw = sw;
	return 0;
}

static int sgm_host(struct sgm41511_native *sgm)
{
	unsigned int status, vbus, control;
	int ret;

	ret = sgm_find_role(sgm);
	if (ret)
		return ret;
	if (usb_role_switch_get_role(sgm->role_sw) != USB_ROLE_HOST &&
	    usb_u30air_gadget_busy() != 0)
		return -EBUSY;
	ret = sgm_boost_off(sgm);
	if (ret)
		return ret;
	ret = sgm_read(sgm, SGM_STATUS, &status);
	if (ret)
		return ret;
	ret = sgm_read(sgm, SGM_VBUS, &vbus);
	if (ret)
		return ret;
	if (!sgm41511_external_input(status, vbus))
		return -ENOLINK;
	/* Recheck input after programming, before enabling the data host. */
	ret = sgm_read(sgm, SGM_STATUS, &status);
	if (ret)
		return ret;
	ret = sgm_read(sgm, SGM_VBUS, &vbus);
	if (ret)
		return ret;
	ret = sgm_read(sgm, SGM_CONTROL, &control);
	if (ret)
		return ret;
	if (!sgm41511_external_input(status, vbus) || (control & SGM_BOOST))
		return -ENOLINK;
	ret = usb_role_switch_set_role(sgm->role_sw, USB_ROLE_HOST);
	if (!ret)
		sgm->host_pending = true;

	return ret;
}

static ssize_t data_role_show(struct device *dev, struct device_attribute *attr,
			     char *buf)
{
	struct sgm41511_native *sgm = dev_get_drvdata(dev);
	enum usb_role role;
	int ret;

	mutex_lock(&sgm->lock);
	ret = sgm_find_role(sgm);
	role = ret ? USB_ROLE_NONE : usb_role_switch_get_role(sgm->role_sw);
	mutex_unlock(&sgm->lock);
	if (ret)
		return ret;
	return sysfs_emit(buf, "%s\n", role == USB_ROLE_HOST ? "host" :
			  role == USB_ROLE_DEVICE ? "device" : "none");
}

static ssize_t data_role_store(struct device *dev, struct device_attribute *attr,
			      const char *buf, size_t count)
{
	struct sgm41511_native *sgm = dev_get_drvdata(dev);
	int ret;

	mutex_lock(&sgm->lock);
	if (sysfs_streq(buf, "auto")) {
		sgm->policy = (struct u30_policy){ .mode = U30_AUTO };
		ret = 0;
	} else if (sysfs_streq(buf, "host")) {
		ret = sgm_host(sgm);
		if (!ret)
			sgm->policy = (struct u30_policy){ .mode = U30_HOST };
	} else if (sysfs_streq(buf, "device")) {
		sgm->policy = (struct u30_policy){ .mode = U30_DEVICE };
		ret = sgm_find_role(sgm);
		if (!ret) {
			ret = usb_role_switch_set_role(sgm->role_sw, USB_ROLE_DEVICE);
			if (!ret) {
				sgm->host_pending = false;
				ret = sgm_boost_off(sgm);
			}
		}
	} else {
		ret = -EINVAL;
	}
	mutex_unlock(&sgm->lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(data_role);

static ssize_t otg_boost_show(struct device *dev, struct device_attribute *attr,
			     char *buf)
{
	struct sgm41511_native *sgm = dev_get_drvdata(dev);
	unsigned int control;
	int ret;

	mutex_lock(&sgm->lock);
	ret = sgm_read(sgm, SGM_CONTROL, &control);
	mutex_unlock(&sgm->lock);
	return ret ? ret : sysfs_emit(buf, "%u\n", !!(control & SGM_BOOST));
}
static DEVICE_ATTR_RO(otg_boost);

static ssize_t input_power_good_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct sgm41511_native *sgm = dev_get_drvdata(dev);
	unsigned int status, vbus;
	int ret;

	mutex_lock(&sgm->lock);
	ret = sgm_read(sgm, SGM_STATUS, &status);
	if (!ret)
		ret = sgm_read(sgm, SGM_VBUS, &vbus);
	mutex_unlock(&sgm->lock);
	return ret ? ret : sysfs_emit(buf, "%u\n",
				     sgm41511_external_input(status, vbus));
}
static DEVICE_ATTR_RO(input_power_good);

static ssize_t host_policy_show(struct device *dev,
			       struct device_attribute *attr, char *buf)
{
	struct sgm41511_native *sgm = dev_get_drvdata(dev);
	int mode, blocked;
	mutex_lock(&sgm->lock);
	mode = sgm->policy.mode;
	blocked = sgm->policy.blocked;
	mutex_unlock(&sgm->lock);
	return sysfs_emit(buf, "%s%s\n", mode == U30_AUTO ? "auto" :
			 mode == U30_HOST ? "host" : "device",
			 blocked ? " (waiting for reconnect or auto command)" : "");
}
static DEVICE_ATTR_RO(host_policy);

static struct attribute *sgm_attrs[] = {
	&dev_attr_charge_policy.attr,
	&dev_attr_data_role.attr,
	&dev_attr_host_policy.attr,
	&dev_attr_otg_boost.attr,
	&dev_attr_input_power_good.attr,
	NULL,
};
static const struct attribute_group sgm_group = { .attrs = sgm_attrs };

static enum power_supply_property sgm_props[] = {
	POWER_SUPPLY_PROP_ONLINE,
	POWER_SUPPLY_PROP_STATUS,
	POWER_SUPPLY_PROP_HEALTH,
	POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX,
	POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT_MAX,
	POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT,
	POWER_SUPPLY_PROP_CHARGE_TERM_CURRENT,
	POWER_SUPPLY_PROP_MODEL_NAME,
	POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,
};

static int sgm_get_property(struct power_supply *psy,
			    enum power_supply_property prop,
			    union power_supply_propval *val)
{
	struct sgm41511_native *sgm = power_supply_get_drvdata(psy);
	unsigned int status, vbus, value, fault;
	int ret = 0;

	mutex_lock(&sgm->lock);
	switch (prop) {
	case POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR:
		ret = sgm_read(sgm, SGM_CONTROL, &value);
		if (!ret)
			val->intval = sgm->quiet_requested &&
				ktime_get_boottime_seconds() < sgm->quiet_deadline &&
				!(value & SGM_CHARGE) ?
				POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE :
				POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO;
		break;
	case POWER_SUPPLY_PROP_ONLINE:
		ret = sgm_read(sgm, SGM_STATUS, &status);
		if (!ret)
			ret = sgm_read(sgm, SGM_VBUS, &vbus);
		if (!ret)
			val->intval = sgm41511_external_input(status, vbus);
		break;
	case POWER_SUPPLY_PROP_STATUS:
		ret = sgm_read(sgm, SGM_STATUS, &status);
		if (!ret)
			ret = sgm_read(sgm, SGM_VBUS, &vbus);
		if (ret)
			break;
		if (!sgm41511_external_input(status, vbus))
			val->intval = POWER_SUPPLY_STATUS_DISCHARGING;
		else if (FIELD_GET(GENMASK(4, 3), status) == 3)
			val->intval = POWER_SUPPLY_STATUS_FULL;
		else if (FIELD_GET(GENMASK(4, 3), status))
			val->intval = POWER_SUPPLY_STATUS_CHARGING;
		else
			val->intval = POWER_SUPPLY_STATUS_NOT_CHARGING;
		break;
	case POWER_SUPPLY_PROP_HEALTH:
		/* REG09 first read is latched; second read is the current fault. */
		ret = sgm_read(sgm, SGM_FAULT, &fault);
		if (!ret)
			ret = sgm_read(sgm, SGM_FAULT, &fault);
		if (ret)
			break;
		val->intval = POWER_SUPPLY_HEALTH_GOOD;
		if (fault & BIT(3))
			val->intval = POWER_SUPPLY_HEALTH_OVERVOLTAGE;
		else if ((fault & 7) == 6 || FIELD_GET(GENMASK(5, 4), fault) == 2)
			val->intval = POWER_SUPPLY_HEALTH_OVERHEAT;
		else if ((fault & 7) == 5)
			val->intval = POWER_SUPPLY_HEALTH_COLD;
		else if (FIELD_GET(GENMASK(5, 4), fault) == 3)
			val->intval = POWER_SUPPLY_HEALTH_SAFETY_TIMER_EXPIRE;
		else if (fault & GENMASK(5, 4))
			val->intval = POWER_SUPPLY_HEALTH_UNSPEC_FAILURE;
		break;
	case POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX:
		ret = sgm_read(sgm, SGM_VOLTAGE, &value);
		if (!ret)
			val->intval = sgm41511_voltage_uv(value);
		break;
	case POWER_SUPPLY_PROP_CHARGE_TERM_CURRENT:
		ret = sgm_read(sgm, 3, &value);
		if (!ret)
			val->intval = 60000 + (value & 15) * 60000;
		break;
	case POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT_MAX:
		ret = sgm_read(sgm, SGM_CURRENT, &value);
		if (!ret)
			val->intval = min(value & 63, 50U) * 60000;
		break;
	case POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT:
		ret = sgm_read(sgm, 0x00, &value);
		if (!ret)
			val->intval = 100000 + (value & 31) * 100000;
		break;
	case POWER_SUPPLY_PROP_MODEL_NAME:
		val->strval = "SGM41511";
		break;
	default:
		ret = -EINVAL;
	}
	mutex_unlock(&sgm->lock);
	return ret;
}

/* No FGU callbacks while holding charger lock. The requester supplies its own
 * coherent battery qualification; charger independently checks input/faults.
 * Repeated INHIBIT requests cannot extend the absolute lease or reopen it on
 * the same readable attachment after expiry. AUTO never overrides JEITA.
 */
static int sgm_set_property(struct power_supply *psy,
			    enum power_supply_property prop,
			    const union power_supply_propval *val)
{
	struct sgm41511_native *sgm = power_supply_get_drvdata(psy);
	unsigned int status, vbus, fault, control;
	int ret = 0;
	bool notify = false;
	if (prop != POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR ||
	    (val->intval != POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO &&
	     val->intval != POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE))
		return -EINVAL;
	mutex_lock(&sgm->lock);
	if (sgm->stopping) {
		ret = -ENODEV;
		goto out;
	}
	if (val->intval == POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO) {
		notify = sgm->quiet_requested;
		sgm->quiet_requested = false;
		goto out;
	}
	if (sgm->quiet_attempted) {
		ret = sgm->quiet_requested &&
			ktime_get_boottime_seconds() < sgm->quiet_deadline ? 0 : -EBUSY;
		goto out;
	}
	ret = sgm_read(sgm, SGM_STATUS, &status);
	if (!ret) ret = sgm_read(sgm, SGM_VBUS, &vbus);
	if (!ret) ret = sgm_read(sgm, SGM_FAULT, &fault);
	if (!ret) ret = sgm_read(sgm, SGM_FAULT, &fault);
	if (!ret) ret = sgm_read(sgm, SGM_CONTROL, &control);
	if (ret) goto out;
	if (!sgm41511_external_input(status, vbus) || fault ||
	    (control & SGM_BOOST) || !sgm->charge_allowed ||
	    sgm->charge_temp < 150 || sgm->charge_temp >= 450) {
		ret = -EAGAIN;
		goto out;
	}
	/* Only battery charging changes. SYS_MIN/BATFET/input/OTG are untouched. */
	ret = sgm_update(sgm, SGM_CONTROL, SGM_CHARGE, 0);
	if (!ret) ret = sgm_read(sgm, SGM_CONTROL, &control);
	if (!ret && (control & SGM_CHARGE)) ret = -EIO;
	if (!ret) {
		sgm->quiet_requested = sgm->quiet_attempted = true;
		sgm->quiet_deadline = ktime_get_boottime_seconds() + U30_QUIET_MAX_S;
		notify = true;
	}
out:
	/* Serialize scheduling with stop(): it sets stopping under this lock. */
	if (!ret && !sgm->stopping) mod_delayed_work(system_wq, &sgm->poll, 0);
	mutex_unlock(&sgm->lock);
	if (notify) power_supply_changed(sgm->psy);
	return ret;
}

static int sgm_property_is_writeable(struct power_supply *psy,
				    enum power_supply_property prop)
{
	return prop == POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR;
}

static const struct power_supply_desc sgm_psy_desc = {
	.name = "sgm41511-charger",
	.type = POWER_SUPPLY_TYPE_USB,
	.properties = sgm_props,
	.num_properties = ARRAY_SIZE(sgm_props),
	.get_property = sgm_get_property,
	.set_property = sgm_set_property,
	.property_is_writeable = sgm_property_is_writeable,
	.charge_behaviours = BIT(POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO) |
		BIT(POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE),
};

/* Sink-only first release: even an accidental regulator request cannot source. */
static int sgm_regulator_enable(struct regulator_dev *rdev)
{
	return -EOPNOTSUPP;
}

static int sgm_regulator_disable(struct regulator_dev *rdev)
{
	struct sgm41511_native *sgm = rdev_get_drvdata(rdev);
	int ret;

	mutex_lock(&sgm->lock);
	ret = sgm_boost_off(sgm);
	mutex_unlock(&sgm->lock);
	return ret;
}

static int sgm_regulator_enabled(struct regulator_dev *rdev)
{
	struct sgm41511_native *sgm = rdev_get_drvdata(rdev);
	unsigned int control;
	int ret;

	mutex_lock(&sgm->lock);
	ret = sgm_read(sgm, SGM_CONTROL, &control);
	mutex_unlock(&sgm->lock);
	return ret ? ret : !!(control & SGM_BOOST);
}

static const struct regulator_ops sgm_regulator_ops = {
	.enable = sgm_regulator_enable,
	.disable = sgm_regulator_disable,
	.is_enabled = sgm_regulator_enabled,
};

static const struct regulator_desc sgm_vbus_desc = {
	.name = "sgm41511-vbus",
	.of_match = "otg-vbus",
	.type = REGULATOR_VOLTAGE,
	.owner = THIS_MODULE,
	.ops = &sgm_regulator_ops,
	.fixed_uV = 5150000,
	.n_voltages = 1,
};

static int sgm_nic_present(struct usb_device *udev, void *data)
{
	bool *present = data;
	if (le16_to_cpu(udev->descriptor.idVendor) == 0x0bda &&
	    le16_to_cpu(udev->descriptor.idProduct) == 0x8153)
		*present = true;
	return 0;
}

/* Called by the charger worker, not the FGU. Valid input absence alone resets
 * the attachment latch; a transient unreadable bus cannot reopen the lease.
 */
static bool sgm_quiet_refresh(struct sgm41511_native *sgm, bool readable,
		bool powered, unsigned int fault, bool valid_temp, int temp)
{
	bool changed = false;
	if (readable && !powered) {
		changed = sgm->quiet_requested;
		sgm->quiet_attempted = sgm->quiet_requested = false;
	}
	if (sgm->quiet_requested &&
	    (!readable || fault || !powered || !valid_temp ||
	     temp < 150 || temp >= 450 ||
	     ktime_get_boottime_seconds() >= sgm->quiet_deadline)) {
		sgm->quiet_requested = false;
		changed = true;
	}
	return changed;
}

static void sgm_poll(struct work_struct *work)
{
	struct sgm41511_native *sgm = container_of(to_delayed_work(work),
						 struct sgm41511_native, poll);
	unsigned int status = 0, vbus = 0, fault = 0, control = 0;
	struct u30_input input = {0};
	struct u30_charge_sample sample = sgm_android_sample();
	bool charge_allowed = false;
	enum u30_action action;
	bool changed = false, nic = false;
	int ret, role_ret, charge_ret = 0;

	usb_for_each_dev(&nic, sgm_nic_present);
	mutex_lock(&sgm->lock);
	ret = sgm_read(sgm, SGM_CONTROL, &control);
	input.boost = !ret && (control & SGM_BOOST);
	if (input.boost)
		ret = sgm_boost_off(sgm);
	if (!ret)
		ret = sgm_read(sgm, SGM_STATUS, &status);
	if (!ret)
		ret = sgm_read(sgm, SGM_VBUS, &vbus);
	if (!ret)
		ret = sgm_read(sgm, SGM_FAULT, &fault);
	if (!ret)
		ret = sgm_read(sgm, SGM_FAULT, &fault);
	role_ret = sgm_find_role(sgm);
	input.readable = !ret;
	input.powered = !ret && sgm41511_external_input(status, vbus);
	changed |= sgm_quiet_refresh(sgm, !ret, input.powered, fault,
				     sample.valid_temp, sample.temp);
	if (!ret) {
		charge_ret = sgm_android_apply(sgm, sample, input.powered, fault, &charge_allowed, &changed);
		/* Apply may have stopped charge; refresh before attempting an enable. */
		if (!charge_ret && charge_allowed)
			charge_ret = sgm_read(sgm, SGM_CONTROL, &control);
		if (!charge_ret && charge_allowed) {
			if (sgm->quiet_requested) {
				charge_ret = sgm_update(sgm, SGM_CONTROL, SGM_CHARGE, 0);
				if (!charge_ret) charge_ret = sgm_read(sgm, SGM_CONTROL, &control);
				if (!charge_ret && (control & SGM_CHARGE)) charge_ret = -EIO;
			} else {
				charge_ret = sgm_keep_charging(sgm, input.powered, status, fault, control, &changed);
			}
		}
	}
	if (charge_ret) {
		dev_err_ratelimited(&sgm->client->dev, "charge-enable failed: %d\n", charge_ret);
	}
	input.host = !role_ret && (sgm->host_pending ||
		usb_role_switch_get_role(sgm->role_sw) == USB_ROLE_HOST);
	input.gadget_busy = !input.host && usb_u30air_gadget_busy() != 0;
	input.nic = nic;
	input.ready = !role_ret && time_after_eq(jiffies, sgm->auto_after);
	action = u30_tick(&sgm->policy, input);
	if (!role_ret && action == U30_TO_DEVICE) {
		role_ret = usb_role_switch_set_role(sgm->role_sw, USB_ROLE_DEVICE);
		if (role_ret)
			dev_err_ratelimited(&sgm->client->dev, "Device fallback failed: %d\n", role_ret);
		else {
			sgm->host_pending = false;
			dev_info(&sgm->client->dev, "USB Device requested (power/NIC/policy)\n");
		}
	} else if (!role_ret && action == U30_TO_HOST) {
		role_ret = sgm_host(sgm);
		if (role_ret) {
			sgm->policy.blocked = 1;
			dev_err_ratelimited(&sgm->client->dev, "external Host refused: %d\n", role_ret);
		} else {
			dev_info(&sgm->client->dev, "external-powered USB Host requested; boost off\n");
		}
	}
	if (!ret) {
		changed |= status != sgm->old_status || fault != sgm->old_fault ||
			  vbus != sgm->old_vbus;
		sgm->old_status = status;
		sgm->old_fault = fault;
		sgm->old_vbus = vbus;
	} else {
		dev_err_ratelimited(&sgm->client->dev, "charger monitor read failed: %d\n", ret);
	}
	mutex_unlock(&sgm->lock);
	if (changed)
		power_supply_changed(sgm->psy);
	if (!READ_ONCE(sgm->stopping))
		schedule_delayed_work(&sgm->poll, msecs_to_jiffies(SGM_POLL_MS));
}

static void sgm_stop(void *data)
{
	struct sgm41511_native *sgm = data;

	WRITE_ONCE(sgm->stopping, true);
	cancel_delayed_work_sync(&sgm->poll);
	mutex_lock(&sgm->lock);
	/* Preserve autonomous charger settings, including its watchdog. */
	sgm_boost_off(sgm);
	mutex_unlock(&sgm->lock);
	if (sgm->role_sw) {
		usb_role_switch_put(sgm->role_sw);
		sgm->role_sw = NULL;
	}
}

static void sgm_bus_put(void *data)
{
	pm_runtime_put(data);
}

static int sgm_probe(struct i2c_client *client)
{
	static char *supplied_to[] = { "sc27xx-fgu" };
	struct power_supply_config psy_config = { };
	struct regulator_config reg_config = { };
	struct of_regulator_match match = { .name = "otg-vbus", .desc = &sgm_vbus_desc };
	struct sgm41511_native *sgm;
	struct regulator_dev *rdev;
	unsigned int part;
	int ret;

	if (!i2c_check_functionality(client->adapter, I2C_FUNC_SMBUS_BYTE_DATA))
		return -EOPNOTSUPP;
	sgm = devm_kzalloc(&client->dev, sizeof(*sgm), GFP_KERNEL);
	if (!sgm)
		return -ENOMEM;
	/* This board polls charging continuously. Keep its controller clock on:
	 * a late threaded SPRD I2C interrupt otherwise accesses gated MMIO.
	 * Register this first so devres stops polling before releasing the bus.
	 */
	if (!client->adapter->dev.parent)
		return -ENODEV;
	ret = pm_runtime_resume_and_get(client->adapter->dev.parent);
	if (ret < 0)
		return dev_err_probe(&client->dev, ret, "cannot keep charger bus awake\n");
	ret = devm_add_action_or_reset(&client->dev, sgm_bus_put,
				     client->adapter->dev.parent);
	if (ret)
		return ret;
	sgm->client = client;
	mutex_init(&sgm->lock);
	INIT_DELAYED_WORK(&sgm->poll, sgm_poll);
	i2c_set_clientdata(client, sgm);
	ret = sgm_read(sgm, SGM_PART, &part);
	if (ret)
		return dev_err_probe(&client->dev, ret, "cannot identify charger\n");
	if ((part & GENMASK(6, 2)) != 0x14)
		return dev_err_probe(&client->dev, -ENODEV, "not SGM41511: REG0B=%02x\n", part);
	/* Disable VBUS boost. The shared poll may enable charging on valid input,
	 * and applies stock battery profiles; hardware safety timers remain unchanged. */
	ret = sgm_boost_off(sgm);
	if (ret)
		return dev_err_probe(&client->dev, ret, "cannot disable OTG boost\n");
	ret = sgm_android_init(sgm);
	if (ret)
		return dev_err_probe(&client->dev, ret, "invalid stock Android battery profile\n");
	sgm->policy.mode = U30_AUTO;
	sgm->auto_after = jiffies + msecs_to_jiffies(60000);
	psy_config.supplied_to = supplied_to;
	psy_config.num_supplicants = ARRAY_SIZE(supplied_to);
	psy_config.drv_data = sgm;
	psy_config.fwnode = dev_fwnode(&client->dev);
	sgm->psy = devm_power_supply_register(&client->dev, &sgm_psy_desc, &psy_config);
	if (IS_ERR(sgm->psy))
		return dev_err_probe(&client->dev, PTR_ERR(sgm->psy), "cannot register charger\n");
	ret = of_regulator_match(&client->dev, client->dev.of_node, &match, 1);
	if (ret <= 0)
		return dev_err_probe(&client->dev, ret ?: -EINVAL, "missing otg-vbus node\n");
	reg_config.dev = &client->dev;
	reg_config.of_node = match.of_node;
	reg_config.init_data = match.init_data;
	reg_config.driver_data = sgm;
	rdev = devm_regulator_register(&client->dev, &sgm_vbus_desc, &reg_config);
	if (IS_ERR(rdev))
		return dev_err_probe(&client->dev, PTR_ERR(rdev), "cannot register VBUS guard\n");
	/* Stop the worker before devres unregisters its power_supply/regulator. */
	ret = devm_add_action_or_reset(&client->dev, sgm_stop, sgm);
	if (ret)
		return ret;
	ret = devm_device_add_group(&client->dev, &sgm_group);
	if (ret)
		return ret;
	schedule_delayed_work(&sgm->poll, msecs_to_jiffies(SGM_POLL_MS));
	dev_info(&client->dev, "native10: Android charging profiles; charger bus awake; external Host auto after 60 s\n");
	return 0;
}

static void sgm_shutdown(struct i2c_client *client)
{
	sgm_stop(i2c_get_clientdata(client));
}

static const struct of_device_id sgm_of_match[] = {
	{ .compatible = "ti,bq2560x_chg" },
	{ .compatible = "sgmicro,sgm41511" },
	{ }
};
MODULE_DEVICE_TABLE(of, sgm_of_match);

static struct i2c_driver sgm_driver = {
	.driver = {
		.name = "sgm41511-native",
		.of_match_table = sgm_of_match,
	},
	.probe = sgm_probe,
	.shutdown = sgm_shutdown,
};
module_i2c_driver(sgm_driver);

MODULE_DESCRIPTION("U30 Air SGM41511 charger and external-powered USB Host policy");
MODULE_LICENSE("GPL");
