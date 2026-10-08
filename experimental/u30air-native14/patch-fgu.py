#!/usr/bin/env python3
"""Apply the U30 Air saved-SOC and charger-status fixes to the pinned tree."""
from pathlib import Path
import sys

p = Path(sys.argv[1]) / 'drivers/power/supply/sc27xx_fuel_gauge.c'
s = p.read_text()
if 'U30 Air saved-SOC repair' in s:
    sys.exit(0)

def replace(old, new):
    global s
    assert s.count(old) == 1, old[:100]
    s = s.replace(old, new)

replace('static int sc27xx_fgu_get_temp(struct sc27xx_fgu_data *data, int *temp);',
        'static int sc27xx_fgu_get_temp(struct sc27xx_fgu_data *data, int *temp);\n'
        'static int sc27xx_fgu_get_vbat_ocv(struct sc27xx_fgu_data *data, int *val);')
replace('static const char * const sc27xx_charger_supply_name[] = {',
        'static const char * const sc27xx_charger_supply_name[] = {\n\t"sgm41511-charger",')
replace('\t\treturn sc27xx_fgu_save_boot_mode(data, SC27XX_FGU_NORMAIL_POWERTON);\n\t}',
'''        /* U30 Air saved-SOC repair: the always-on field may contain a
         * vendor encoding or corrupt/stale value, not a Linux percentage.
         * Do not clamp that value to 100 and initialize a full battery.
         */
        if (*cap >= 0 && *cap <= 100)
            return sc27xx_fgu_save_boot_mode(data, SC27XX_FGU_NORMAIL_POWERTON);
        dev_warn(data->dev, "invalid saved SOC %d; estimating from live OCV\\n", *cap);
        ret = sc27xx_fgu_get_vbat_ocv(data, &ocv);
        if (ret)
            return ret;
        data->boot_volt = ocv;
        *cap = power_supply_ocv2cap_simple(data->cap_table, data->table_len, ocv);
        dev_info(data->dev, "boot SOC %d%% from OCV %d uV\\n", *cap, ocv);
        ret = sc27xx_fgu_save_last_cap(data, *cap);
        if (ret)
            return ret;
        return sc27xx_fgu_save_boot_mode(data, SC27XX_FGU_NORMAIL_POWERTON);
    }''')
replace('int ret, cur_clbcnt, delta_clbcnt, delta_cap, temp;',
        'int ret, cur_clbcnt, delta_clbcnt, delta_cap;\n\ts64 temp;')
replace('delta_clbcnt = cur_clbcnt - data->init_clbcnt;',
        'delta_clbcnt = (s32)((u32)cur_clbcnt - (u32)data->init_clbcnt);')
replace('temp = DIV_ROUND_CLOSEST(delta_clbcnt * 10, 36 * SC27XX_FGU_SAMPLE_HZ);',
        'temp = DIV_S64_ROUND_CLOSEST((s64)delta_clbcnt * 10, 36 * SC27XX_FGU_SAMPLE_HZ);')
replace('value = DIV_ROUND_CLOSEST(value * 10,\n\t\t\t\t\t  36 * SC27XX_FGU_SAMPLE_HZ);',
        'value = DIV_S64_ROUND_CLOSEST((s64)value * 10,\n\t\t\t\t\t  36 * SC27XX_FGU_SAMPLE_HZ);')
replace('return DIV_ROUND_CLOSEST(cur_cap * 36 * data->cur_1000ma_adc * SC27XX_FGU_SAMPLE_HZ, 10);',
        'return DIV_S64_ROUND_CLOSEST((s64)cur_cap * 36 * data->cur_1000ma_adc * SC27XX_FGU_SAMPLE_HZ, 10);')
replace('''		val->intval = value;
		break;

	case POWER_SUPPLY_PROP_VOLTAGE_AVG:''',
        '''		val->intval = clamp(value, 0, 100);
		break;

	case POWER_SUPPLY_PROP_VOLTAGE_AVG:''')
replace('''			else
				*status = POWER_SUPPLY_STATUS_FULL;''',
        '''			else
				*status = POWER_SUPPLY_STATUS_UNKNOWN;''')
replace('''	case POWER_SUPPLY_PROP_CAPACITY:
		ret = sc27xx_fgu_save_last_cap(data, val->intval);''',
        '''	case POWER_SUPPLY_PROP_CAPACITY:
		if (val->intval < 0 || val->intval > 100) {
			ret = -EINVAL;
			break;
		}
		ret = sc27xx_fgu_save_last_cap(data, val->intval);''')
replace('''	case POWER_SUPPLY_PROP_CALIBRATE:
		sc27xx_fgu_adjust_cap(data, val->intval);''',
        '''	case POWER_SUPPLY_PROP_CALIBRATE:
		if (val->intval < 0 || val->intval > 100) {
			ret = -EINVAL;
			break;
		}
		sc27xx_fgu_adjust_cap(data, val->intval);''')
replace('static struct platform_driver sc27xx_fgu_driver = {',
'''/* Save the live estimate before restart: there is no charger-manager
 * userspace saving capacity on this OpenWrt image. Do not let a valid but
 * obsolete boot estimate survive every normal reboot.
 */
static void sc27xx_fgu_shutdown(struct platform_device *pdev)
{
    struct sc27xx_fgu_data *data = platform_get_drvdata(pdev);
    int cap, ret;

    mutex_lock(&data->lock);
    ret = sc27xx_fgu_get_capacity(data, &cap);
    if (!ret)
        ret = sc27xx_fgu_save_last_cap(data, clamp(cap, 0, 100));
    mutex_unlock(&data->lock);
    if (ret)
        dev_err(data->dev, "failed to save shutdown SOC: %d\\n", ret);
}

static struct platform_driver sc27xx_fgu_driver = {\n\t.shutdown = sc27xx_fgu_shutdown,''')
s = '#include <linux/workqueue.h>\n#include "fgu-soc-policy.h"\n' + s
replace('''		value = DIV_S64_ROUND_CLOSEST((s64)value * 10,
					  36 * SC27XX_FGU_SAMPLE_HZ);
		val->intval = sc27xx_fgu_adc_to_current(data, value);''', '''		/* Use the same SOC reference as CAPACITY after any rebase. */
		value = (s32)((u32)value - (u32)data->init_clbcnt);
		value = DIV_S64_ROUND_CLOSEST((s64)value * 10,
					  36 * SC27XX_FGU_SAMPLE_HZ);
		val->intval = u30_charge_uah(data->init_cap, data->total_cap,
					   sc27xx_fgu_adc_to_current(data, value));''')
replace('struct mutex lock;', '''struct mutex lock;
    struct delayed_work soc_work;
    struct u30_soc_state soc_state;
    int soc_full_uv, soc_full_ua;
    bool soc_stopping;''')
replace('static int sc27xx_fgu_probe(struct platform_device *pdev)',
        '#include "fgu-soc-reconcile.inc"\n\nstatic int sc27xx_fgu_probe(struct platform_device *pdev)')
probe_start = s.index('static int sc27xx_fgu_probe(struct platform_device *pdev)')
probe_end = s.index('\n#ifdef CONFIG_PM_SLEEP', probe_start)
probe = s[probe_start:probe_end]
needle = '\n\treturn 0;\n}'
assert probe.count(needle) == 1
insert = '''
    if (data->var == &ump9620_info) {
        struct device_node *bat = of_parse_phandle(dev->of_node, "monitored-battery", 0);
        u32 full_uv, full_ua;
        if (!bat)
            return -EINVAL;
        ret = of_property_read_u32(bat, "fullbatt-voltage", &full_uv);
        if (!ret)
            ret = of_property_read_u32(bat, "fullbatt-current", &full_ua);
        of_node_put(bat);
        if (ret || full_uv < 4000000 || full_uv > data->max_volt * 1000 ||
            !full_ua || full_ua > 420000)
            return ret ?: -EINVAL;
        data->soc_full_uv = full_uv;
        data->soc_full_ua = full_ua;
        INIT_DELAYED_WORK(&data->soc_work, sc27xx_fgu_soc_work);
        ret = devm_add_action_or_reset(dev, sc27xx_fgu_soc_stop, data);
        if (ret)
            return ret;
        schedule_delayed_work(&data->soc_work, msecs_to_jiffies(10000));
    }
'''
probe = probe.replace(needle, insert + needle)
s = s[:probe_start] + probe + s[probe_end:]
replace('''    mutex_lock(&data->lock);
    ret = sc27xx_fgu_get_capacity(data, &cap);''', '''    if (data->var == &ump9620_info)
        sc27xx_fgu_soc_stop(data);
    mutex_lock(&data->lock);
    ret = sc27xx_fgu_get_capacity(data, &cap);''')
# native12: confirmed temperature and cross-system encoding defects.
replace('data->resist_table_len, temp);',
        'data->resist_table_len, DIV_ROUND_CLOSEST(temp, 10));')
replace('''\t/* Return the battery OCV in micro volts. */
\t*val = vol * 1000 - cur * resistance;''', '''
    /* Stock U30 includes 10 mOhm of board-path resistance. */
    if (data->var == &ump9620_info)
        resistance += 10;
\t/* Return the battery OCV in micro volts. */
\t*val = vol * 1000 - cur * resistance;''')
replace('*cap = value & SC27XX_FGU_CAP_AREA_MASK;',
        '*cap = data->var == &ump9620_info ? u30_saved_soc(value) :\n'
        '\t\tvalue & SC27XX_FGU_CAP_AREA_MASK;')
p.write_text(s)
import subprocess
subprocess.run([sys.executable, str(Path(__file__).with_name('refine-fgu.py')), sys.argv[1]], check=True)
