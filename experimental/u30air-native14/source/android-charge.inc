/* SPDX-License-Identifier: GPL-2.0-only */
/* Stock U30 battery profiles, with source and temperature limits. */
static int sgm_android_init(struct sgm41511_native *sgm)
{
	static const char * const names[] = { "unknown", "sdp", "dcp", "cdp", "fchg" };
	struct device_node *bat;
	u32 cells[20], pair[2], full_current;
	unsigned int term;
	char name[64];
	int i, j, ret;

	bat = of_parse_phandle(sgm->client->dev.of_node, "monitored-battery", 0);
	if (!bat) {
		sgm_update(sgm, SGM_CONTROL, SGM_CHARGE, 0);
		return -EINVAL;
	}
	ret = of_property_read_u32(bat, "constant-charge-current-max-microamp", &sgm->stock_current);
	if (ret || !sgm->stock_current || sgm->stock_current > 3000000)
		goto invalid;
	ret = of_property_read_u32(bat, "constant-charge-voltage-max-microvolt", &sgm->stock_voltage);
	if (ret || sgm->stock_voltage < 3856000 || sgm->stock_voltage > 4300000)
		goto invalid;
	ret = of_property_read_u32(bat, "charge-term-current-microamp", &sgm->stock_term);
	if (ret || sgm->stock_term < 60000 || sgm->stock_term > 480000)
		goto invalid;
	/* Finish CV to the stock full-current criterion (120 mA), keeping
	 * hardware termination, voltage ceiling and protection enabled. */
	ret = of_property_read_u32(bat, "fullbatt-current", &full_current);
	if (ret || full_current < 60000 || full_current > sgm->stock_term)
		goto invalid;
	sgm->stock_term = full_current / 60000 * 60000;
	/* Native OpenWrt has no Android charger-manager timeout supervisor.
	 * Enable hardware termination and the charge safety timer explicitly;
	 * preserve the duration, watchdog and JEITA fields. Never reset per tick.
	 */
	ret = sgm_update(sgm, SGM_TIMER, BIT(7) | BIT(3), BIT(7) | BIT(3));
	if (ret)
		goto invalid;
	for (i = 0; i < 5; i++) {
		snprintf(name, sizeof(name), "charge-%s-current-microamp", names[i]);
		ret = of_property_read_u32_array(bat, name, pair, 2);
		if (ret || !pair[0] || pair[0] > 3400000 || !pair[1] || pair[1] > 3200000)
			goto invalid;
		sgm->profiles[i].charge_ua = min(pair[0], sgm->stock_current);
		sgm->profiles[i].input = pair[1];
		snprintf(name, sizeof(name), "%s-jeita-temp-table", names[i]);
		if (of_property_count_u32_elems(bat, name) != 20)
			goto invalid;
		ret = of_property_read_u32_array(bat, name, cells, 20);
		if (ret)
			goto invalid;
		for (j = 0; j < 5; j++) {
			struct u30_jeita_row *row = &sgm->profiles[i].rows[j];
			row->temp = (int)cells[j * 4] - 1000;
			row->recovery = (int)cells[j * 4 + 1] - 1000;
			row->charge_ua = cells[j * 4 + 2];
			row->voltage = cells[j * 4 + 3];
			if (row->temp < -200 || row->temp > 500 ||
			    row->recovery < -200 || row->recovery > 500 ||
			    row->charge_ua > 3000000 || row->voltage < 3856000 ||
			    row->voltage > sgm->stock_voltage ||
			    (j && row->temp <= sgm->profiles[i].rows[j - 1].temp))
				goto invalid;
		}
	}
	ret = sgm_read(sgm, 3, &term);
	if (ret)
		goto invalid;
	sgm->rearm_pending = (term & 15) != sgm->stock_term / 60000 - 1;
	ret = sgm_update(sgm, 3, 15, sgm->stock_term / 60000 - 1);
	if (ret)
		goto invalid;
	of_node_put(bat);
	sgm->charge_source = -1;
	return 0;
invalid:
	of_node_put(bat);
	/* A failed profile or protection setup must not leave charge enabled. */
	sgm_update(sgm, SGM_CONTROL, SGM_CHARGE, 0);
	return ret ?: -EINVAL;
}

/* Read other supplies before taking the charger lock: FGU can query us. */
static struct u30_charge_sample sgm_android_sample(void)
{
	struct u30_charge_sample sample = { };
	struct power_supply *fgu, *pd;
	struct regmap *pmic;
	union power_supply_propval val;
	unsigned int bc;
	int type, voltage;

	fgu = power_supply_get_by_name("sc27xx-fgu");
	if (fgu) {
		if (!power_supply_get_property(fgu, POWER_SUPPLY_PROP_TEMP, &val)) {
			sample.temp = val.intval;
			sample.valid_temp = sample.temp >= -400 && sample.temp <= 800;
		}
		if (fgu->dev.parent && fgu->dev.parent->parent) {
			pmic = dev_get_regmap(fgu->dev.parent->parent, NULL);
			if (pmic && !regmap_read(pmic, 0x239c, &bc))
				sample.source = u30_bc_source(bc);
		}
		power_supply_put(fgu);
	}
	pd = power_supply_get_by_name("sprd-tcpm-source-psy-sc27xx-pd");
	if (!pd)
		return sample;
	if (power_supply_get_property(pd, POWER_SUPPLY_PROP_ONLINE, &val) || !val.intval)
		goto out;
	if (power_supply_get_property(pd, POWER_SUPPLY_PROP_USB_TYPE, &val))
		goto out;
	type = val.intval;
	if (type != POWER_SUPPLY_USB_TYPE_PD && type != POWER_SUPPLY_USB_TYPE_C)
		goto out;
	if (power_supply_get_property(pd, POWER_SUPPLY_PROP_VOLTAGE_NOW, &val))
		goto out;
	voltage = val.intval;
	if (voltage < 4500000 || voltage > 5500000)
		goto out;
	if (power_supply_get_property(pd, POWER_SUPPLY_PROP_CURRENT_NOW, &val) ||
	    val.intval < 100000 || val.intval > 3200000)
		goto out;
	sample.source = 4;
	sample.contract = val.intval;
out:
	power_supply_put(pd);
	return sample;
}

/* Caller holds lock. Failure/temperature stop must precede charge enable. */
static int sgm_android_apply(struct sgm41511_native *sgm, struct u30_charge_sample sample,
			     bool powered, unsigned int fault, bool *allowed, bool *changed)
{
	struct u30_charge_profile *profile;
	unsigned int input, charge_ua, voltage, reg, next;
	int source = sample.source, zone, ret;

	*allowed = false;
	if (!powered)
		source = 0;
	if (source != sgm->charge_source) {
		sgm->jeita = (struct u30_jeita_state){ };
		sgm->charge_source = source;
		*changed = true;
	}
	profile = &sgm->profiles[source];
	/* BC1.2 alone guarantees 1.5 A; only a live Type-C/PD contract permits more. */
	input = min(profile->input, source == 2 || source == 3 ? 1500000U : 500000U);
	if (source == 4)
		input = min(profile->input, sample.contract);
	charge_ua = min(profile->charge_ua, sgm->stock_current);
	voltage = sgm->stock_voltage;
	if (!sample.valid_temp || fault || !powered)
		goto stop;
	zone = u30_jeita_zone(profile->rows, 5, &sgm->jeita, sample.temp);
	if (!zone || zone == 5)
		goto stop;
	charge_ua = min(charge_ua, profile->rows[zone].charge_ua);
	voltage = min(voltage, profile->rows[zone].voltage);
	if (!charge_ua)
		goto stop;
	*allowed = true;
stop:
	if (!*allowed) {
		ret = sgm_update(sgm, SGM_CONTROL, SGM_CHARGE, 0);
		if (ret)
			return ret;
		charge_ua = 0;
		input = min(input, 500000U);
	}
	ret = sgm_read(sgm, 0, &reg);
	if (ret)
		goto failed;
	input = (input - 100000) / 100000;
	/* Android also ramps charge_ua; decreases are immediate, increases are bounded. */
	next = min(input, (reg & 31) + 2);
	ret = sgm_update(sgm, 0, 31, next);
	if (ret)
		goto failed;
	*changed |= next != (reg & 31);
	ret = sgm_read(sgm, SGM_CURRENT, &reg);
	if (ret)
		goto failed;
	next = min(charge_ua / 60000, (reg & 63) + 5);
	ret = sgm_update(sgm, SGM_CURRENT, 63, next);
	if (ret)
		goto failed;
	*changed |= next != (reg & 63);
	ret = sgm_update(sgm, SGM_VOLTAGE, GENMASK(7, 3), u30_voltage_code(voltage) << 3);
	if (ret)
		goto failed;
	/* Re-evaluate an inherited charge-done latch once after correcting ITERM.
	 * All source, temperature and fault guards have passed before this point.
	 * Preserve termination and safety timers; never restart each full poll.
	 */
	if (*allowed && sgm->rearm_pending && !sgm->quiet_requested) {
		ret = sgm_read(sgm, SGM_STATUS, &reg);
		if (ret)
			goto failed;
		if (((reg >> 3) & 3) == 3) {
			ret = sgm_update(sgm, SGM_CONTROL, SGM_CHARGE, 0);
			if (!ret)
				ret = sgm_update(sgm, SGM_CONTROL, SGM_CHARGE, SGM_CHARGE);
			if (ret)
				goto failed;
			*changed = true;
		}
		sgm->rearm_pending = false;
	}
	sgm->charge_temp = sample.temp;
	sgm->charge_allowed = *allowed;
	return 0;
failed:
	*allowed = false;
	sgm->charge_allowed = false;
	sgm_update(sgm, SGM_CONTROL, SGM_CHARGE, 0);
	return ret;
}

static ssize_t charge_policy_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct sgm41511_native *sgm = dev_get_drvdata(dev);
	int ret;
	mutex_lock(&sgm->lock);
	ret = sysfs_emit(buf, "stock-android source=%d temp=%d allowed=%d; 5V only; BC1.2 cap=1500000uA\n",
			sgm->charge_source, sgm->charge_temp, sgm->charge_allowed);
	mutex_unlock(&sgm->lock);
	return ret;
}
static DEVICE_ATTR_RO(charge_policy);
