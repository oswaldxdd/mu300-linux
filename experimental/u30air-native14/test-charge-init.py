"""Execute real charger initialization: timer bits, ITERM and fail-stop."""
from pathlib import Path
import tempfile
import subprocess as sp

root=Path(__file__).resolve().parent
s=(root/'source/android-charge.inc').read_text()
init=s[s.index('static int sgm_android_init'):s.index('/* Read other supplies')]
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include "android-charge-policy.h"
typedef unsigned int u32;
#define BIT(n) (1U<<(n))
#define SGM_TIMER 5
#define SGM_CONTROL 1
#define SGM_CHARGE BIT(4)
#define min(a,b) ((a)<(b)?(a):(b))
struct device_node { int unused; };
struct client { struct { void *of_node; } dev; };
struct sgm41511_native {
 struct client *client; unsigned int stock_current,stock_voltage,stock_term;
 struct u30_charge_profile profiles[5]; bool rearm_pending; int charge_source;
};
static struct device_node bat;
static int missing,fail_reg=-1,fail_property;
static unsigned int regs[16];
static struct device_node *of_parse_phandle(void *n,const char *p,int i)
{ (void)n;(void)p;(void)i;return missing?NULL:&bat; }
static void of_node_put(struct device_node *n) { (void)n; }
static int of_property_read_u32(struct device_node *n,const char *p,unsigned int *v)
{
 (void)n; if(fail_property)return -EIO;
 if(!strcmp(p,"constant-charge-current-max-microamp"))*v=3000000;
 else if(!strcmp(p,"constant-charge-voltage-max-microvolt"))*v=4300000;
 else if(!strcmp(p,"charge-term-current-microamp"))*v=420000;
 else if(!strcmp(p,"fullbatt-current"))*v=120000;
 else return -EINVAL;
 return 0;
}
static int of_property_count_u32_elems(struct device_node *n,const char *p)
{ (void)n;(void)p;return 20; }
static int of_property_read_u32_array(struct device_node *n,const char *p,u32 *v,int len)
{
 const int temps[]={-50,100,200,450,500}; (void)n;(void)p;
 if(len==2){v[0]=1920000;v[1]=1500000;return 0;}
 for(int i=0;i<5;i++){v[4*i]=temps[i]+1000;v[4*i+1]=temps[i]+1000;
  v[4*i+2]=1920000;v[4*i+3]=4272000;}
 return 0;
}
static int sgm_read(struct sgm41511_native *d,int r,unsigned int *v)
{ (void)d;*v=regs[r];return 0; }
static int sgm_update(struct sgm41511_native *d,int r,unsigned int mask,unsigned int v)
{
 (void)d; if(r==fail_reg){fail_reg=-1;return -EIO;}
 regs[r]=(regs[r]&~mask)|(v&mask);return 0;
}
''' + init + r'''
int main(void) {
 struct client c={0};struct sgm41511_native d={.client=&c};
 for(unsigned int initial=0;initial<256;initial++){
  regs[5]=initial;regs[3]=0x66;regs[1]=0x10;
  assert(!sgm_android_init(&d));
  assert(regs[5]==(initial|0x88)); /* Preserve duration, watchdog and JEITA. */
  assert(regs[3]==0x61 && d.stock_term==120000 && d.rearm_pending);
  assert(regs[1]==0x10); /* Initialization does not cycle charge. */
 }
 for(int failure=0;failure<4;failure++){
  regs[1]=0x10;missing=fail_property=0;fail_reg=-1;
  if(failure==0)fail_reg=5;
  if(failure==1)fail_reg=3;
  if(failure==2)fail_property=1;
  if(failure==3)missing=1;
  assert(sgm_android_init(&d)<0);
  assert(!(regs[1]&0x10));
 }
}
'''
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp)/'init.c';p.write_text(code);exe=Path(tmp)/'init'
    sp.run(['gcc','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(root/'source'),str(p),'-o',str(exe)],check=True)
    sp.run([str(exe)],check=True)
print('PASS: real init, all timer field combinations, 120mA ITERM and setup fail-stop')
