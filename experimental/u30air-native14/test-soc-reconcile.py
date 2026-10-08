"""Test real SOC policy and transaction code, including failed register writes."""
from pathlib import Path
import subprocess as sp
import tempfile
root=Path(__file__).resolve().parent
inc=(root/'source/fgu-soc-reconcile.inc').read_text()
rebase=inc[inc.index('static int sc27xx_fgu_rebase_soc'):inc.index('static void sc27xx_fgu_soc_work')]
code=r'''
#include <assert.h>
#include "fgu-soc-policy.h"
struct sc27xx_fgu_data { int init_cap, init_clbcnt; };
static int read_error, save_error, stored;
static int sc27xx_fgu_get_clbcnt(struct sc27xx_fgu_data *d,int *c) { (void)d; *c=12345; return read_error; }
static int sc27xx_fgu_save_last_cap(struct sc27xx_fgu_data *d,int c) { (void)d; if(!save_error)stored=c; return save_error; }
''' + rebase + r'''
int main(void) {
 assert(u30_charge_uah(76,4050,0)==3078000);
 assert(u30_charge_uah(93,4050,0)==3766500);
 assert(u30_charge_uah(93,4050,-12345)==3754155);
 assert(u30_charge_uah(0,4050,-100)==0);
 assert(u30_charge_uah(100,4050,100)==4050000);
 struct u30_soc_state s={0}; int cap=76;
 for(int i=0;i<12;i++) assert(u30_soc_tick(&s,1,4179000,93,cap,0)==76);
 for(int i=0;i<2;i++) assert(u30_soc_tick(&s,1,4179000,93,cap,0)==76);
 cap=u30_soc_tick(&s,1,4179000,93,cap,0); assert(cap==77);
 for(int i=0;i<100;i++) cap=u30_soc_tick(&s,1,4179000,93,cap,0);
 assert(cap==93);
 for(int i=0;i<100;i++) assert(u30_soc_tick(&s,1,4179000,93,cap,0)==93);
 assert(u30_soc_tick(&s,0,4179000,93,cap,0)==93 && s.stable==0);
 s=(struct u30_soc_state){0}; cap=76;
 for(int i=0;i<12;i++) u30_soc_tick(&s,1,4179000,93,cap,0);
 assert(u30_soc_tick(&s,1,4190000,95,cap,0)==76 && s.stable==1);
 s=(struct u30_soc_state){0}; cap=98;
 for(int i=0;i<100;i++) cap=u30_soc_tick(&s,1,4260000,100,cap,0);
 assert(cap==98); /* Unconfirmed full is never promoted to 100. */
 s=(struct u30_soc_state){0}; cap=98;
 for(int i=0;i<100;i++) cap=u30_soc_tick(&s,1,4260000,100,cap,1);
 assert(cap==100);
 struct sc27xx_fgu_data d={.init_cap=76,.init_clbcnt=99};
 read_error=-5; assert(sc27xx_fgu_rebase_soc(&d,77)==-5 && d.init_cap==76 && d.init_clbcnt==99);
 read_error=0; save_error=-5; assert(sc27xx_fgu_rebase_soc(&d,77)==-5 && d.init_cap==76 && d.init_clbcnt==99);
 save_error=0; assert(!sc27xx_fgu_rebase_soc(&d,77) && stored==77 && d.init_cap==77 && d.init_clbcnt==12345);
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp)/'soc.c'; p.write_text(code); exe=Path(tmp)/'soc'
 sp.run(['gcc','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I'+str(root/'source'),str(p),'-o',str(exe)],check=True)
 sp.run([str(exe)],check=True)
print('PASS: quiet dwell, 1%/30s, OCV target, voltage instability, invalid samples, guarded full and atomic persistent rebase')
