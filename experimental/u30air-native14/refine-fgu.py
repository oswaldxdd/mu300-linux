"""Idempotent native12 usable-capacity and confirmed full endpoint patch."""
from pathlib import Path
import sys

p=Path(sys.argv[1])/'drivers/power/supply/sc27xx_fuel_gauge.c'
s=p.read_text()
s=s.replace('    /* Stock U30 includes 10 mOhm of board-path resistance. */\n    if (data->var == &ump9620_info)\n        resistance += 10;',
            '\t/* Stock U30 includes 10 mOhm of board-path resistance. */\n\tif (data->var == &ump9620_info)\n\t\tresistance += 10;')
if 'native12 usable battery capacity' in s:
    p.write_text(s)
    raise SystemExit(0)
def replace(old,new):
    global s
    assert s.count(old)==1, old
    s=s.replace(old,new)
replace('int total_cap;', 'int total_cap;\n\tint design_cap;')
replace('bool soc_stopping;', 'bool soc_stopping, soc_full_verified;')
replace('data->total_cap = info->charge_full_design_uah / 1000;', '''
    /* native12 usable battery capacity: stock default, not learned FCC. */
    data->design_cap = info->charge_full_design_uah / 1000;
    data->total_cap = data->design_cap;
    if (data->var == &ump9620_info) {
        struct device_node *bat = of_parse_phandle(data->dev->of_node, "monitored-battery", 0);
        u32 usable;
        ret = bat ? of_property_read_u32(bat, "charge-full-microamp-hours", &usable) : -EINVAL;
        of_node_put(bat);
        if (ret || usable < 1000000 || usable > info->charge_full_design_uah) {
            power_supply_put_battery_info(data->battery, info);
            return ret ?: -EINVAL;
        }
        data->total_cap = usable / 1000;
    }''')
replace('val->intval = data->total_cap * 1000;',
        'val->intval = data->design_cap * 1000;')
replace('val->intval = clamp(value, 0, 100);', '''
        /* Reserve 100% for a protected, stable hardware full endpoint. */
        val->intval = clamp(value, 0,
            data->var == &ump9620_info && !data->soc_full_verified ? 99 : 100);''')
p.write_text(s)
