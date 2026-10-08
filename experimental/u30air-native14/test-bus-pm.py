"""Exercise the actual charger bus acquisition and release against PM failures."""
from pathlib import Path
import subprocess as sp
import tempfile

root = Path(__file__).resolve().parent
s = (root / 'source/sgm41511-native.c').read_text()
release = s[s.index('static void sgm_bus_put'):s.index('static int sgm_probe')]
acquire = s[s.index('\tif (!client->adapter->dev.parent)'):s.index('\tsgm->client = client;')]
c = r'''
#include <assert.h>
#include <stddef.h>
#include <errno.h>
struct device { struct device *parent; };
struct adapter { struct device dev; };
struct i2c_client { struct adapter *adapter; struct device dev; };
static int usage, get_error, action_error;
static void (*cleanup)(void *);
static void *cleanup_data;
static int pm_runtime_resume_and_get(void *p) {
    assert(p); if (get_error) return get_error; usage++; return 0;
}
static void pm_runtime_put(void *p) { assert(p && usage == 1); usage--; }
static int dev_err_probe(void *p, int e, const char *s) { (void)p; (void)s; return e; }
static int devm_add_action_or_reset(void *d, void (*fn)(void *), void *p) {
    (void)d; if (action_error) { fn(p); return action_error; }
    cleanup=fn; cleanup_data=p; return 0;
}
''' + release + '\nstatic int acquire(struct i2c_client *client) { int ret;\n' + acquire + r'''
return 0;
}
int main(void) {
    struct device bus={0}; struct adapter a={.dev.parent=&bus};
    struct i2c_client c={.adapter=&a};
    a.dev.parent=NULL; assert(acquire(&c)==-ENODEV && usage==0);
    a.dev.parent=&bus; get_error=-EIO; assert(acquire(&c)==-EIO && usage==0);
    get_error=0; action_error=-ENOMEM; assert(acquire(&c)==-ENOMEM && usage==0);
    action_error=0; assert(acquire(&c)==0 && usage==1);
    /* The same devres action handles probe failure or driver removal. */
    cleanup(cleanup_data); assert(usage==0);
}
'''
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / 'bus.c'; p.write_text(c)
    exe = Path(tmp) / 'bus'
    sp.run(['gcc','-Wall','-Wextra','-Werror','-fsanitize=undefined',str(p),'-o',str(exe)],check=True)
    sp.run([str(exe)],check=True)
print('PASS: actual bus PM acquisition, missing parent, resume failure, action failure and balanced release')
