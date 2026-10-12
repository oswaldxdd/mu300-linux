/* SPDX-License-Identifier: GPL-2.0-only */
/* Included after the FGU read/convert helpers, under the existing FGU lock. */
static int u30_fcc_ocv_tenth(struct sc27xx_fgu_data *data, int uv)
{
	int i;
	const struct power_supply_battery_ocv_table *t = data->cap_table;
	if (uv >= t[0].ocv)
		return t[0].capacity * 10;
	for (i = 1; i < data->table_len; i++) {
		if (uv < t[i].ocv)
			continue;
		if (t[i - 1].ocv <= t[i].ocv)
			return -EINVAL;
		return t[i].capacity * 10 + DIV_S64_ROUND_CLOSEST(
			(s64)(uv - t[i].ocv) *
			(t[i - 1].capacity - t[i].capacity) * 10,
			t[i - 1].ocv - t[i].ocv);
	}
	return t[data->table_len - 1].capacity * 10;
}

static int u30_fcc_read_low(struct sc27xx_fgu_data *data, int temp,
			    struct u30_fcc_start_sample *low)
{
	int i, ret, resistance = data->internal_resist;
	s64 sum = 0;
	u32 current_adc, voltage_adc;
	if (data->resist_table_len > 0) {
		int ratio = power_supply_temp2resist_simple(data->resist_table,
			data->resist_table_len, DIV_ROUND_CLOSEST(temp, 10));
		resistance = resistance * ratio / 100;
	}
	resistance += 10;
	for (i = 0; i < 8; i++) {
		ret = regmap_read(data->regmap,
			data->base + SC27XX_FGU_CURRENT_BUF + i * 4, &current_adc);
		if (ret)
			return ret;
		ret = regmap_read(data->regmap,
			data->base + SC27XX_FGU_VOLTAGE_BUF + i * 4, &voltage_adc);
		if (ret)
			return ret;
		low->buffered_current_ma[i] = sc27xx_fgu_adc_to_current(data,
			(s64)current_adc - SC27XX_FGU_CUR_BASIC_ADC);
		low->buffered_ocv_uv[i] = sc27xx_fgu_adc_to_voltage(data, voltage_adc) * 1000 -
			low->buffered_current_ma[i] * resistance;
		sum += low->buffered_ocv_uv[i];
	}
	low->ocv_uv = DIV_S64_ROUND_CLOSEST(sum, 8);
	low->cap_tenth = u30_fcc_ocv_tenth(data, low->ocv_uv);
	low->temp_decic = temp;
	low->battery_present = data->bat_present;
	low->reads_ok = 1;
	low->counter_continuous = 1;
	return 0;
}

static int u30_fcc_apply_full(struct sc27xx_fgu_data *data, int candidate)
{
	int counter, ret;
	u32 saved;
	if (!data->bat_present || (s64)candidate * 2 <= data->design_cap ||
	    (s64)candidate * 10 >= (s64)data->design_cap * 11)
		return -EINVAL;
	ret = sc27xx_fgu_get_clbcnt(data, &counter);
	if (ret)
		return ret;
	ret = sc27xx_fgu_save_last_cap(data, 100);
	if (ret)
		return ret;
	udelay(200);
	ret = regmap_read(data->regmap,
		data->base + SC27XX_FGU_USER_AREA_STATUS, &saved);
	if (ret)
		return ret;
	if ((saved & SC27XX_FGU_CAP_AREA_MASK) != 100)
		return -EIO;
	/* All fallible operations completed. Publish capacity and full anchor
	 * together under the same lock, without changing the hardware counter.
	 */
	data->total_cap = candidate;
	data->init_cap = 100;
	data->init_clbcnt = counter;
	data->fcc_pending = 0;
	data->fcc_sequence++;
	dev_info(data->dev, "FCC learned %d mAh, sequence=%u\n",
		 candidate, data->fcc_sequence);
	return 0;
}

static bool sc27xx_fgu_fcc_poll(struct sc27xx_fgu_data *data, bool protected_full,
		struct power_supply *charger, enum u30_quiet_action *action)
{
	struct u30_fcc_start_sample low = {0};
	struct u30_fcc_tick_sample sample = {
		.now_s = (u32)ktime_get_boottime_seconds(),
		.gain = data->cur_1000ma_adc,
		.previous_mah = data->total_cap, .design_mah = data->design_cap,
		.battery_present = data->bat_present, .protected_full = protected_full,
	};
	struct u30_quiet_sample quiet = {
		.now_s = sample.now_s, .battery_present = data->bat_present,
		.learning_active = data->fcc_state.active,
		.learned_sequence = data->fcc_sequence || data->fcc_restored,
	};
	union power_supply_propval val;
	int counter, ocv = 0, temp = 0, vbat = 0, ret, supply_ret = -ENODEV;
	bool was_active = data->fcc_state.active;
	bool was_quiet = data->quiet_state.active;
	ret = sc27xx_fgu_get_temp(data, &temp);
	if (!ret)
		ret = sc27xx_fgu_get_vbat_ocv(data, &ocv);
	if (!ret)
		ret = sc27xx_fgu_get_vbat_vol(data, &vbat);
	if (!ret && !data->fcc_state.active && !protected_full && ocv <= 3650000) {
		ret = u30_fcc_read_low(data, temp, &low);
		if (!ret)
			quiet.low = &low;
	}
	if (!ret)
		ret = sc27xx_fgu_get_clbcnt(data, &counter);
	if (!ret) {
		sample.counter = (u32)counter;
		sample.reads_ok = 1;
	}
	quiet.temp_decic = temp;
	quiet.voltage_uv = vbat * 1000;
	quiet.ocv_uv = ocv;
	if (charger) {
		supply_ret = power_supply_get_property(charger, POWER_SUPPLY_PROP_ONLINE, &val);
		if (!supply_ret) quiet.powered = val.intval != 0;
		if (!supply_ret)
			supply_ret = power_supply_get_property(charger, POWER_SUPPLY_PROP_HEALTH, &val);
		if (!supply_ret) quiet.healthy = val.intval == POWER_SUPPLY_HEALTH_GOOD;
		if (!supply_ret)
			supply_ret = power_supply_get_property(charger, POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, &val);
		if (!supply_ret)
			quiet.inhibited_readback = val.intval == POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE;
	}
	quiet.reads_ok = sample.reads_ok && !supply_ret;
	*action = u30_quiet_tick(&data->quiet_state, &quiet);
	/* The same real low reference and hardware counter qualify the learning
	 * start; requesting inhibition or just displaying low SOC never does.
	 */
	if (*action == U30_QUIET_ACCEPT_RELEASE) sample.low = &low;
	if (was_active && (supply_ret || !quiet.powered))
		ret = u30_fcc_abort(&data->fcc_state,
			supply_ret ? U30_FCC_READ_ERROR : U30_FCC_INPUT_LOST);
	else
		ret = u30_fcc_tick(&data->fcc_state, &sample);
	if (!was_quiet && data->quiet_state.active)
		dev_info(data->dev, "FCC quiet window requested; timeout=%us\n", U30_QUIET_MAX_S);
	if (!was_active && data->fcc_state.active) {
		data->fcc_pending = 0;
		dev_info(data->dev, "FCC learning start, OCV=%duV cap=%d/1000\n",
			 low.ocv_uv, low.cap_tenth);
	}
	if (ret < 0) {
		data->fcc_pending = 0;
		dev_warn_ratelimited(data->dev, "FCC learning invalidated: %d\n", -ret);
	} else if (ret > 0) {
		data->fcc_pending = ret;
	}
	if (quiet.reads_ok && quiet.powered && quiet.healthy &&
	    sample.battery_present && protected_full && data->fcc_pending) {
		ret = u30_fcc_apply_full(data, data->fcc_pending);
		if (!ret)
			return true;
		dev_warn_ratelimited(data->dev, "FCC apply failed: %d\n", ret);
	}
	return false;
}

/* Restore only a model qualified on a previous boot. Do not move the saved
 * SOC/coulomb anchor: that anchor already belongs to this model. */
static int u30_fcc_restore_model(struct sc27xx_fgu_data *data, unsigned int model,
		unsigned int design, unsigned int gain, u32 now)
{
	if (!data->bat_present || data->design_cap <= 0 || data->cur_1000ma_adc <= 0 ||
	    design != (unsigned int)data->design_cap ||
	    gain != (unsigned int)data->cur_1000ma_adc || model < 1000 || model > 6000 ||
	    (s64)model * 2 <= design || (s64)model * 10 >= (s64)design * 11)
		return -EINVAL;
	if (data->fcc_sequence || now > data->fcc_restore_deadline)
		return -EBUSY;
	if (data->fcc_restored)
		return data->total_cap == (int)model ? 0 : -EALREADY;
	data->fcc_state = (struct u30_fcc_state){0};
	data->fcc_pending = 0;
	data->total_cap = model;
	data->fcc_restored = true;
	return 0;
}

static ssize_t fcc_restore_store(struct device *dev, struct device_attribute *attr,
				const char *buf, size_t count)
{
	struct sc27xx_fgu_data *data = dev_get_drvdata(dev);
	char *copy, *cursor, *token;
	unsigned int values[3];
	int i, ret = -EINVAL;
	if (!count || count > 64 || memchr(buf, '\0', count))
		return -EINVAL;
	copy = kstrndup(buf, count, GFP_KERNEL);
	if (!copy)
		return -ENOMEM;
	cursor = strim(copy);
	for (i = 0; i < 3; i++) {
		token = strsep(&cursor, " ");
		if (!token || kstrtouint(token, 10, &values[i]))
			goto out;
	}
	if (cursor)
		goto out;
	mutex_lock(&data->lock);
	ret = u30_fcc_restore_model(data, values[0], values[1], values[2],
		(u32)ktime_get_boottime_seconds());
	mutex_unlock(&data->lock);
out:
	kfree(copy);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(fcc_restore);

static ssize_t fcc_status_show(struct device *dev, struct device_attribute *attr,
			     char *buf)
{
	struct sc27xx_fgu_data *data = dev_get_drvdata(dev);
	ssize_t len;
	mutex_lock(&data->lock);
	len = sysfs_emit(buf,
		"model_mah=%d design_mah=%d gain=%d sequence=%u active=%d candidate_mah=%d pending_mah=%d reason=%d start_s=%u last_s=%u net_counts=%lld restored=%d restore_deadline=%u quiet_active=%d quiet_attempted=%d quiet_reason=%d quiet_start_s=%u quiet_stable_s=%u quiet_error=%d\n",
		data->total_cap, data->design_cap, data->cur_1000ma_adc,
		data->fcc_sequence, data->fcc_state.active, data->fcc_state.candidate_mah,
		data->fcc_pending, data->fcc_state.reason, data->fcc_state.start_s,
		data->fcc_state.last_s, data->fcc_state.net_counts,
		data->fcc_restored, data->fcc_restore_deadline,
		data->quiet_state.active, data->quiet_state.attempted,
		data->quiet_state.reason, data->quiet_state.start_s,
		data->quiet_state.stable_s, data->quiet_error);
	mutex_unlock(&data->lock);
	return len;
}
static DEVICE_ATTR_RO(fcc_status);
static struct attribute *u30_fcc_attrs[] = {
	&dev_attr_fcc_status.attr, &dev_attr_fcc_restore.attr, NULL
};
static const struct attribute_group u30_fcc_group = { .attrs = u30_fcc_attrs };
