#!/usr/bin/env python3
"""Exercise the actual patched boot initializer and common charge predicate."""
from pathlib import Path
import os
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
BUILD = Path(os.environ.get('U30_BUILD', '/root/u30air-native12-build-20261005'))
s = (BUILD/'kernel-work/linux-7.2.8/drivers/power/supply/sc27xx_fuel_gauge.c').read_text()
start = s.index('static int sc27xx_fgu_get_boot_capacity(')
end = s.index('\nstatic int sc27xx_fgu_set_clbcnt', start)
boot = s[start:end]
start = s.index('static int sc27xx_fgu_cap_to_clbcnt(', s.index('static void sc27xx_fgu_disable('))
end = s.index('\nstatic int sc27xx_fgu_calibration', start)
counter = s[start:end]
assert 'sgm41511-charger' in s
assert '*status = POWER_SUPPLY_STATUS_UNKNOWN;' in s
assert 'delta_clbcnt * 10' in s and '(s64)delta_clbcnt' in s
assert '.shutdown = sc27xx_fgu_shutdown' in s
program = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include "sgm41511-policy.h"
typedef int64_t s64;
#define DIV_S64_ROUND_CLOSEST(n,d) (((n)+(d)/2)/(d))
#define DIV_ROUND_CLOSEST(n,d) (((n)+(d)/2)/(d))
#define SC27XX_FGU_SAMPLE_HZ 2
#define SC27XX_FGU_NORMAIL_POWERTON 5
#define SC27XX_FGU_CLBCNT_QMAXL 0x74
#define SC27XX_FGU_POCV 0x28
#define SC27XX_FGU_CUR_BASIC_ADC 8192
#define dev_warn(...) ((void)0)
#define dev_info(...) ((void)0)
struct sc27xx_fgu_data { void *dev,*regmap,*cap_table; int base,table_len,boot_volt,internal_resist,total_cap,cur_1000ma_adc; };
static int saved, written, first, fail_ocv, live_ocv, read_fail;
static bool sc27xx_fgu_is_first_poweron(struct sc27xx_fgu_data *d) { return first; }
static int sc27xx_fgu_read_last_cap(struct sc27xx_fgu_data *d,int *c) { *c=saved; return read_fail; }
static int sc27xx_fgu_save_boot_mode(struct sc27xx_fgu_data *d,int m) { assert(m==5); return 0; }
static int sc27xx_fgu_get_vbat_ocv(struct sc27xx_fgu_data *d,int *v) { *v=live_ocv; return fail_ocv; }
static int power_supply_ocv2cap_simple(void *t,int n,int v) { assert(v==3792000); return 42; }
static int sc27xx_fgu_save_last_cap(struct sc27xx_fgu_data *d,int c) { written=c; return 0; }
static int regmap_read(void *m,int r,int *v) { *v=r==0x74?4096:3792; return 0; }
static int sc27xx_fgu_adc_to_current(struct sc27xx_fgu_data *d,int v) { return v; }
static int sc27xx_fgu_adc_to_voltage(struct sc27xx_fgu_data *d,int v) { return v; }
'''
program += boot + '\n' + counter + r'''
int main(void) {
    struct sc27xx_fgu_data d={.total_cap=4050,.cur_1000ma_adc=1400};
    int c;
    live_ocv=3792000;
    saved=1071; written=-1;
    assert(sc27xx_fgu_get_boot_capacity(&d,&c)==0 && c==42 && written==42 && d.boot_volt==3792000);
    saved=50; written=-1;
    assert(sc27xx_fgu_get_boot_capacity(&d,&c)==0 && c==50 && written==-1);
    saved=4094; fail_ocv=-5;
    assert(sc27xx_fgu_get_boot_capacity(&d,&c)==-5);
    fail_ocv=0; read_fail=-5;
    assert(sc27xx_fgu_get_boot_capacity(&d,&c)==-5);
    read_fail=0; first=1;
    assert(sc27xx_fgu_get_boot_capacity(&d,&c)==0 && c==42);
    assert(sc27xx_fgu_cap_to_clbcnt(&d,100)==40824000);
    assert(sc27xx_fgu_cap_to_clbcnt(&d,50)==20412000);
    assert(sgm41511_charge_needed(1,0x24,0,0x8a));
    assert(!sgm41511_charge_needed(0,0x24,0,0x8a));
    assert(!sgm41511_charge_needed(1,0x3c,0,0x8a));
    assert(!sgm41511_charge_needed(1,0x24,0x08,0x8a));
    assert(!sgm41511_charge_needed(1,0x24,0,0x9a));
    assert(!sgm41511_charge_needed(1,0x24,0,0xaa));
    puts("Saved SOC 1071 recovery, valid SOC, first boot, read failures, coulomb arithmetic, full/fault/boost charge guards passed");
}
'''
with tempfile.TemporaryDirectory() as folder:
    p = Path(folder)
    (p/'test.c').write_text(program)
    subprocess.run(['gcc','-Werror','-Wno-unused-parameter','-fsanitize=undefined',
                    '-I',str(HERE/'source'),str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
