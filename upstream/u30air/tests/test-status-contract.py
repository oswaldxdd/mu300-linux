"""Compile and run the production FGU STATUS branch with small kernel stubs."""
from pathlib import Path
import subprocess
import tempfile

here = Path(__file__).resolve().parent
source_dir = here.parent / "fcc-learning" / "source"
source = (source_dir / "sc27xx_fuel_gauge.c").read_text()
getter_status = source.split("case POWER_SUPPLY_PROP_STATUS:", 1)[1].split(
    "case POWER_SUPPLY_PROP_HEALTH:", 1
)[0]
production_branch = "case POWER_SUPPLY_PROP_STATUS:" + getter_status

worker = (source_dir / "fgu-soc-reconcile.inc").read_text()
assert "power_supply_get_property(charger, POWER_SUPPLY_PROP_STATUS, &val)" in worker
assert "if (val.intval != POWER_SUPPLY_STATUS_FULL)" in worker
assert "valid && full_ready && data->soc_full_verified" in worker
assert "u30_unverified_full" not in worker

harness = r'''
#include <errno.h>
#include <stdio.h>
#include "fgu-soc-policy.h"

enum {
    POWER_SUPPLY_STATUS_UNKNOWN = 0,
    POWER_SUPPLY_STATUS_CHARGING = 1,
    POWER_SUPPLY_STATUS_DISCHARGING = 2,
    POWER_SUPPLY_STATUS_NOT_CHARGING = 3,
    POWER_SUPPLY_STATUS_FULL = 4,
    POWER_SUPPLY_PROP_STATUS = 10,
    POWER_SUPPLY_PROP_HEALTH = 11,
};
static int ump9620_info;
static int other_info;
struct sc27xx_fgu_data { void *var; int soc_full_verified; };
struct power_supply_propval { int intval; };
static int raw_status;
static int status_error;

static int sc27xx_fgu_get_status(struct sc27xx_fgu_data *data, int *value)
{
    (void)data;
    if (status_error)
        return status_error;
    *value = raw_status;
    return 0;
}

static int run_status(struct sc27xx_fgu_data *data,
                      struct power_supply_propval *val, int prop)
{
    int ret = 0;
    int value = POWER_SUPPLY_STATUS_UNKNOWN;
    switch (prop) {
        __PRODUCTION_STATUS_BRANCH__
    default:
        return -EINVAL;
    }
    return 0;
error:
    return ret;
}

static int check(const char *name, int raw, int verified, void *variant,
                 int error, int expected_ret, int expected_status)
{
    struct sc27xx_fgu_data data = { .var = variant,
                                    .soc_full_verified = verified };
    struct power_supply_propval val = { .intval = 12345 };
    int ret;

    raw_status = raw;
    status_error = error;
    ret = run_status(&data, &val, POWER_SUPPLY_PROP_STATUS);
    if (ret != expected_ret || val.intval != expected_status) {
        fprintf(stderr, "%s: ret=%d status=%d, expected ret=%d status=%d\n",
                name, ret, val.intval, expected_ret, expected_status);
        return 1;
    }
    printf("PASS: %s\n", name);
    return 0;
}

int main(void)
{
    int failures = 0;
    failures += check("UMP raw Full masked until protected Full",
                      POWER_SUPPLY_STATUS_FULL, 0, &ump9620_info, 0, 0,
                      POWER_SUPPLY_STATUS_NOT_CHARGING);
    failures += check("UMP verified Full is public Full",
                      POWER_SUPPLY_STATUS_FULL, 1, &ump9620_info, 0, 0,
                      POWER_SUPPLY_STATUS_FULL);
    failures += check("UMP non-Full status is preserved",
                      POWER_SUPPLY_STATUS_CHARGING, 0, &ump9620_info, 0, 0,
                      POWER_SUPPLY_STATUS_CHARGING);
    failures += check("status I/O error propagates without publishing value",
                      POWER_SUPPLY_STATUS_FULL, 0, &ump9620_info, -EIO, -EIO,
                      12345);
    failures += check("non-UMP Full status is unchanged",
                      POWER_SUPPLY_STATUS_FULL, 0, &other_info, 0, 0,
                      POWER_SUPPLY_STATUS_FULL);
    return failures ? 1 : 0;
}
'''.replace("__PRODUCTION_STATUS_BRANCH__", production_branch)

with tempfile.TemporaryDirectory(prefix="mu300-status-contract-") as tmp:
    c_file = Path(tmp) / "test_status_contract.c"
    binary = Path(tmp) / "test_status_contract"
    c_file.write_text(harness)
    subprocess.run([
        "gcc", "-std=c11", "-Wall", "-Wextra", "-Werror",
        "-I", str(source_dir), str(c_file), "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)

print("PASS: FCC worker retains raw charger Full for qualification")
