/* SPDX-License-Identifier: GPL-2.0-only */
/* U30 reconciliation and bounded charger API coordination. */
static int sc27xx_fgu_rebase_soc(struct sc27xx_fgu_data *data, int cap)
{
	int counter, ret;
	ret = sc27xx_fgu_get_clbcnt(data, &counter);
	if (ret)
		return ret;
	ret = sc27xx_fgu_save_last_cap(data, cap);
	if (ret)
		return ret;
	/* Commit together only after all reads and persistence succeeded. */
	data->init_cap = cap;
	data->init_clbcnt = counter;
	return 0;
}

static void sc27xx_fgu_soc_work(struct work_struct *work)
{
	struct sc27xx_fgu_data *data = container_of(to_delayed_work(work),
						struct sc27xx_fgu_data, soc_work);
	struct power_supply *charger;
	union power_supply_propval val;
	int cap = 0, ocv = 0, vbat = 0, cur = 0, temp = 0, target, ret;
	bool valid = false, changed = false, full_ready;
	enum u30_quiet_action quiet_action = U30_QUIET_RELEASE;

	mutex_lock(&data->lock);
	charger = power_supply_get_by_name("sgm41511-charger");
	if (!charger)
		goto tick;
	if (power_supply_get_property(charger, POWER_SUPPLY_PROP_STATUS, &val))
		goto put;
	if (val.intval != POWER_SUPPLY_STATUS_FULL) {
		/* A new charging cycle needs a new protected full endpoint.
		 * Preserve the verified baseline during discharge instead of
		 * artificially dropping a freshly unplugged 100% to 99%.
		 */
		if (val.intval == POWER_SUPPLY_STATUS_CHARGING && data->soc_full_verified) {
			data->soc_full_verified = false;
			changed = true;
		}
		goto put;
	}
	if (power_supply_get_property(charger, POWER_SUPPLY_PROP_ONLINE, &val) || !val.intval)
		goto put;
	if (power_supply_get_property(charger, POWER_SUPPLY_PROP_HEALTH, &val) ||
	    val.intval != POWER_SUPPLY_HEALTH_GOOD)
		goto put;
	if (power_supply_get_property(charger, POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX, &val) ||
	    val.intval < data->max_volt * 1000 - 32000)
		goto put;
	if (!data->bat_present || sc27xx_fgu_get_temp(data, &temp) || temp < 150 || temp > 450 ||
	    sc27xx_fgu_get_cur_now(data, &cur) || cur < -20 || cur > 20 ||
	    sc27xx_fgu_get_vbat_ocv(data, &ocv) || ocv < 4000000 ||
	    sc27xx_fgu_get_vbat_vol(data, &vbat) ||
	    sc27xx_fgu_get_capacity(data, &cap))
		goto put;
	if ((temp < 200 || temp > 400) &&
	    !(vbat * 1000 >= data->soc_full_uv && cur >= -20 &&
	      cur * 1000 <= data->soc_full_ua))
		goto put;
	valid = true;
put:
tick:
	full_ready = data->soc_state.stable >= 12;
	target = u30_soc_tick(&data->soc_state, valid, ocv,
		valid ? power_supply_ocv2cap_simple(data->cap_table, data->table_len, ocv) : 0,
		cap, valid && vbat * 1000 >= data->soc_full_uv && cur >= 0 &&
		cur * 1000 <= data->soc_full_ua);
	if (valid && target != cap) {
		ret = sc27xx_fgu_rebase_soc(data, target);
		if (!ret) {
			changed = true;
			if (target == 100)
				data->soc_full_verified = true;
			dev_info(data->dev, "SOC reconcile %d -> %d, OCV=%duV, full-confirmed=%d\n",
				 cap, target, ocv, vbat * 1000 >= data->soc_full_uv);
		} else {
			data->soc_state = (struct u30_soc_state){0};
			dev_err_ratelimited(data->dev, "SOC reconcile failed: %d\n", ret);
		}
	}
	/* A stored 100% also needs live full verification after reboot. */
	if (valid && full_ready && target == 100 && cap == 100 && !data->soc_full_verified &&
	    data->soc_state.stable >= 12 && vbat * 1000 >= data->soc_full_uv &&
	    cur >= 0 && cur * 1000 <= data->soc_full_ua) {
		data->soc_full_verified = true;
		changed = true;
	}
	if (sc27xx_fgu_fcc_poll(data, valid && full_ready && data->soc_full_verified &&
		vbat * 1000 >= data->soc_full_uv && cur >= 0 &&
		cur * 1000 <= data->soc_full_ua, charger, &quiet_action))
		changed = true;
	mutex_unlock(&data->lock);
	/* No charger write callback under FGU lock. Keep the charger reference
	 * until the command completes; teardown waits for this worker then AUTO.
	 */
	if (charger) {
		val.intval = quiet_action == U30_QUIET_INHIBIT &&
			!READ_ONCE(data->soc_stopping) ?
			POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE :
			POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO;
		ret = power_supply_set_property(charger, POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, &val);
		mutex_lock(&data->lock);
		data->quiet_error = ret;
		if (ret) {
			u30_quiet_release(&data->quiet_state, U30_QUIET_FAULT);
			if (data->fcc_state.active) {
				u30_fcc_abort(&data->fcc_state, U30_FCC_READ_ERROR);
				data->fcc_pending = 0;
			}
		}
		mutex_unlock(&data->lock);
		power_supply_put(charger);
	}
	if (changed)
		power_supply_changed(data->battery);
	if (!READ_ONCE(data->soc_stopping))
		schedule_delayed_work(&data->soc_work, msecs_to_jiffies(10000));
}

static void sc27xx_fgu_soc_stop(void *arg)
{
	struct sc27xx_fgu_data *data = arg;
	struct power_supply *charger;
	union power_supply_propval val = {
		.intval = POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO,
	};
	WRITE_ONCE(data->soc_stopping, true);
	cancel_delayed_work_sync(&data->soc_work);
	charger = power_supply_get_by_name("sgm41511-charger");
	if (charger) {
		power_supply_set_property(charger, POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR, &val);
		power_supply_put(charger);
	}
}
