"""Compile the actual ADC/OCV/FCC commit helpers against deterministic faults."""
from pathlib import Path
import subprocess
import tempfile

here = Path(__file__).resolve().parent
src = (here/'source/fcc-kernel.inc').read_text()
def function(name):
    pos = src.index('static int '+name+'(')
    begin = src.index('{', pos)
    depth = 1
    end = begin + 1
    while depth:
        depth += (src[end] == '{') - (src[end] == '}')
        end += 1
    return src[pos:end]
prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
typedef long long s64;
typedef uint32_t u32;
#define SC27XX_FGU_CURRENT_BUF 0xf0
#define SC27XX_FGU_VOLTAGE_BUF 0xd0
#define SC27XX_FGU_USER_AREA_STATUS 0xa8
#define SC27XX_FGU_CAP_AREA_MASK 0xfff
#define SC27XX_FGU_CUR_BASIC_ADC 8192
static long long rnd(long long n,long long d) {return n>=0?(n+d/2)/d:-((-n+d/2)/d);}
#define DIV_S64_ROUND_CLOSEST(n,d) rnd(n,d)
#define DIV_ROUND_CLOSEST(n,d) rnd(n,d)
#define dev_info(...) ((void)0)
struct power_supply_battery_ocv_table {int ocv,capacity;};
#include "fcc-state.h"
struct sc27xx_fgu_data {
 int internal_resist,resist_table_len,base,total_cap,design_cap,init_cap,init_clbcnt;
 int fcc_pending,cur_1000ma_adc; unsigned int fcc_sequence,fcc_restore_deadline;
 bool fcc_restored; struct u30_fcc_state fcc_state;
 bool bat_present; void *regmap,*resist_table,*dev;
 int table_len; struct power_supply_battery_ocv_table *cap_table;
};
static int read_count,fail_read,fail_counter,fail_save;
static u32 saved,current_values[8],voltage_values[8];
static int regmap_read(void *map,int reg,u32 *out) {
 (void)map;read_count++;if(read_count==fail_read)return -EIO;
 if(reg==0xa8){*out=saved;return 0;}
 if(reg>=0xf0&&reg<=0x10c){*out=current_values[(reg-0xf0)/4];return 0;}
 assert(reg>=0xd0&&reg<=0xec);*out=voltage_values[(reg-0xd0)/4];return 0;
}
static int power_supply_temp2resist_simple(void *p,int n,int temp){(void)p;(void)n;assert(temp==25);return 100;}
static int sc27xx_fgu_adc_to_current(struct sc27xx_fgu_data *d,s64 v){(void)d;return (int)v;}
static int sc27xx_fgu_adc_to_voltage(struct sc27xx_fgu_data *d,s64 v){(void)d;return (int)v;}
static int sc27xx_fgu_get_clbcnt(struct sc27xx_fgu_data *d,int *v){(void)d;*v=100075;return fail_counter?-EIO:0;}
static int sc27xx_fgu_save_last_cap(struct sc27xx_fgu_data *d,int cap){(void)d;assert(cap==100);return fail_save?-EIO:0;}
static void udelay(int us){assert(us==200);}
'''
main = r'''
int main(void) {
 struct power_supply_battery_ocv_table t[]={{4252000,100},{3616000,10},{3539000,5},{3400000,0}};
 struct sc27xx_fgu_data d={.internal_resist=150,.resist_table_len=1,.cap_table=t,.table_len=4,
 .total_cap=3847,.design_cap=4050,.init_cap=93,.init_clbcnt=42,.fcc_pending=3550,.bat_present=1};
 assert(u30_fcc_ocv_tenth(&d,3539000)==50);
 assert(u30_fcc_ocv_tenth(&d,3577500)==75);
 assert(u30_fcc_ocv_tenth(&d,3616000)==100);
 assert(u30_fcc_ocv_tenth(&d,3300000)==0);
 for(int i=0;i<8;i++){current_values[i]=8192-40;voltage_values[i]=3500;}
 struct u30_fcc_start_sample low={0};
 assert(!u30_fcc_read_low(&d,250,&low));
 assert(read_count==16&&low.ocv_uv==3506400&&low.buffered_current_ma[7]==-40);
 assert(u30_fcc_start_valid(&low));
 for(int i=1;i<=16;i++){
  read_count=0;fail_read=i;low=(struct u30_fcc_start_sample){0};
  assert(u30_fcc_read_low(&d,250,&low)==-EIO&&!low.reads_ok);
 }
 fail_read=0;
 for(int fault=1;fault<=5;fault++) {
  struct sc27xx_fgu_data before=d;
  read_count=0;fail_counter=(fault==1);fail_save=(fault==2);
  fail_read=(fault==3)?1:0;saved=(fault==4)?0x95:100;
  int candidate=(fault==5)?0:3550;
  assert(u30_fcc_apply_full(&d,candidate)<0);
  assert(d.total_cap==before.total_cap&&d.init_cap==before.init_cap&&
   d.init_clbcnt==before.init_clbcnt&&d.fcc_pending==before.fcc_pending&&d.fcc_sequence==0);
 }
 fail_counter=fail_save=fail_read=0;saved=100;
 assert(!u30_fcc_apply_full(&d,3550));
 assert(d.total_cap==3550&&d.init_cap==100&&d.init_clbcnt==100075&&d.fcc_pending==0&&d.fcc_sequence==1);
 d.fcc_sequence=0;d.fcc_restore_deadline=60;d.cur_1000ma_adc=650;
 d.init_cap=81;d.init_clbcnt=1234;d.fcc_state.active=1;d.fcc_pending=3550;
 d.bat_present=0;
 assert(u30_fcc_restore_model(&d,3500,4050,650,30)==-EINVAL&&!d.fcc_restored);
 d.bat_present=1;
 assert(u30_fcc_restore_model(&d,3500,4050,650,61)==-EBUSY&&!d.fcc_restored);
 assert(u30_fcc_restore_model(&d,3500,4050,651,30)==-EINVAL&&!d.fcc_restored);
 assert(u30_fcc_restore_model(&d,2025,4050,650,30)==-EINVAL&&!d.fcc_restored);
 assert(!u30_fcc_restore_model(&d,3500,4050,650,30));
 assert(d.total_cap==3500&&d.init_cap==81&&d.init_clbcnt==1234&&
  d.fcc_restored&&!d.fcc_state.active&&!d.fcc_pending&&!d.fcc_sequence);
 assert(!u30_fcc_restore_model(&d,3500,4050,650,31));
 assert(u30_fcc_restore_model(&d,3600,4050,650,31)==-EALREADY&&d.total_cap==3500);
 d.fcc_sequence=1;
 assert(u30_fcc_restore_model(&d,3500,4050,650,31)==-EBUSY);
 puts("PASS: actual adapter reads all 8 ADC pairs; every read/save/readback failure preserves the previous model and anchor");
}
'''
with tempfile.TemporaryDirectory(prefix='u30-fcc-adapter-') as tmp:
    path = Path(tmp)
    c = path/'test.c'
    c.write_text(prefix+'\n'+ '\n'.join(function(n) for n in
        ('u30_fcc_ocv_tenth','u30_fcc_read_low','u30_fcc_apply_full',
         'u30_fcc_restore_model'))+'\n'+main)
    subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror',
        '-fsanitize=undefined,address','-I'+str(here/'source'),str(c),'-o',str(path/'test')], check=True)
    subprocess.run([str(path/'test')],check=True)
