from pathlib import Path
import subprocess as sp
import tempfile
root=Path(__file__).resolve().parent
code=r'''
#include <assert.h>
#include "android-charge-policy.h"
int main(void) {
    struct u30_jeita_row rows[] = {
        {0,30,0,4100000},{50,80,400000,4300000},
        {100,130,1200000,4300000},{450,410,3000000,4300000},
        {500,470,2000000,4300000}
    };
    struct u30_jeita_state state={0};
    assert(u30_jeita_zone(rows,5,&state,343)==3);
    assert(rows[state.zone].charge_ua==3000000);
    assert(u30_jeita_zone(rows,5,&state,455)==4);
    assert(rows[state.zone].charge_ua==2000000);
    assert(u30_jeita_zone(rows,5,&state,500)==5);
    assert(u30_jeita_zone(rows,5,&state,475)==5);
    assert(u30_jeita_zone(rows,5,&state,469)==4);
    assert(u30_jeita_zone(rows,5,&state,0)==0);
    assert(u30_jeita_zone(rows,5,&state,35)==1);
    assert(u30_jeita_zone(rows,5,&state,55)==2);
    assert(u30_jeita_zone(rows,5,&state,85)==2);
    assert(u30_bc_source(0x886)==1);
    assert(u30_bc_source(0x846)==2);
    assert(u30_bc_source(0x826)==3);
    assert(u30_bc_source(0x884)==1);
    assert(u30_bc_source(0x804)==0);
    assert(u30_bc_source(0x846 & ~4)==0);
    assert(u30_bc_source(0x886 & ~0x800)==0);
    assert(u30_bc_source(0x8c4)==0);
    assert(u30_voltage_code(4300000)==13);
    assert(u30_voltage_code(4272000)==13);
    assert(u30_voltage_code(4340000)==14);
    assert(u30_voltage_code(4100000)==7);
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c=Path(tmp)/'test.c'; c.write_text(code)
    exe=Path(tmp)/'test'
    sp.run(['gcc','-Wall','-Wextra','-Werror','-fsanitize=undefined','-I'+str(root/'source'),str(c),'-o',str(exe)],check=True)
    sp.run([str(exe)],check=True)
print('PASS: stock JEITA thresholds/recovery, BC1.2 decoding, voltage rounding; UBSan')

implementation=(root/'source/android-charge.inc').read_text()
apply=implementation[implementation.index('static int sgm_android_apply'):implementation.index('static ssize_t charge_policy_show')]
mocks=r'''
#include <assert.h>
#include <stdbool.h>
#include "android-charge-policy.h"
#define min(a,b) ((a)<(b)?(a):(b))
#define GENMASK(a,b) (((1U<<((a)+1))-1) & ~((1U<<(b))-1))
#define SGM_CONTROL 1
#define SGM_CURRENT 2
#define SGM_VOLTAGE 4
#define SGM_STATUS 8
#define SGM_CHARGE 16
struct sgm41511_native {
    struct u30_charge_profile profiles[5]; struct u30_jeita_state jeita;
    unsigned int stock_current,stock_voltage; int charge_source,charge_temp;
    bool charge_allowed, rearm_pending; unsigned int regs[12]; int fail;
};
static int sgm_read(struct sgm41511_native *s,unsigned char r,unsigned int *v) {
    if (s->fail==r+1) return -5; *v=s->regs[r]; return 0;
}
static int sgm_update(struct sgm41511_native *s,unsigned char r,unsigned char mask,unsigned char value) {
    if (s->fail==r+1) return -5;
    s->regs[r]=(s->regs[r]&~mask)|(value&mask); return 0;
}
'''
cases=r'''
static struct sgm41511_native fresh(void) {
    struct sgm41511_native s={.stock_current=3000000,.stock_voltage=4300000,.charge_source=-1};
    struct u30_jeita_row rows[]={{0,30,0,4100000},{50,80,400000,4300000},
        {100,130,1200000,4300000},{450,410,3000000,4300000},{500,470,2000000,4300000}};
    for (int i=0;i<5;i++) { s.profiles[i].charge_ua=i==1?700000:3000000; s.profiles[i].input=3200000;
        for (int j=0;j<5;j++) s.profiles[i].rows[j]=rows[j]; }
    s.regs[0]=4; s.regs[1]=0x9a; s.regs[2]=0x8b; s.regs[4]=0x58; return s;
}
int main(void) {
    struct sgm41511_native s=fresh(); bool allowed,changed=false;
    struct u30_charge_sample sample={.source=1,.temp=343,.valid_temp=1};
    assert(!sgm_android_apply(&s,sample,true,0,&allowed,&changed));
    assert(allowed && (s.regs[0]&31)==4 && (s.regs[2]&63)==11);
    assert((s.regs[2]&0x80)==0x80 && (s.regs[4]>>3)==13);
    s=fresh(); sample.source=2;
    for (int i=0;i<15;i++) assert(!sgm_android_apply(&s,sample,true,0,&allowed,&changed));
    assert((s.regs[0]&31)==14 && (s.regs[2]&63)==50);
    sample.source=4; sample.contract=2000000;
    for (int i=0;i<10;i++) assert(!sgm_android_apply(&s,sample,true,0,&allowed,&changed));
    assert((s.regs[0]&31)==19);
    sample.contract=500000;
    assert(!sgm_android_apply(&s,sample,true,0,&allowed,&changed));
    assert((s.regs[0]&31)==4);
    sample.temp=455;
    assert(!sgm_android_apply(&s,sample,true,0,&allowed,&changed));
    assert(allowed && (s.regs[2]&63)==33);
    sample.temp=500;
    assert(!sgm_android_apply(&s,sample,true,0,&allowed,&changed));
    assert(!allowed && !(s.regs[1]&16) && !(s.regs[2]&63));
    s=fresh(); sample.valid_temp=0;
    assert(!sgm_android_apply(&s,sample,true,0,&allowed,&changed));
    assert(!allowed && !(s.regs[1]&16));
    s=fresh(); sample.valid_temp=1; sample.temp=343;
    assert(!sgm_android_apply(&s,sample,true,8,&allowed,&changed));
    assert(!allowed && !(s.regs[1]&16));
    s=fresh(); s.rearm_pending=true; s.regs[8]=24; s.regs[1]=16;
    assert(!sgm_android_apply(&s,sample,true,0,&allowed,&changed));
    assert(allowed && !s.rearm_pending && (s.regs[1]&16));
    s.regs[1]=0;
    assert(!sgm_android_apply(&s,sample,true,0,&allowed,&changed));
    assert(!(s.regs[1]&16)); /* Not repeatedly restarting a completed cycle. */
    s=fresh(); s.rearm_pending=true; sample.temp=500;
    assert(!sgm_android_apply(&s,sample,true,0,&allowed,&changed));
    assert(!allowed && s.rearm_pending && !(s.regs[1]&16));
    sample.temp=343;
    s=fresh(); s.fail=3;
    assert(sgm_android_apply(&s,sample,true,0,&allowed,&changed)==-5);
    assert(!allowed && !(s.regs[1]&16));
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c=Path(tmp)/'apply.c'; c.write_text(mocks+apply+cases); exe=Path(tmp)/'test'
    sp.run(['gcc','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-fsanitize=undefined','-I'+str(root/'source'),str(c),'-o',str(exe)],check=True)
    sp.run([str(exe)],check=True)
print('PASS: actual driver policy ramps, source limits, JEITA derating, sensor/fault/I2C fail-stop; UBSan')
